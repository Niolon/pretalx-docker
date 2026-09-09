"""Keep erasure ownership in the database; worker jobs contain only row IDs."""
from django.db import transaction
from django_scopes import scopes_disabled
from .adapters import replace_function
from .models import MailSubjects


def install_mail_privacy():
    from pretalx.mail.domain import render, queue, send
    from .mail_isolation import install_isolation, require_single_application
    install_isolation()
    from pretalx.mail import tasks
    from pretalx.mail.models import QueuedMail
    from pretalx.person.models import User
    from pretalx.submission.models import Submission

    original_render = render.render_to_mail
    def render_to_mail(**kwargs):
        context = kwargs.get("context_kwargs") or {}
        if context.get("submission") and context.get("slot") and context["submission"].pk != context["slot"].submission_id:
            from django.core.exceptions import ValidationError
            raise ValidationError("Message and interview must refer to the same application.")
        from .emails import _application_context
        selected = context.get("submission") or (context["slot"].submission if context.get("slot") else None)
        token = _application_context.set(selected.pk if selected else None)
        try:
            mail = original_render(**kwargs)
        finally:
            _application_context.reset(token)
        mail._privacy_users = [context["user"].pk] if context.get("user") else []
        submission = context.get("submission")
        if not submission and context.get("slot"):
            submission = context["slot"].submission
        mail._privacy_submissions = [submission.pk] if submission else []
        return mail
    replace_function(render, "render_to_mail", render_to_mail)

    original_save = queue.save_draft
    def save_draft(mail, **kwargs):
        with transaction.atomic(), scopes_disabled():
            sub_ids = set(getattr(mail, "_privacy_submissions", [])) | {s.pk for s in (kwargs.get("submissions") or [])}
            sub_ids.update(require_single_application(mail))
            if len(sub_ids) > 1:
                from django.core.exceptions import ValidationError
                raise ValidationError("Generate separate messages for each application.")
            user_ids = set(getattr(mail, "_privacy_users", [])) | {u.pk for u in (kwargs.get("to_users") or [])}
            user_ids.update(User.objects.filter(profiles__submissions__pk__in=sub_ids).values_list("pk", flat=True))
            users = list(User.objects.filter(pk__in=user_ids).order_by("pk").select_for_update())
            subs = list(Submission.all_objects.filter(pk__in=sub_ids).order_by("pk").select_for_update())
            if len(users) != len(user_ids) or len(subs) != len(sub_ids):
                raise ValueError("Email subject has been erased.")
            result = original_save(mail, **kwargs)
            ownership, _ = MailSubjects.objects.get_or_create(mail=mail)
            ownership.users.add(*users)
            ownership.submissions.add(*subs)
            return result
    replace_function(queue, "save_draft", save_draft)

    def send_transient(mail, *, force_global_backend=False):
        if not mail._state.adding:
            raise RuntimeError("send_transient requires an unsaved message")
        if force_global_backend:
            # System credentials must never appear in a call's organiser outbox.
            mail.event = None
            mail.template = None
        with scopes_disabled():
            save_draft(mail)
            send.send_draft(mail)
    replace_function(send, "send_transient", send_transient)

    original_copy = queue.copy_to_draft
    def copy_to_draft(mail):
        with transaction.atomic(), scopes_disabled():
            require_single_application(mail)
            new = original_copy(mail)
            ownership = MailSubjects.objects.filter(mail=mail).first()
            if ownership:
                copied = MailSubjects.objects.create(mail=new)
                copied.users.set(ownership.users.all())
                copied.submissions.set(ownership.submissions.all())
            return new
    replace_function(queue, "copy_to_draft", copy_to_draft)

    original_bulk = queue.bulk_create_drafts
    def bulk_create_drafts(template, recipients, *, progress=None):
        # Upstream groups identical content by recipient. Calling it per
        # application prevents two applications from collapsing into one row.
        entries = list(recipients)
        mails, failures = [], 0
        with transaction.atomic(), scopes_disabled():
            for index, entry in enumerate(entries):
                if progress:
                    progress(index + 1, len(entries))
                if not entry.get("submission_id") and not entry.get("slot_id"):
                    ids = list(Submission.all_objects.filter(event=template.event, speakers__user_id=entry["user_id"]).values_list("pk", flat=True))
                    expanded = [{**entry, "submission_id": pk} for pk in ids] or [entry]
                else:
                    expanded = [entry]
                for recipient in expanded:
                    created, errors = original_bulk(template, [recipient])
                    mails.extend(created)
                    failures += errors
        return mails, failures
    replace_function(queue, "bulk_create_drafts", bulk_create_drafts)

    original_task = tasks.task_send_draft.run
    def run(queued_mail_id):
        with scopes_disabled(), transaction.atomic():
            from django.db.models import Q
            ids = list(Submission.all_objects.filter(Q(mails__pk=queued_mail_id) | Q(mailsubjects__mail_id=queued_mail_id)).values_list("pk", flat=True))
            users = User.objects.filter(Q(profiles__submissions__pk__in=ids) | Q(mails__pk=queued_mail_id) | Q(mailsubjects__mail_id=queued_mail_id)).values("pk")
            list(User.objects.filter(pk__in=users).order_by("pk").select_for_update())
            list(Submission.all_objects.filter(pk__in=ids).order_by("pk").select_for_update())
            if not QueuedMail.objects.select_for_update().filter(pk=queued_mail_id).exists():
                return
            require_single_application(QueuedMail.objects.get(pk=queued_mail_id))
            return original_task(queued_mail_id)
    tasks.task_send_draft.run = run

    original_async = tasks.task_send_draft.apply_async
    def apply_async(*args, **kwargs):
        if transaction.get_connection().in_atomic_block and not tasks.task_send_draft.app.conf.task_always_eager:
            transaction.on_commit(lambda: original_async(*args, **kwargs))
            return None
        return original_async(*args, **kwargs)
    tasks.task_send_draft.apply_async = apply_async

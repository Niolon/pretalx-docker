"""Shared, owner-authorised live erasure for withdrawal and retention."""
from django.contrib import messages
from django.contrib.auth import logout
from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.db.models import Q
from django.http import Http404
from django.shortcuts import redirect, render
from django_scopes import scopes_disabled

from .models import ManualMail, MailSubjects


def erase_logs(objects):
    from pretalx.common.models import ActivityLog
    for obj in objects:
        ActivityLog.objects.filter(content_type=ContentType.objects.get_for_model(obj), object_id=obj.pk).delete()


def _erase_application(submission_id):
    from pretalx.submission.models import Submission, Answer
    from pretalx.person.models import User
    from pretalx.person.domain.user import shred_user
    from pretalx.mail.models import QueuedMail
    from pretalx.common.models import ActivityLog
    with scopes_disabled(), transaction.atomic():
        initial = Submission.all_objects.filter(pk=submission_id).first()
        if initial is None:
            return {"applications": 0, "accounts": 0, "messages": 0}
        users = list(User.objects.filter(profiles__submissions=initial).order_by("pk").distinct())
        # Lock users first, then the application, then its mail rows.
        users = list(User.objects.filter(pk__in=[u.pk for u in users]).order_by("pk").select_for_update())
        submission = Submission.all_objects.select_for_update().filter(pk=submission_id).first()
        if submission is None:
            return {"applications": 0, "accounts": 0, "messages": 0}
        mail_ids = QueuedMail.objects.filter(Q(submissions=submission) | Q(recruitment_subjects__submissions=submission)).values("pk")
        mails = list(QueuedMail.objects.filter(pk__in=mail_ids).order_by("pk").select_for_update())
        from .mail_isolation import require_single_application, require_manual_isolation
        for mail in mails:
            require_single_application(mail)
        answers = list(submission.answers.all()) + list(Answer.objects.filter(review__submission=submission))
        related = [submission, *answers, *submission.reviews.all(), *submission.slots.all(), *submission.resources.all(), *mails]
        erase_logs(related)
        for item in ManualMail.objects.filter(Q(source_mail__in=mails) | Q(subject_submissions=submission)).distinct():
            require_manual_isolation(item)
            item.delete()
        QueuedMail.objects.filter(pk__in=[m.pk for m in mails]).delete()
        schedules = list(submission.event.schedules.all())
        def clear_schedule_cache():
            from pretalx.schedule.domain.changes import invalidate_cached_schedule_changes
            for schedule in schedules:
                invalidate_cached_schedule_changes(schedule)
        transaction.on_commit(clear_schedule_cache)
        submission.slots.all().delete()
        for answer in answers:
            answer.delete(skip_log=True)
        for resource in submission.resources.all():
            resource.delete(skip_log=True)
        event_id = submission.event_id
        from pretalx.submission.models import ReviewScore
        scores = list(ReviewScore.objects.filter(reviews__submission=submission).values_list("pk", flat=True))
        submission.delete(skip_log=True)
        ReviewScore.objects.filter(pk__in=scores, reviews__isnull=True).delete()
        deleted_users = 0
        for user in users:
            # Profile data for other applications or an active panel role is retained.
            has_role = user.is_administrator or user.is_superuser or user.teams.exists() or user.reviews.exists() or user.assigned_reviews.exists()
            for profile in user.profiles.filter(event_id=event_id):
                if not profile.submissions.exists() and not has_role:
                    erase_logs([profile])
                    for answer in Answer.objects.filter(speaker=profile):
                        erase_logs([answer])
                        answer.delete(skip_log=True)
                    profile.delete(skip_log=True)
            if has_role or Submission.all_objects.filter(speakers__user=user).exists() or user.profiles.exists():
                continue
            # Explicit account ownership; never match an email address in body text.
            account_mails = list(QueuedMail.objects.filter(Q(recruitment_subjects__users=user) | Q(to_users=user)).filter(submissions__isnull=True, recruitment_subjects__submissions__isnull=True).distinct())
            for item in ManualMail.objects.filter(Q(source_mail__in=account_mails) | Q(subject_users=user)).filter(subject_submissions__isnull=True).distinct():
                if require_manual_isolation(item):
                    continue
                item.delete()
            erase_logs(account_mails)
            QueuedMail.objects.filter(pk__in=[m.pk for m in account_mails]).delete()
            ActivityLog.objects.filter(person=user).delete()
            shred_user(user)
            deleted_users += 1
        return {"applications": 1, "accounts": deleted_users, "messages": len(mails)}


def erase_application(submission_id):
    from .file_erasure import _erasing_event
    from pretalx.submission.models import Submission
    with scopes_disabled():
        event_id = Submission.all_objects.filter(pk=submission_id).values_list("event_id", flat=True).first()
    token = _erasing_event.set(event_id)
    try:
        return _erase_application(submission_id)
    finally:
        _erasing_event.reset(token)


def install_erasure():
    from pretalx.cfp.views.user import SubmissionsWithdrawView
    from pretalx.submission.models import Submission
    from .adapters import replace_function
    from pretalx.submission.domain import submission as domain

    def withdraw(view, request, *args, **kwargs):
        obj = view.get_object()
        if not request.user.is_active or not obj.speakers.filter(user=request.user).exists():
            raise Http404
        if request.POST.get("confirm_erasure") != "yes":
            messages.error(request, "Confirm permanent deletion before withdrawing.")
            return render(request, "recruitment/withdraw.html", {"submission": obj})
        event = obj.event
        erase_application(obj.pk)
        from pretalx.person.models import User
        if not User.objects.filter(pk=request.user.pk).exists():
            logout(request)
        from .models import PendingFileErasure
        if PendingFileErasure.objects.filter(event=event).exists():
            messages.warning(request, "Your application record was deleted, but private-file cleanup needs administrator attention. Backups and infrastructure logs expire under the hosting policy.")
        else:
            messages.success(request, "Your live application has been permanently deleted. Backups and infrastructure logs expire under the hosting policy.")
        return redirect("cfp:event.start", event=event.slug)

    SubmissionsWithdrawView.template_name = "recruitment/withdraw.html"
    SubmissionsWithdrawView.post = withdraw
    def model_withdraw(submission, person=None, orga=False):
        if not orga and (not person or not person.is_active or not submission.speakers.filter(user=person).exists()):
            raise Http404
        return erase_application(submission.pk)
    Submission.withdraw = model_withdraw
    original_transition = domain.set_submission_state
    def set_submission_state(submission, new_state, **kwargs):
        if new_state == "withdrawn":
            from pretalx.common.exceptions import SubmissionError
            raise SubmissionError("Use the permanent withdrawal confirmation page to erase an application; ordinary state changes cannot delete it.")
        return original_transition(submission, new_state, **kwargs)
    replace_function(domain, "set_submission_state", set_submission_state)
    original_pending = domain.set_pending_state
    def set_pending_state(submission, new_state):
        if new_state == "withdrawn":
            from pretalx.common.exceptions import SubmissionError
            raise SubmissionError("Use the permanent withdrawal confirmation page; erasure cannot be a pending state change.")
        return original_pending(submission, new_state)
    replace_function(domain, "set_pending_state", set_pending_state)
    # Organiser deletion also uses the same complete cleanup.
    def delete_submission(submission, **kwargs):
        return erase_application(submission.pk)
    replace_function(domain, "delete_submission", delete_submission)

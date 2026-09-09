"""Single-application correspondence, checked before capture, send and erasure."""
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models.signals import m2m_changed
from django_scopes import scopes_disabled


def application_ids(mail):
    from .models import MailSubjects
    if not mail.pk:
        return set()
    ids = set(mail.submissions.values_list("pk", flat=True))
    ownership = MailSubjects.objects.filter(mail=mail).first()
    if ownership:
        ids.update(ownership.submissions.values_list("pk", flat=True))
    return ids


def require_single_application(mail):
    ids = application_ids(mail)
    if len(ids) > 1:
        raise ValidationError("Recruitment correspondence must belong to at most one application. Generate separate messages.")
    return ids


def require_manual_isolation(item):
    ids = set(item.subject_submissions.values_list("pk", flat=True))
    if item.source_mail_id:
        ids.update(application_ids(item.source_mail))
    if len(ids) > 1:
        raise ValidationError("Manual correspondence has conflicting application ownership. No records were erased.")
    return ids


def install_isolation():
    from pretalx.mail.models import QueuedMail
    from .models import MailSubjects, ManualMail
    # A unique index on each through-table parent also enforces the maximum
    # against bulk_create and concurrent relation additions at database level.
    relations = [(QueuedMail, "submissions"), (MailSubjects, "submissions"), (ManualMail, "subject_submissions")]
    for model, field in relations:
        through = getattr(model, field).through
        def guard(sender, instance, action, reverse, pk_set, _model=model, **kwargs):
            if action != "pre_add" or not pk_set:
                return
            with scopes_disabled(), transaction.atomic():
                parents = _model.objects.filter(pk__in=pk_set).order_by("pk") if reverse else _model.objects.filter(pk=instance.pk)
                for candidate in parents:
                    # Use the same canonical mail lock for both ownership maps.
                    mail_id = candidate.pk if _model is QueuedMail else (candidate.mail_id if _model is MailSubjects else candidate.source_mail_id)
                    if mail_id:
                        QueuedMail.objects.select_for_update().get(pk=mail_id)
                    parent = _model.objects.select_for_update().get(pk=candidate.pk)
                    incoming = {instance.pk} if reverse else set(pk_set)
                    if isinstance(parent, QueuedMail):
                        existing = application_ids(parent)
                    elif isinstance(parent, MailSubjects):
                        existing = application_ids(parent.mail)
                    else:
                        existing = require_manual_isolation(parent)
                    if len(existing | incoming) > 1:
                        raise ValidationError("Recruitment correspondence must belong to at most one application.")
        m2m_changed.connect(guard, sender=through, weak=False, dispatch_uid=f"recruitment_mail_isolation_{model.__name__}")

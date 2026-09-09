import uuid

from django.conf import settings
from django.db import models

from .storage import PrivateUploadStorage


class ManualMail(models.Model):
    """A prepared email awaiting handover by an instance administrator."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created = models.DateTimeField(auto_now_add=True)
    subject = models.CharField(max_length=998)
    recipients = models.JSONField(default=list)
    cc = models.JSONField(default=list)
    bcc = models.JSONField(default=list)
    sender = models.TextField()
    body = models.TextField()
    message = models.FileField(
        upload_to="manual-mail/",
        storage=PrivateUploadStorage(file_permissions_mode=0o600, directory_permissions_mode=0o700),
        max_length=255,
    )
    subject_users = models.ManyToManyField(settings.AUTH_USER_MODEL, blank=True, related_name="recruitment_manual_mails")
    subject_submissions = models.ManyToManyField("submission.Submission", blank=True, related_name="recruitment_manual_mails")
    source_mail = models.ForeignKey("mail.QueuedMail", null=True, blank=True, on_delete=models.SET_NULL)
    requires_validation = models.BooleanField(default=False)
    handed_over_at = models.DateTimeField(null=True, blank=True)
    handed_over_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)

    class Meta:
        ordering = ["-created", "-pk"]


from django.db.models.signals import post_delete
from django.dispatch import receiver
from django.db import transaction


@receiver(post_delete, sender=ManualMail, dispatch_uid="manual_mail_file_cleanup")
def remove_message_file(sender, instance, **kwargs):
    if instance.message:
        storage, name = instance.message.storage, instance.message.name
        from .file_erasure import schedule_erasure
        if not schedule_erasure(name):
            transaction.on_commit(lambda: storage.delete(name))


class PolicyQuerySet(models.QuerySet):
    def update(self, **kwargs):
        if set(kwargs) & {"approved", "approval_reference", "approved_at", "approved_by", "approved_by_id"}:
            raise ValueError("Use record_approval or remove_approval with an instance administrator.")
        if set(kwargs) & {"lawful_basis", "retention_days", "hosting_policy"}:
            kwargs.update(approved=False, approval_reference="", approved_at=None, approved_by=None)
        return super().update(**kwargs)

    def bulk_create(self, *args, **kwargs):
        raise ValueError("Save recruitment policies individually to preserve approval integrity.")

    def bulk_update(self, *args, **kwargs):
        raise ValueError("Save recruitment policies individually to preserve approval integrity.")


class RecruitmentPolicy(models.Model):
    event = models.OneToOneField("event.Event", on_delete=models.CASCADE, related_name="recruitment_policy")
    lawful_basis = models.TextField(blank=True)
    retention_days = models.PositiveIntegerField(null=True, blank=True)
    hosting_policy = models.TextField(blank=True, help_text="Approved hosting, backup and infrastructure-log expiry information.")
    approved = models.BooleanField(default=False)
    approval_reference = models.CharField(max_length=500, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="recruitment_policy_approvals")
    completed_on = models.DateField(null=True, blank=True)
    objects = PolicyQuerySet.as_manager()
    policy_fields = ("lawful_basis", "retention_days", "hosting_policy")
    approval_fields = ("approved", "approval_reference", "approved_at", "approved_by_id")

    def save(self, *args, **kwargs):
        from django.core.exceptions import ValidationError
        with transaction.atomic():
            old = type(self).objects.select_for_update().filter(pk=self.pk).first() if self.pk else None
            fields = kwargs.get("update_fields")
            changed = old and any(getattr(old, k) != getattr(self, k) for k in self.policy_fields if fields is None or k in fields)
            if changed:
                self.approved = False
                self.approval_reference = ""
                self.approved_at = None
                self.approved_by = None
                if fields is not None:
                    kwargs["update_fields"] = set(fields) | set(self.approval_fields)
            elif not getattr(self, "_recording_approval", False):
                previous = old or type(self)()
                if any(getattr(previous, k) != getattr(self, k) for k in self.approval_fields):
                    raise ValidationError("Only an instance administrator can record or remove approval using the approval action.")
            return super().save(*args, **kwargs)

    def _set_approval(self, actor, reference=None):
        from django.core.exceptions import PermissionDenied, ValidationError
        from django.utils import timezone
        from pretalx.person.models import User
        with transaction.atomic():
            administrator = User.objects.select_for_update().filter(pk=getattr(actor, "pk", None), is_active=True, is_administrator=True).first()
            if not administrator:
                raise PermissionDenied("Only active instance administrators may record or remove approval.")
            current = type(self).objects.select_for_update().get(pk=self.pk)
            if reference is not None:
                reference = reference.strip()
                if not reference or len(reference) > 500 or not current.lawful_basis.strip() or not current.hosting_policy.strip() or not current.retention_days or current.retention_days < 1:
                    raise ValidationError("All policy values and an approval reference are required.")
            current.approved = reference is not None
            current.approval_reference = reference or ""
            current.approved_at = timezone.now() if reference is not None else None
            current.approved_by = administrator if reference is not None else None
            current._recording_approval = True
            current.save(update_fields=self.approval_fields)
            self.refresh_from_db()

    def record_approval(self, actor, reference):
        from django.core.exceptions import ValidationError
        if not isinstance(reference, str) or not reference.strip():
            raise ValidationError("An approval reference is required.")
        self._set_approval(actor, reference)

    def remove_approval(self, actor):
        self._set_approval(actor)


class MailSubjects(models.Model):
    """Explicit erasure ownership, separate from delivery recipients."""
    mail = models.OneToOneField("mail.QueuedMail", on_delete=models.CASCADE, related_name="recruitment_subjects")
    users = models.ManyToManyField(settings.AUTH_USER_MODEL, blank=True)
    submissions = models.ManyToManyField("submission.Submission", blank=True)


class PendingFileErasure(models.Model):
    """Durable retry record when post-commit private-file deletion fails."""
    event = models.ForeignKey("event.Event", on_delete=models.PROTECT)
    name = models.CharField(max_length=255, unique=True)

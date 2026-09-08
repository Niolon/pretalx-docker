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
        transaction.on_commit(lambda: storage.delete(name))

"""Private storage for answer documents and temporary uploads/exports."""

from pathlib import Path

from django.conf import settings
from django.core.checks import Error, Tags, register
from django.core.files.storage import FileSystemStorage
from django.db import transaction
from django.db.models.signals import pre_save
from django.urls import reverse
from django_scopes import scopes_disabled


class PrivateUploadStorage(FileSystemStorage):
    # Properties deliberately read settings dynamically (including test overrides).
    @property
    def location(self):
        return str((Path(settings.DATA_DIR) / "arc-private").resolve())

    @property
    def base_location(self):
        return self.location

    def url(self, name):
        return reverse(
            "plugins:pretalx_arc_application:download", kwargs={"name": name}
        )


class PrivateFormStorage(PrivateUploadStorage):
    @property
    def location(self):
        return str(Path(super().location) / "cfp_uploads")


def install_private_storage():
    """Attach storage through Django's field API, without changing upstream files.

    This applies instance-wide even when a call has not enabled the ARC form.
    Existing widgets, serializers and exports use FieldFile.url automatically.
    Cached files are opened by their originating UI views; this plugin never
    grants direct downloads of cached files.
    """
    from pretalx.cfp.flow.base import FormFlowStep
    from pretalx.common.models import CachedFile
    from pretalx.submission.models import Answer

    storage = PrivateUploadStorage(
        file_permissions_mode=0o600, directory_permissions_mode=0o700
    )
    Answer._meta.get_field("answer_file").storage = storage
    CachedFile._meta.get_field("file").storage = storage
    # Upstream's cleanup task deletes via default_storage (public MEDIA_ROOT),
    # which cannot delete files outside that directory. Adapt the model's
    # cleanup hook as well so replacements, expiry and deletions still work.
    for model in (Answer, CachedFile):
        model._schedule_file_cleanup = schedule_private_cleanup
        pre_save.connect(
            cleanup_cleared_file,
            sender=model,
            dispatch_uid=f"arc_private_clear_{model._meta.label_lower}",
        )
    FormFlowStep.file_storage = PrivateFormStorage(
        file_permissions_mode=0o600, directory_permissions_mode=0o700
    )


def schedule_private_cleanup(self, *, field, path):
    storage = self._meta.get_field(field).storage
    if not isinstance(storage, PrivateUploadStorage):
        from pretalx.common.models.mixins import FileCleanupMixin

        return FileCleanupMixin._schedule_file_cleanup(self, field=field, path=path)

    # Reject paths outside the private root, including symlink escapes.
    name = Path(path).resolve().relative_to(Path(storage.location)).as_posix()
    model = type(self)

    def remove_unreferenced_file():
        with scopes_disabled():
            if not model.objects.filter(**{field: name}).exists():
                storage.delete(name)

    transaction.on_commit(remove_unreferenced_file)


def cleanup_cleared_file(sender, instance, raw=False, update_fields=None, **kwargs):
    # The upstream replacement hook only covers non-empty replacement values.
    if raw or instance._state.adding:
        return
    for field in instance._file_fields:
        if (update_fields is not None and field not in update_fields) or getattr(
            instance, field
        ):
            continue
        with scopes_disabled():
            previous = sender.objects.filter(pk=instance.pk).first()
        if previous and (old_file := getattr(previous, field)):
            schedule_private_cleanup(instance, field=field, path=old_file.path)


@register(Tags.security)
def check_private_storage(app_configs, **kwargs):
    private = Path(PrivateUploadStorage().location)
    for public in (settings.MEDIA_ROOT, settings.STATIC_ROOT):
        if public and (
            private.is_relative_to(Path(public).resolve())
            or Path(public).resolve().is_relative_to(private)
        ):
            return [
                Error(
                    "ARC private storage must be outside MEDIA_ROOT and STATIC_ROOT.",
                    hint="Keep DATA_DIR/arc-private outside all proxy document roots.",
                    id="pretalx_arc_application.E001",
                )
            ]
    return []

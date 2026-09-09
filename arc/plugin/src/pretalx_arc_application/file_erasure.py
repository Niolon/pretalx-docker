from contextvars import ContextVar
from django.db import transaction
from django_scopes import scopes_disabled

_erasing_event = ContextVar("recruitment_erasing_event", default=None)


def schedule_erasure(name):
    event_id = _erasing_event.get()
    if event_id is None:
        return False
    from .models import PendingFileErasure
    record, _ = PendingFileErasure.objects.get_or_create(name=name, defaults={"event_id": event_id})
    transaction.on_commit(lambda: retry_file(record.pk))
    return True


def retry_file(pk):
    from .models import PendingFileErasure, ManualMail
    from .storage import PrivateUploadStorage
    from pretalx.submission.models import Answer
    from pretalx.common.models import CachedFile
    from pathlib import Path
    with scopes_disabled():
        record = PendingFileErasure.objects.filter(pk=pk).first()
        if not record:
            return True
        storage = PrivateUploadStorage()
        try:
            Path(storage.path(record.name)).resolve().relative_to(Path(storage.location).resolve())
            if not Answer.objects.filter(answer_file=record.name).exists() and not CachedFile.objects.filter(file=record.name).exists() and not ManualMail.objects.filter(message=record.name).exists():
                storage.delete(record.name)
            record.delete()
            return True
        except (OSError, ValueError):
            # Do not log document names or claim completion. Readiness and the
            # retention command report pending counts and can retry safely.
            return False


def retry_files(event):
    from .models import PendingFileErasure
    for pk in PendingFileErasure.objects.filter(event=event).values_list("pk", flat=True):
        retry_file(pk)
    return PendingFileErasure.objects.filter(event=event).count()

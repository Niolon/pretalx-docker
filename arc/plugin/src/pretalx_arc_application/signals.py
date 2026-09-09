from django.db.models.signals import m2m_changed, post_migrate, post_save
from django.dispatch import receiver
from pretalx.event.models import Event
from pretalx.person.models import SpeakerProfile
from pretalx.submission.models import SpeakerRole, Submission

from .schema import safe_feature_flags

PLUGIN_NAME = "pretalx_arc_application"


def _is_enabled(event):
    return PLUGIN_NAME in event.plugin_list


def _title(submission, speaker=None):
    position = str(submission.submission_type.name)
    return f"Application {submission.code} — {position}"[:1000]


def update_application_title(submission, speaker=None):
    if not _is_enabled(submission.event):
        return
    title = _title(submission, speaker=speaker)
    if submission.title != title:
        Submission.all_objects.filter(pk=submission.pk).update(title=title)
        submission.title = title


@receiver(post_save, sender=Submission, dispatch_uid="arc_application_title_save")
def application_saved(sender, instance, **kwargs):
    update_application_title(instance)


@receiver(
    m2m_changed,
    sender=Submission.speakers.through,
    dispatch_uid="arc_application_title_speaker_added",
)
def applicant_added(sender, instance, action, pk_set, **kwargs):
    if action == "post_add" and pk_set:
        speaker = SpeakerProfile.objects.filter(pk=next(iter(pk_set))).first()
        update_application_title(instance, speaker=speaker)


@receiver(
    post_save,
    sender=SpeakerProfile,
    dispatch_uid="arc_application_title_applicant_changed",
)
def applicant_changed(sender, instance, **kwargs):
    if not _is_enabled(instance.event):
        return
    for role in SpeakerRole.objects.filter(speaker=instance).select_related(
        "submission", "submission__event", "submission__submission_type"
    ):
        update_application_title(role.submission, speaker=instance)


@receiver(post_save, sender=Event, dispatch_uid="arc_application_privacy_lock")
def lock_event_privacy(sender, instance, **kwargs):
    """Keep public recruitment surfaces disabled across this ARC instance."""
    flags = safe_feature_flags(instance.feature_flags)
    if flags != instance.feature_flags:
        Event.objects.filter(pk=instance.pk).update(feature_flags=flags)
        instance.feature_flags = flags


@receiver(post_migrate, dispatch_uid="arc_application_existing_privacy_lock")
def lock_existing_event_privacy(sender, **kwargs):
    """Normalise calls created before the ARC image or plugin was installed."""
    if sender.label != "event":
        return
    for event in Event.objects.values("pk", "feature_flags"):
        flags = safe_feature_flags(event["feature_flags"])
        if flags != event["feature_flags"]:
            Event.objects.filter(pk=event["pk"]).update(feature_flags=flags)

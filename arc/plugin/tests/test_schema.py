import pytest
from django_scopes import scope
from pretalx.event.models import Event
from pretalx.submission.models import CfP, Question, Submission
from tests.factories import EventFactory, SpeakerFactory, SubmissionFactory

from pretalx_arc_application.schema import APPLICATION_QUESTIONS, configure_event
from pretalx_arc_application.signals import (
    lock_existing_event_privacy,
    update_application_title,
)

pytestmark = pytest.mark.django_db


def test_configure_event_creates_focused_private_schema():
    event = EventFactory()
    with scope(event=event):
        configure_event(event)
        event = Event.objects.get(pk=event.pk)
        cfp = CfP.objects.get(event=event)

        assert event.feature_flags["show_schedule"] is False
        assert event.feature_flags["show_featured"] == "never"
        assert event.feature_flags["submission_public_review"] is False
        assert cfp.fields["title"]["visibility"] == "do_not_ask"
        assert cfp.fields["biography"]["visibility"] == "do_not_ask"

        questions = Question.all_objects.filter(
            identifier__startswith="arc_"
        ).order_by("position")
        assert list(questions.values_list("identifier", flat=True)) == [
            question.identifier for question in APPLICATION_QUESTIONS
        ]
        assert not questions.filter(is_public=True).exists()
        assert not questions.filter(identifier="arc_adjustments").exists()

        configure_event(event)
        assert Question.all_objects.filter(identifier__startswith="arc_").count() == len(
            APPLICATION_QUESTIONS
        )


def test_generated_title_uses_code_and_position():
    event = EventFactory(plugins="pretalx_arc_application")
    with scope(event=event):
        speaker = SpeakerFactory(event=event, name="Ada Lovelace")
        submission = SubmissionFactory(event=event, title="")
        submission.speakers.add(speaker)

        update_application_title(submission, speaker=speaker)
        submission = Submission.all_objects.select_related("submission_type").get(
            pk=submission.pk
        )

        assert submission.title == f"Application {submission.code} — {submission.submission_type.name}"


def test_privacy_flags_are_locked_instance_wide():
    event = EventFactory()
    with scope(event=event):
        event.feature_flags = {
            **event.feature_flags,
            "show_schedule": True,
            "show_featured": "always",
            "submission_public_review": True,
        }
        event.save(update_fields=["feature_flags"])

        event = Event.objects.get(pk=event.pk)
        assert event.feature_flags["show_schedule"] is False
        assert event.feature_flags["show_featured"] == "never"
        assert event.feature_flags["submission_public_review"] is False


def test_existing_events_are_normalised_on_startup():
    event = EventFactory()
    Event.objects.filter(pk=event.pk).update(
        feature_flags={
            **event.feature_flags,
            "show_schedule": True,
            "show_featured": "pre_schedule",
            "submission_public_review": True,
        }
    )

    lock_existing_event_privacy(Event._meta.app_config)

    event = Event.objects.get(pk=event.pk)
    assert event.feature_flags["show_schedule"] is False
    assert event.feature_flags["show_featured"] == "never"
    assert event.feature_flags["submission_public_review"] is False

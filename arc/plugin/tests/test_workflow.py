from datetime import datetime, timedelta, timezone
from io import BytesIO
from zipfile import ZipFile

import pytest
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django_scopes import scope
from pretalx.common.exceptions import SendMailException
from pretalx.common.models import CachedFile
from pretalx.event.models import Event
from pretalx.mail.domain.send import send_draft
from pretalx.mail.domain.smtp import deliver_persisted
from pretalx.schedule.domain.release import freeze_schedule
from pretalx.submission.domain.submission import set_submission_state, send_initial_mails
from pretalx.submission.models import Submission, Answer
from tests.factories import EventFactory, QuestionFactory, RoomFactory, SpeakerFactory, SubmissionFactory, TalkSlotFactory, TeamFactory, UserFactory

from pretalx_arc_application.emails import validate_mail

pytestmark = pytest.mark.django_db


def test_decision_reversal_replaces_draft_and_blocks_stale_worker(mailoutbox):
    event = EventFactory(plugins="pretalx_arc_application")
    with scope(event=event):
        submission = SubmissionFactory(event=event)
        submission.speakers.add(SpeakerFactory(event=event))
        set_submission_state(submission, "accepted", orga=True)
        stale = event.queued_mails.get()
        # Simulate an already queued worker, which survives draft replacement.
        stale.state = "sending"
        stale.save()
        set_submission_state(submission, "rejected", orga=True)
        assert list(event.queued_mails.filter(state="draft").values_list("template__role", flat=True)) == ["submission.state.rejected"]
        with pytest.raises(SendMailException):
            deliver_persisted(stale)
        assert not mailoutbox
        rejection = event.queued_mails.get(state="draft")
        send_draft(rejection)
        assert len(mailoutbox) == 1
        assert "not be inviting you to interview" in mailoutbox[0].body


def test_reversal_removes_unsent_shortlist():
    event = EventFactory(plugins="pretalx_arc_application")
    with scope(event=event):
        submission = SubmissionFactory(event=event)
        submission.speakers.add(SpeakerFactory(event=event))
        set_submission_state(submission, "accepted", orga=True)
        set_submission_state(submission, "rejected", orga=True)
        assert event.queued_mails.count() == 1
        assert event.queued_mails.get().template.role == "submission.state.rejected"


def test_raw_state_mutation_is_rejected():
    event = EventFactory(plugins="pretalx_arc_application")
    with scope(event=event):
        submission = SubmissionFactory(event=event)
        with pytest.raises(ValueError, match="Bulk decision"):
            Submission.all_objects.filter(pk=submission.pk).update(state="accepted")
        submission.state = "accepted"
        with pytest.raises(ValueError, match="set_submission_state"):
            submission.save()
        submission.refresh_from_db()
        set_submission_state(submission, "accepted", orga=True)
        assert submission.slots.exists()


def test_receipt_has_no_answers_or_document_links(mailoutbox):
    event = EventFactory(plugins="pretalx_arc_application", name="Biology PhD")
    with scope(event=event):
        speaker = SpeakerFactory(event=event)
        submission = SubmissionFactory(event=event, abstract="PRIVATE APPLICATION TEXT")
        submission.speakers.add(speaker)
        question = QuestionFactory(event=event)
        Answer.objects.create(question=question, submission=submission, answer="PRIVATE ANSWER")
        send_initial_mails(submission, person=speaker.user)
        receipt = event.queued_mails.get(template__role="submission.new")
        assert "PRIVATE" not in receipt.text
        assert "/arc-files/" not in receipt.text
        assert "The Biology PhD recruitment team" in receipt.text
        assert "adjustment" not in receipt.text


def test_export_is_ephemeral_and_rejects_cached_ids(client, settings, tmp_path):
    settings.DATA_DIR = tmp_path
    event = EventFactory(plugins="pretalx_arc_application")
    user = UserFactory()
    team = TeamFactory(organiser=event.organiser, all_events=False)
    team.limit_events.add(event)
    team.members.add(user)
    client.force_login(user)
    with scope(event=event):
        question = QuestionFactory(event=event, variant="file")
        submission = SubmissionFactory(event=event)
        answer = Answer.objects.create(question=question, submission=submission)
        answer.answer_file.save("cv.pdf", ContentFile(b"PRIVATE PDF"))
        url = str(question.urls.download)
        response = client.get(url)
        assert response.status_code == 200
        body = b"".join(response.streaming_content)
        response.close()
        with ZipFile(BytesIO(body)) as archive:
            assert len(archive.namelist()) == 1
            assert archive.read(archive.namelist()[0]) == b"PRIVATE PDF"
        assert "no-store" in response["Cache-Control"]
        assert not CachedFile.objects.exists()
        foreign = CachedFile.objects.create(expires=datetime.now(timezone.utc)+timedelta(hours=1), filename="foreign.zip", content_type="application/zip")
        foreign.file.save("foreign.zip", ContentFile(b"FOREIGN"))
        assert client.get(url, {"cached_file": foreign.pk}).status_code == 404
        assert client.get(url, {"async_id": "foreign-task"}).status_code == 404


def test_interview_and_reschedule_have_full_private_details(mailoutbox):
    event = EventFactory(plugins="pretalx_arc_application", timezone="Europe/London")
    with scope(event=event):
        speaker = SpeakerFactory(event=event)
        submission = SubmissionFactory(event=event, state="accepted")
        submission.speakers.add(speaker)
        room = RoomFactory(event=event, name="Panel A", speaker_info="Join https://meet.example.test/panel-a")
        slot = TalkSlotFactory(submission=submission, room=room, start=datetime(2026,10,20,9,tzinfo=timezone.utc), end=datetime(2026,10,20,9,30,tzinfo=timezone.utc))
        released, wip = freeze_schedule(slot.schedule, "0.1", notify_speakers=True)
        invitation = event.queued_mails.get(template__role="schedule.new")
        assert "20 October 2026" in invitation.text
        assert "10:00–10:30 BST" in invitation.text
        assert "Europe/London" in invitation.text
        assert "https://meet.example.test/panel-a" in invitation.text
        assert "adjustments" in invitation.text
        assert "/me/submissions/" in invitation.text
        assert "/talk/" not in invitation.attachments[0]["content"]
        import vobject
        assert "https://meet.example.test/panel-a" in vobject.readOne(invitation.attachments[0]["content"]).vevent.description.value
        validate_mail(invitation)
        import vobject
        uid = vobject.readOne(invitation.attachments[0]["content"]).vevent.uid.value
        moved = wip.talks.get(submission=submission)
        moved.start += timedelta(hours=1)
        moved.end += timedelta(hours=1)
        moved.room = RoomFactory(event=event, speaker_info="Join https://meet.example.test/panel-b")
        moved.save()
        freeze_schedule(wip, "0.2", notify_speakers=True)
        with pytest.raises(SendMailException):
            validate_mail(invitation)
        replacement = event.queued_mails.filter(template__role="schedule.new").latest("pk")
        assert replacement.subject.startswith("Updated interview details")
        assert "11:00–11:30" in replacement.text
        assert "https://meet.example.test/panel-b" in replacement.text
        assert vobject.readOne(replacement.attachments[0]["content"]).vevent.uid.value == uid
        validate_mail(replacement)
        set_submission_state(submission, "confirmed", orga=False)
        replacement.refresh_from_db()
        send_draft(replacement)
        assert len(mailoutbox) == 1


def test_default_application_completes_and_uploads_survive_back_navigation(client, settings, tmp_path):
    from pretalx_arc_application.schema import configure_event
    from tests.cfp.views.conftest import start_wizard, get_response_and_url
    from django.utils.timezone import now
    settings.DATA_DIR = tmp_path
    event = EventFactory(plugins="pretalx_arc_application", cfp__deadline=now()+timedelta(days=30))
    user = UserFactory()
    client.force_login(user)
    with scope(event=event):
        configure_event(event)
        questions = {q.identifier:q for q in event.questions.all()}
    response, url = start_wizard(client, event)
    assert not response.context["form"].fields
    response, questions_url = get_response_and_url(client, url, data={})
    assert "/questions/" in questions_url
    data = {
        f"question_{questions['arc_cv'].pk}": SimpleUploadedFile("cv.pdf", b"%PDF-1.4 cv", content_type="application/pdf"),
        f"question_{questions['arc_cover_letter'].pk}": SimpleUploadedFile("letter.pdf", b"%PDF-1.4 letter", content_type="application/pdf"),
        f"question_{questions['arc_declaration'].pk}": "True",
    }
    response, profile_url = get_response_and_url(client, questions_url, data=data)
    assert "/profile/" in profile_url, response.context["form"].errors
    back = client.get(questions_url)
    form = back.context["form"]
    for name in data:
        if "file" in str(form[name]):
            assert ' required' not in str(form[name])
    response, profile_url = get_response_and_url(client, questions_url, data={f"question_{questions['arc_declaration'].pk}": "True"})
    assert "/profile/" in profile_url
    response, final_url = get_response_and_url(client, profile_url, data={"name":"Ada Applicant"})
    assert "/me/submissions/" in final_url, getattr(response.context.get("form"), "errors", None)
    with scope(event=event):
        submission = Submission.objects.get(event=event)
        assert submission.title.startswith("Ada Applicant —")
        assert submission.answers.filter(answer_file__gt="").count() == 2


def test_incomplete_interview_cannot_be_released_for_notification():
    event = EventFactory(plugins="pretalx_arc_application")
    with scope(event=event):
        submission = SubmissionFactory(event=event, state="accepted")
        submission.speakers.add(SpeakerFactory(event=event))
        slot = TalkSlotFactory(submission=submission, room=RoomFactory(event=event, speaker_info=""))
        with pytest.raises(SendMailException, match="joining instructions"):
            freeze_schedule(slot.schedule, "0.1", notify_speakers=True)
        slot.schedule.refresh_from_db()
        assert slot.schedule.version is None
        assert not event.queued_mails.exists()


def test_bulk_event_updates_cannot_publish_recruitment():
    event = EventFactory()
    Event.objects.filter(pk=event.pk).update(feature_flags={"show_schedule": True, "show_featured": "always"})
    event.refresh_from_db()
    assert event.feature_flags["show_schedule"] is False
    assert event.feature_flags["show_featured"] == "never"
    event.feature_flags["show_schedule"] = True
    assert event.get_feature_flag("show_schedule") is False


def test_private_interview_details_on_own_application_only(client):
    event = EventFactory(plugins="pretalx_arc_application")
    with scope(event=event):
        speaker = SpeakerFactory(event=event)
        submission = SubmissionFactory(event=event, state="accepted")
        submission.speakers.add(speaker)
        room = RoomFactory(event=event, speaker_info="Join https://meet.example.test/private-panel")
        slot = TalkSlotFactory(submission=submission, room=room)
        freeze_schedule(slot.schedule, "0.1", notify_speakers=True)
        client.force_login(speaker.user)
        response = client.get(str(submission.urls.user_base))
        assert response.status_code == 200
        assert b"https://meet.example.test/private-panel" in response.content
        response = client.get(str(submission.urls.confirm))
        assert b"https://meet.example.test/private-panel" in response.content
        client.force_login(UserFactory())
        response = client.get(str(submission.urls.user_base))
        assert b"https://meet.example.test/private-panel" not in response.content
        client.logout()
        response = client.get(str(submission.urls.user_base))
        assert b"https://meet.example.test/private-panel" not in response.content


def test_privacy_defaults_disable_update_metadata():
    from pretalx.common.models.settings import GlobalSettings
    assert GlobalSettings().settings.update_check_enabled is False


def test_existing_custom_email_copy_is_preserved():
    from pretalx.mail.domain.template import mail_template_by_role
    from pretalx_arc_application.schema import configure_event
    event = EventFactory(plugins="pretalx_arc_application")
    with scope(event=event):
        template = mail_template_by_role(event, "submission.state.rejected")
        template.text = {"en": "Our approved wording"}
        template.save()
        configure_event(event)
        assert str(mail_template_by_role(event, "submission.state.rejected").text) == "Our approved wording"


def test_export_rechecks_user_and_question_access(client, settings, tmp_path):
    settings.DATA_DIR = tmp_path
    event = EventFactory(plugins="pretalx_arc_application")
    other = EventFactory(plugins="pretalx_arc_application")
    user = UserFactory()
    team = TeamFactory(organiser=event.organiser, all_events=False)
    team.limit_events.add(event)
    team.members.add(user)
    with scope(event=other):
        question = QuestionFactory(event=other, variant="file")
        url = str(question.urls.download)
    client.force_login(user)
    assert client.get(url).status_code in {403, 404}
    with scope(event=event):
        own = QuestionFactory(event=event, variant="file")
        own_url = str(own.urls.download)
    response = client.get(own_url)
    assert response.status_code == 200
    response.close()
    team.members.remove(user)
    assert client.get(own_url).status_code in {403, 404}


def test_stale_single_mail_send_returns_actionable_ui_error(client, mailoutbox):
    from pretalx.mail.domain.queue import copy_to_draft
    event = EventFactory(plugins="pretalx_arc_application")
    user = UserFactory()
    team = TeamFactory(organiser=event.organiser, all_events=False)
    team.limit_events.add(event)
    team.members.add(user)
    client.force_login(user)
    with scope(event=event):
        submission = SubmissionFactory(event=event)
        submission.speakers.add(SpeakerFactory(event=event))
        set_submission_state(submission, "accepted", orga=True)
        shortlist = event.queued_mails.get()
        shortlist.state = "sent"
        shortlist.save()
        set_submission_state(submission, "rejected", orga=True)
        stale = copy_to_draft(shortlist)
        response = client.post(str(stale.urls.send), follow=True)
        assert response.status_code == 200
        assert b"no longer matches the application decision" in response.content
        assert not mailoutbox


def test_mail_defaults_work_before_form_plugin_is_enabled():
    event = EventFactory(plugins="")
    with scope(event=event):
        submission = SubmissionFactory(event=event, state="accepted")
        submission.speakers.add(SpeakerFactory(event=event))
        slot = TalkSlotFactory(submission=submission, room=RoomFactory(event=event, speaker_info="Join https://meet.example.test/panel"))
        freeze_schedule(slot.schedule, "0.1", notify_speakers=True)
        invitation = event.queued_mails.get(template__role="schedule.new")
        assert "https://meet.example.test/panel" in invitation.text
        assert "adjustments" in invitation.text
        validate_mail(invitation)

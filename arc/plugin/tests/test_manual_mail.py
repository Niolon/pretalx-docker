from email import policy
from email.parser import BytesParser
from unittest.mock import patch

import pytest
from django.core.mail import EmailMultiAlternatives
from django.test import Client
from django.urls import reverse
from django_scopes import scope
from pretalx.mail.domain.send import send_draft
from pretalx.mail.domain.smtp import deliver_payload
from pretalx.submission.domain.submission import set_submission_state
from tests.factories import EventFactory, SpeakerFactory, SubmissionFactory, TeamFactory, UserFactory

from pretalx_arc_application.manual_mail import ManualEmailBackend
from pretalx_arc_application.models import ManualMail

pytestmark = pytest.mark.django_db


@pytest.fixture
def manual(settings, tmp_path):
    settings.EMAIL_HOST = ""
    settings.DATA_DIR = tmp_path / "data"
    settings.MEDIA_ROOT = tmp_path / "public"
    settings.EMAIL_BACKEND = "pretalx_arc_application.manual_mail.ManualEmailBackend"
    return UserFactory(is_administrator=True)


def message():
    email = EmailMultiAlternatives("Reviewer invitation", "Private invitation: https://example.test/invite/token", "recruitment@example.test", ["reviewer@example.test"])
    email.attach_alternative('<img src="https://tracker.example.test/pixel"><script>alert(1)</script>', "text/html")
    email.attach("interview.ics", "BEGIN:VCALENDAR\r\nEND:VCALENDAR\r\n", "text/calendar")
    return email


def test_capture_preserves_message_and_attachments_without_network(manual, settings):
    with patch("smtplib.SMTP") as smtp:
        assert ManualEmailBackend().send_messages([message()]) == 1
        smtp.assert_not_called()
    item = ManualMail.objects.get()
    assert item.handed_over_at is None
    assert item.message.path.startswith(str(settings.DATA_DIR))
    assert not item.message.path.startswith(str(settings.MEDIA_ROOT))
    assert item.message.storage.open(item.message.name).read()
    assert "Private invitation" in item.body
    with item.message.open("rb") as source:
        parsed = BytesParser(policy=policy.default).parsebytes(source.read())
    assert parsed["To"] == "reviewer@example.test"
    assert [a.get_filename() for a in parsed.iter_attachments()] == ["interview.ics"]


def test_mailbox_and_download_require_active_instance_admin(client, manual):
    ManualEmailBackend().send_messages([message()])
    item = ManualMail.objects.get()
    urls = [
        reverse("plugins:pretalx_arc_application:manual_mail"),
        reverse("plugins:pretalx_arc_application:manual_mail_detail", kwargs={"message_id":item.pk}),
        reverse("plugins:pretalx_arc_application:manual_mail_download", kwargs={"message_id":item.pk}),
    ]
    for user in [None, UserFactory(), UserFactory(is_administrator=True, is_active=False)]:
        client.logout()
        if user:
            client.force_login(user)
        for url in urls:
            response = client.get(url)
            assert response.status_code in {302, 403, 404}
            if response.status_code == 302:
                assert "/orga/login/" in response["Location"]
    event = EventFactory()
    organiser = UserFactory()
    team = TeamFactory(organiser=event.organiser)
    team.members.add(organiser)
    client.force_login(organiser)
    for url in urls:
        assert client.get(url).status_code == 404
    client.force_login(manual)
    for url in urls:
        response = client.get(url)
        assert response.status_code == 200
        assert "no-store" in response["Cache-Control"]
        if getattr(response, "streaming", False):
            list(response.streaming_content)  # Django test-client iterator closes the response safely.
    response = client.get(urls[1])
    assert b"tracker.example.test" not in response.content
    assert b"<script>alert" not in response.content
    assert b"Awaiting manual delivery" in response.content


def test_smtp_keeps_existing_mailbox_accessible(client, manual, settings):
    settings.EMAIL_HOST = "smtp.example.test"
    client.force_login(manual)
    assert client.get(reverse("plugins:pretalx_arc_application:manual_mail")).status_code == 200


def test_missing_custom_smtp_uses_manual_delivery(manual):
    event = EventFactory(mail_settings={"smtp_use_custom":True, "smtp_host":""})
    with patch("smtplib.SMTP") as smtp, patch("smtplib.SMTP_SSL") as ssl:
        deliver_payload(event=event, to=["recipient@example.test"], subject="Test", body="Content", html="<p>Content</p>")
        smtp.assert_not_called()
        ssl.assert_not_called()
    assert ManualMail.objects.count() == 1


def test_handover_is_explicit_post_and_csrf_protected(manual):
    ManualEmailBackend().send_messages([message()])
    item = ManualMail.objects.get()
    client = Client(enforce_csrf_checks=True)
    client.force_login(manual)
    url = reverse("plugins:pretalx_arc_application:manual_mail_detail", kwargs={"message_id":item.pk})
    client.get(url)
    item.refresh_from_db()
    assert item.handed_over_at is None
    assert client.post(url, {"action":"handed_over"}).status_code == 403
    token = client.cookies["pretalx_csrftoken"].value if "pretalx_csrftoken" in client.cookies else client.cookies["csrftoken"].value
    response = client.post(url, {"action":"handed_over", "csrfmiddlewaretoken":token})
    assert response.status_code == 302
    item.refresh_from_db()
    assert item.handed_over_at is not None
    assert item.handed_over_by == manual


def test_old_decision_cannot_be_collected_after_reversal(client, manual):
    event = EventFactory(plugins="pretalx_arc_application")
    with scope(event=event):
        submission = SubmissionFactory(event=event)
        submission.speakers.add(SpeakerFactory(event=event))
        set_submission_state(submission, "accepted", orga=True)
        send_draft(event.queued_mails.get())
        item = ManualMail.objects.get()
        assert item.source_mail_id
        set_submission_state(submission, "rejected", orga=True)
    client.force_login(manual)
    url = reverse("plugins:pretalx_arc_application:manual_mail_download", kwargs={"message_id":item.pk})
    assert client.get(url).status_code == 404
    detail = reverse("plugins:pretalx_arc_application:manual_mail_detail", kwargs={"message_id":item.pk})
    response = client.get(detail)
    assert b"no longer matches the application decision" in response.content
    assert b"Download email" not in response.content


def test_system_email_and_custom_smtp_test_use_manual_backend(manual):
    from pretalx.mail.domain.send import send_system_mail
    from pretalx.mail.domain.smtp import mail_backend_for_event
    event = EventFactory(mail_settings={"smtp_use_custom":True, "smtp_host":""})
    assert isinstance(mail_backend_for_event(event, force_custom=True), ManualEmailBackend)
    with patch("smtplib.SMTP") as smtp:
        send_system_mail(subject="Account access", text="Private reset link", to="person@example.com")
        smtp.assert_not_called()
    assert ManualMail.objects.get().body.strip() == "Private reset link"


def test_reviewer_team_invitation_is_captured(manual):
    from pretalx.event.domain.team import create_team_invite, send_team_invite

    team = TeamFactory(is_reviewer=True)
    with patch("smtplib.SMTP") as smtp, patch("smtplib.SMTP_SSL") as ssl:
        invite = create_team_invite(team=team, email="reviewer@example.test")
        item = ManualMail.objects.get()
        assert item.recipients == [invite.email]
        assert invite.invitation_url in item.body
        assert str(team.name) in item.body
        assert item.source_mail_id is not None
        assert item.source_mail.event_id is None
        send_team_invite(invite)
        assert ManualMail.objects.count() == 2
        smtp.assert_not_called()
        ssl.assert_not_called()


def test_headers_are_separate_and_raw_file_is_not_public(client, manual):
    email = message()
    email.cc = ["cc@example.test"]
    email.bcc = ["bcc@example.test"]
    ManualEmailBackend().send_messages([email])
    item = ManualMail.objects.get()
    assert item.recipients == ["reviewer@example.test"]
    assert item.cc == email.cc
    assert item.bcc == email.bcc
    client.force_login(manual)
    assert client.get(item.message.url).status_code == 404


def test_deleting_record_cleans_private_message(manual, django_capture_on_commit_callbacks):
    ManualEmailBackend().send_messages([message()])
    item = ManualMail.objects.get()
    storage, name = item.message.storage, item.message.name
    with django_capture_on_commit_callbacks(execute=True):
        item.delete()
    assert not storage.exists(name)


def test_configured_server_smtp_is_used_without_capture(manual, settings):
    from pretalx.mail.domain.send import send_system_mail
    settings.EMAIL_HOST = "smtp.example.test"
    settings.EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
    with patch("django.core.mail.backends.smtp.EmailBackend.send_messages", return_value=1) as send:
        send_system_mail(subject="Account access", text="Private link", to="person@institution.test")
    send.assert_called_once()
    assert ManualMail.objects.count() == 0


def test_configured_call_smtp_is_used_without_capture(manual):
    event = EventFactory(mail_settings={"smtp_use_custom": True, "smtp_host": "smtp.example.test"})
    with patch("pretalx.mail.smtp.CustomSMTPBackend.send_messages", return_value=1) as send:
        deliver_payload(event=event, to=["person@institution.test"], subject="Invitation", body="Content", html=None)
    send.assert_called_once()
    assert ManualMail.objects.count() == 0


def test_smtp_failure_does_not_fall_back_to_manual(manual, settings):
    settings.EMAIL_HOST = "smtp.example.test"
    settings.EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
    with patch("django.core.mail.backends.smtp.EmailBackend.send_messages", side_effect=OSError("SMTP unavailable")):
        with pytest.raises(OSError):
            deliver_payload(event=None, to=["person@institution.test"], subject="Invitation", body="Content", html=None)
    assert ManualMail.objects.count() == 0


def test_call_missing_custom_host_is_manual_even_with_global_smtp(manual, settings):
    settings.EMAIL_HOST = "smtp.example.test"
    settings.EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
    event = EventFactory(mail_settings={"smtp_use_custom": True, "smtp_host": ""})
    with patch("smtplib.SMTP") as smtp:
        deliver_payload(event=event, to=["person@example.com"], subject="Invitation", body="Content", html=None)
    smtp.assert_not_called()
    assert ManualMail.objects.count() == 1

from datetime import timedelta
from io import StringIO
from unittest.mock import patch

import pytest
from django.core.files.base import ContentFile
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import transaction
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from django_scopes import scope, scopes_disabled
from pretalx.submission.models import Answer, Submission
from pretalx.mail.models import QueuedMail
from pretalx.person.models import User
from tests.factories import EventFactory, SpeakerFactory, SubmissionFactory, UserFactory, TeamFactory, ReviewFactory, QuestionFactory, TalkSlotFactory
from pretalx_arc_application.models import RecruitmentPolicy, ManualMail, MailSubjects
from pretalx_arc_application.schema import configure_event, ACKNOWLEDGEMENT
from pretalx_arc_application.privacy import intake_open, PolicyForm, notice_url
from pretalx_arc_application.erasure import erase_application

pytestmark = pytest.mark.django_db


@pytest.fixture
def call(settings, tmp_path):
    settings.DATA_DIR = tmp_path / "data"
    settings.MEDIA_ROOT = tmp_path / "public"
    settings.DEBUG = False
    event = EventFactory(plugins="pretalx_arc_application")
    with scope(event=event):
        configure_event(event)
    with scopes_disabled():
        yield event


def approve(event):
    return RecruitmentPolicy.objects.create(event=event, lawful_basis="SYNTHETIC TEST BASIS", hosting_policy="SYNTHETIC TEST HOSTING POLICY", retention_days=30, approved=True)


def application(event, user=None):
    with scope(event=event):
        speaker = SpeakerFactory(event=event, **({"user": user} if user else {}))
        submission = SubmissionFactory(event=event)
        submission.speakers.add(speaker)
        answer = Answer.objects.create(question=event.questions.get(identifier="arc_cv"), submission=submission)
        answer.answer_file.save("cv.pdf", ContentFile(b"%PDF synthetic"))
    return submission, speaker.user, answer


def test_gate_requires_complete_approved_policy_and_blocks_completed_call(call, client):
    assert not intake_open(call)
    assert not call.cfp.is_open
    assert client.get(notice_url(call)).status_code == 200
    policy = approve(call)
    assert intake_open(call)
    policy.completed_on = timezone.localdate()
    policy.save()
    assert not intake_open(call)
    url = reverse("cfp:event.submit", kwargs={"event": call.slug, "step": "info", "tmpid": "ABC123"})
    assert client.post(url + "?access_code=anything", {}).status_code == 302
    assert Submission.all_objects.filter(event=call).count() == 0


def test_policy_form_rejects_false_approval_and_future_completion():
    assert not PolicyForm({"approved": True}).is_valid()
    assert not PolicyForm({"completed_on": timezone.localdate() + timedelta(days=1)}).is_valid()


def test_notice_and_landing_are_public_but_settings_are_protected(call, client):
    response = client.get(notice_url(call))
    assert b"information.governance@durham.ac.uk" in response.content
    assert b"Draft notice" in response.content
    assert b"not consent" in response.content
    response = client.get(call.urls.base)
    assert notice_url(call).encode() in response.content
    url = reverse("plugins:pretalx_arc_application:privacy_settings", kwargs={"event": call.slug})
    client.force_login(UserFactory())
    assert client.get(url).status_code == 404
    admin = UserFactory(is_administrator=True)
    client.force_login(admin)
    assert client.get(url).status_code == 200


@pytest.mark.parametrize("state", ["submitted", "accepted", "confirmed", "rejected"])
def test_erasure_removes_files_reviews_slots_and_mail(call, state, settings, django_capture_on_commit_callbacks):
    from pretalx.mail.domain.send import send_draft
    from pretalx.submission.domain.submission import set_submission_state
    settings.EMAIL_HOST = ""
    settings.EMAIL_BACKEND = "pretalx_arc_application.manual_mail.ManualEmailBackend"
    with scope(event=call):
        submission, user, answer = application(call)
        storage, name = answer.answer_file.storage, answer.answer_file.name
        ReviewFactory(submission=submission)
        TalkSlotFactory(submission=submission, schedule=call.wip_schedule)
        if state != "submitted":
            set_submission_state(submission, state, orga=True)
            for mail in list(submission.mails.filter(state="draft")):
                send_draft(mail)
        pk, user_id = submission.pk, user.pk
        with django_capture_on_commit_callbacks(execute=True):
            result = erase_application(pk)
        assert result["applications"] == 1
        assert not Submission.all_objects.filter(pk=pk).exists()
        assert not storage.exists(name)
        assert not User.objects.filter(pk=user_id).exists()
        assert not ManualMail.objects.exists()
        assert not QueuedMail.objects.filter(event=call).exists()
        assert erase_application(pk)["applications"] == 0


def test_erasure_preserves_other_call_and_shared_account(call):
    other = EventFactory(plugins="pretalx_arc_application")
    with scope(event=other):
        configure_event(other)
    first, user, _ = application(call)
    second, _, answer = application(other, user)
    erase_application(first.pk)
    assert User.objects.filter(pk=user.pk).exists()
    assert Submission.all_objects.filter(pk=second.pk).exists()
    assert answer.answer_file.storage.exists(answer.answer_file.name)


def test_erasure_preserves_reviewer_account(call):
    submission, user, _ = application(call)
    team = TeamFactory(organiser=call.organiser, is_reviewer=True)
    team.members.add(user)
    erase_application(submission.pk)
    assert User.objects.filter(pk=user.pk).exists()
    assert team.members.filter(pk=user.pk).exists()


def test_withdrawal_requires_owner_post_confirmation_and_csrf(call, client):
    submission, user, _ = application(call)
    url = str(submission.urls.withdraw)
    client.force_login(UserFactory())
    assert client.post(url, {"confirm_erasure": "yes"}).status_code in {403, 404}
    client.force_login(user)
    assert client.get(url).status_code == 200
    assert client.post(url, {}).status_code == 200
    assert Submission.all_objects.filter(pk=submission.pk).exists()
    csrf_client = Client(enforce_csrf_checks=True)
    csrf_client.force_login(user)
    assert csrf_client.post(url, {"confirm_erasure": "yes"}).status_code == 403
    assert client.post(url, {"confirm_erasure": "yes"}).status_code == 302
    assert not Submission.all_objects.filter(pk=submission.pk).exists()


def test_erasure_rollback_keeps_records_and_files(call):
    submission, user, answer = application(call)
    with pytest.raises(RuntimeError), transaction.atomic():
        erase_application(submission.pk)
        raise RuntimeError("rollback")
    assert Submission.all_objects.filter(pk=submission.pk).exists()
    assert User.objects.filter(pk=user.pk).exists()
    assert answer.answer_file.storage.exists(answer.answer_file.name)


def test_retention_boundary_dry_run_and_repeated_execute(call, django_capture_on_commit_callbacks):
    policy = approve(call)
    policy.completed_on = timezone.localdate() - timedelta(days=30)
    policy.save()
    submission, _, answer = application(call)
    out = StringIO()
    call_command("arc_retention_sweep", event=call.slug, before=timezone.localdate(), dry_run=True, stdout=out)
    assert "0 applications eligible" in out.getvalue()
    policy.completed_on -= timedelta(days=1)
    policy.save()
    call_command("arc_retention_sweep", event=call.slug, before=timezone.localdate(), dry_run=True, stdout=out)
    assert Submission.all_objects.filter(pk=submission.pk).exists()
    with django_capture_on_commit_callbacks(execute=True):
        call_command("arc_retention_sweep", event=call.slug, before=timezone.localdate(), execute=True, stdout=out)
    assert not Submission.all_objects.filter(pk=submission.pk).exists()
    assert not answer.answer_file.storage.exists(answer.answer_file.name)
    call_command("arc_retention_sweep", event=call.slug, before=timezone.localdate(), execute=True, stdout=out)
    assert "applications=0" in out.getvalue()


def test_retention_refuses_unapproved_active_and_future_cutoffs(call):
    for _ in range(2):
        with pytest.raises(CommandError):
            call_command("arc_retention_sweep", event=call.slug, before=timezone.localdate(), execute=True)
        if not RecruitmentPolicy.objects.filter(event=call).exists():
            approve(call)
    with pytest.raises(CommandError):
        call_command("arc_retention_sweep", event=call.slug, before=timezone.localdate()+timedelta(days=1), execute=True)


def test_strict_readiness_passes_synthetic_policy_and_detects_changes(call):
    out = StringIO()
    with pytest.raises(CommandError):
        call_command("arc_privacy_check", event=call.slug, strict=True, stdout=out)
    approve(call)
    try:
        call_command("arc_privacy_check", event=call.slug, strict=True, stdout=out)
    except CommandError:
        pytest.fail(out.getvalue())
    assert "PASS:" in out.getvalue()
    with scope(event=call):
        call.questions.filter(identifier="arc_declaration").update(question="Consent")
    with pytest.raises(CommandError):
        call_command("arc_privacy_check", event=call.slug, strict=True, stdout=out)


def test_queued_retry_after_erasure_does_not_send_or_recreate(call, settings):
    from pretalx.mail.domain.render import render_to_mail
    from pretalx.mail.domain.queue import save_draft
    from pretalx.mail.tasks import task_send_draft
    from django.utils.safestring import mark_safe
    submission, user, _ = application(call)
    with scopes_disabled():
        mail = render_to_mail(event=call, subject_template="Internal", text_template="Private content", context_kwargs={"submission": submission, "user": user})
        save_draft(mail, to="organiser@institution.test")
        pk = mail.pk
    erase_application(submission.pk)
    with patch("smtplib.SMTP") as smtp:
        task_send_draft.run(pk)
    smtp.assert_not_called()
    with scopes_disabled():
        assert not QueuedMail.objects.filter(pk=pk).exists()


def test_failed_file_cleanup_is_reported_and_retryable(call, django_capture_on_commit_callbacks):
    from pretalx_arc_application.storage import PrivateUploadStorage
    from pretalx_arc_application.models import PendingFileErasure
    submission, _, answer = application(call)
    storage, name = answer.answer_file.storage, answer.answer_file.name
    with patch.object(PrivateUploadStorage, "delete", side_effect=PermissionError("storage unavailable")):
        with django_capture_on_commit_callbacks(execute=True):
            erase_application(submission.pk)
    assert storage.exists(name)
    assert PendingFileErasure.objects.count() == 1
    with pytest.raises(CommandError):
        call_command("arc_privacy_check", event=call.slug, strict=True, stdout=StringIO())
    call_command("arc_retention_sweep", event=call.slug, retry_file_deletions=True, execute=True, stdout=StringIO())
    assert not storage.exists(name)
    assert not PendingFileErasure.objects.exists()


def test_internal_and_account_mail_have_explicit_ownership(call, settings):
    from pretalx.mail.domain.send import send_system_mail
    from pretalx.submission.domain.submission import send_initial_mails
    settings.EMAIL_HOST = ""
    settings.EMAIL_BACKEND = "pretalx_arc_application.manual_mail.ManualEmailBackend"
    call.mail_settings = {**call.mail_settings, "mail_on_new_submission": True}
    call.save()
    submission, user, _ = application(call)
    send_initial_mails(submission, person=user)
    send_system_mail(subject="Account", text="Private reset link", to=user.email, context_kwargs={"user": user})
    assert MailSubjects.objects.filter(submissions=submission).count() == 2
    assert ManualMail.objects.count() == 3
    erase_application(submission.pk)
    assert not ManualMail.objects.exists()
    assert not QueuedMail.objects.exists()


def test_shared_mail_is_erased_in_full_without_erasing_other_application(call):
    from pretalx.mail.domain.queue import save_draft
    from pretalx.mail.domain.render import render_to_mail
    first, _, _ = application(call)
    second, _, _ = application(call)
    mail = render_to_mail(event=call, subject_template="Panel summary", text_template="Shared private information")
    save_draft(mail, to="organiser@institution.test")
    ownership = MailSubjects.objects.get(mail=mail)
    ownership.submissions.add(first, second)
    result = erase_application(first.pk)
    assert result["shared_messages"] == 1
    assert not QueuedMail.objects.filter(pk=mail.pk).exists()
    assert Submission.all_objects.filter(pk=second.pk).exists()


def test_reviewer_defaults_and_additive_permissions_are_checked(call):
    from pretalx_arc_application.views import can_download_answer
    approve(call)
    first, _, answer = application(call)
    second, _, second_answer = application(call)
    reviewer = UserFactory()
    team = TeamFactory(organiser=call.organiser, is_reviewer=True, can_change_submissions=False, can_change_event_settings=False, can_change_teams=False, can_create_events=False, can_change_organiser_settings=False)
    team.limit_events.add(call)
    team.members.add(reviewer)
    first.assigned_reviewers.add(reviewer)
    assert can_download_answer(reviewer, answer)
    assert not can_download_answer(reviewer, second_answer)
    call_command("arc_privacy_check", event=call.slug, strict=True, stdout=StringIO())
    team.can_change_teams = True
    team.save()
    with pytest.raises(CommandError):
        call_command("arc_privacy_check", event=call.slug, strict=True, stdout=StringIO())


def test_manual_copy_remains_erasable_after_source_mail_deletion(call, settings, django_capture_on_commit_callbacks):
    from pretalx.submission.domain.submission import send_initial_mails
    settings.EMAIL_HOST = ""
    settings.EMAIL_BACKEND = "pretalx_arc_application.manual_mail.ManualEmailBackend"
    submission, user, _ = application(call)
    send_initial_mails(submission, person=user)
    item = ManualMail.objects.get()
    storage, name = item.message.storage, item.message.name
    item.source_mail.delete()
    item.refresh_from_db()
    assert item.source_mail_id is None
    assert item.subject_submissions.filter(pk=submission.pk).exists()
    with django_capture_on_commit_callbacks(execute=True):
        erase_application(submission.pk)
    assert not ManualMail.objects.exists()
    assert not storage.exists(name)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("first", ["delivery", "erasure"])
def test_postgresql_delivery_and_erasure_serialize(call, settings, first):
    """Exercise real row locks; SQLite cannot establish this guarantee."""
    import threading
    from django.db import connection, close_old_connections
    from pretalx.mail.domain.render import render_to_mail
    from pretalx.mail.domain.queue import save_draft
    from pretalx.mail.tasks import task_send_draft
    from pretalx_arc_application.manual_mail import ManualEmailBackend
    if connection.vendor != "postgresql":
        pytest.skip("PostgreSQL row-lock regression")
    settings.EMAIL_HOST = ""
    settings.EMAIL_BACKEND = "pretalx_arc_application.manual_mail.ManualEmailBackend"
    submission, user, _ = application(call)
    mail = render_to_mail(event=call, subject_template="Receipt", text_template="Private content", context_kwargs={"submission": submission, "user": user})
    save_draft(mail, to=user.email, submissions=[submission])
    entered, release, second_done = threading.Event(), threading.Event(), threading.Event()
    failures = []
    original_send = ManualEmailBackend.send_messages
    original_delete = Submission.delete

    def slow_send(backend, messages):
        entered.set()
        assert release.wait(10)
        return original_send(backend, messages)

    def slow_delete(obj, *args, **kwargs):
        entered.set()
        assert release.wait(10)
        return original_delete(obj, *args, **kwargs)

    def deliver():
        task_send_draft.run(mail.pk)

    def erase():
        erase_application(submission.pk)

    def run(fn, done=None):
        close_old_connections()
        try:
            fn()
        except BaseException as exc:
            failures.append(exc)
        finally:
            connection.close()
            if done:
                done.set()

    first_fn, second_fn = (deliver, erase) if first == "delivery" else (erase, deliver)
    target, method, replacement = (ManualEmailBackend, "send_messages", slow_send) if first == "delivery" else (Submission, "delete", slow_delete)
    with patch.object(target, method, replacement):
        t1 = threading.Thread(target=run, args=(first_fn,))
        t2 = threading.Thread(target=run, args=(second_fn, second_done))
        try:
            t1.start()
            assert entered.wait(10)
            t2.start()
            assert not second_done.wait(0.2)
        finally:
            release.set()
            t1.join(15)
            if t2.ident:
                t2.join(15)
    assert not failures
    assert not t1.is_alive() and not t2.is_alive()
    assert not ManualMail.objects.exists()
    assert not QueuedMail.objects.filter(pk=mail.pk).exists()
    assert not Submission.all_objects.filter(pk=submission.pk).exists()


def test_ordinary_state_changes_cannot_silently_erase_an_application(call):
    from pretalx.submission.domain.submission import set_submission_state, set_pending_state
    from pretalx.common.exceptions import SubmissionError
    submission, _, _ = application(call)
    with pytest.raises(SubmissionError, match="confirmation"):
        set_submission_state(submission, "withdrawn", orga=True)
    with pytest.raises(SubmissionError, match="confirmation"):
        set_pending_state(submission, "withdrawn")
    assert Submission.all_objects.filter(pk=submission.pk).exists()

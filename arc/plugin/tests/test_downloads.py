from datetime import timedelta
from pathlib import Path
from zipfile import ZipFile

import pytest
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils.timezone import now
from django_scopes import scope, scopes_disabled
from pretalx.cfp.flow.base import FormFlowStep
from pretalx.common.models import CachedFile
from pretalx.submission.models import Answer
from tests.factories import (
    EventFactory, QuestionFactory, ReviewFactory, SpeakerFactory,
    SubmissionFactory, TeamFactory, TrackFactory, UserFactory,
)

from pretalx_arc_application.storage import check_private_storage

pytestmark = pytest.mark.django_db
DOCUMENT = b"%PDF-1.4 synthetic private application"


@pytest.fixture
def document(settings, tmp_path):
    settings.DATA_DIR = tmp_path / "data"
    settings.MEDIA_ROOT = tmp_path / "public"
    event = EventFactory(plugins="pretalx_arc_application")
    with scope(event=event):
        speaker = SpeakerFactory(event=event)
        submission = SubmissionFactory(event=event)
        submission.speakers.add(speaker)
        question = QuestionFactory(event=event, variant="file")
        answer = Answer.objects.create(question=question, submission=submission)
        answer.answer_file.save("cv.pdf", ContentFile(DOCUMENT))
    return answer, speaker.user


def team_user(event, *, reviewer=False):
    user = UserFactory()
    team = TeamFactory(
        organiser=event.organiser, can_change_submissions=not reviewer,
        is_reviewer=reviewer, all_events=False,
    )
    team.limit_events.add(event)
    team.members.add(user)
    return user, team


def get_document(client, answer, user=None):
    if user:
        client.force_login(user)
    return client.get(answer.answer_file.url)


def assert_document(response):
    assert response.status_code == 200
    assert b"".join(response.streaming_content) == DOCUMENT
    assert response["Content-Type"] == "application/octet-stream"
    assert response["Content-Disposition"].startswith("attachment;")
    assert response["X-Content-Type-Options"] == "nosniff"
    assert "no-store" in response["Cache-Control"]
    assert "private" in response["Cache-Control"]
    response.close()


def test_document_saved_outside_public_media_and_ui_link_is_protected(document, settings):
    answer, _ = document
    path = Path(answer.answer_file.path)
    assert path.is_relative_to(settings.DATA_DIR / "arc-private")
    assert not (settings.MEDIA_ROOT / answer.answer_file.name).exists()
    assert path.stat().st_mode & 0o777 == 0o600
    assert answer.answer_file.url.startswith("/arc-files/")
    with scope(event=answer.question.event):
        assert answer.answer_string == answer.answer_file.url


def test_owner_can_download(client, document):
    answer, owner = document
    assert_document(get_document(client, answer, owner))


def test_anonymous_and_other_applicant_cannot_download(client, document):
    answer, _ = document
    response = get_document(client, answer)
    assert response.status_code == 404
    assert "no-store" in response["Cache-Control"]
    assert get_document(client, answer, UserFactory()).status_code == 404


def test_organiser_access_and_revocation(client, document):
    answer, _ = document
    user, team = team_user(answer.question.event)
    assert_document(get_document(client, answer, user))
    team.members.remove(user)
    assert client.get(answer.answer_file.url).status_code == 404


def test_other_event_organiser_denied(client, document):
    answer, _ = document
    user, _ = team_user(EventFactory())
    assert get_document(client, answer, user).status_code == 404


def test_reviewer_must_have_assignment_and_question_visibility(client, document):
    answer, _ = document
    event = answer.question.event
    with scope(event=event):
        phase = event.active_review_phase
        phase.proposal_visibility = "assigned"
        phase.can_see_speaker_names = True
        phase.save()
    user, _ = team_user(event, reviewer=True)
    assert get_document(client, answer, user).status_code == 404
    with scope(event=event):
        answer.submission.assigned_reviewers.add(user)
    assert_document(client.get(answer.answer_file.url))
    with scope(event=event):
        answer.question.is_visible_to_reviewers = False
        answer.question.save()
    assert client.get(answer.answer_file.url).status_code == 404


def test_team_restriction_applies_to_organiser(client, document):
    answer, _ = document
    user, team = team_user(answer.question.event)
    other_team = TeamFactory(organiser=answer.question.event.organiser)
    with scope(event=answer.question.event):
        answer.question.limit_teams.add(other_team)
    assert get_document(client, answer, user).status_code == 404
    with scope(event=answer.question.event):
        answer.question.limit_teams.add(team)
    assert_document(client.get(answer.answer_file.url))


def test_public_question_does_not_grant_file_access(client, document):
    answer, _ = document
    with scope(event=answer.question.event):
        answer.question.is_public = True
        answer.question.save()
    assert get_document(client, answer).status_code == 404
    assert get_document(client, answer, UserFactory()).status_code == 404


def test_disabling_workflow_keeps_documents_private_and_available_to_owner(client, document):
    answer, owner = document
    event = answer.question.event
    event.plugins = ""
    event.save()
    assert get_document(client, answer).status_code == 404
    assert_document(get_document(client, answer, owner))


def test_missing_file_and_unknown_path_fail_closed(client, document):
    answer, owner = document
    client.force_login(owner)
    assert client.get("/arc-files/unknown/cv.pdf").status_code == 404
    assert client.get("/arc-files/../.secret").status_code == 404
    Path(answer.answer_file.path).unlink()
    assert client.get(answer.answer_file.url).status_code == 404


def test_media_url_never_serves_document_even_in_debug(client, document, settings):
    answer, owner = document
    settings.DEBUG = True
    client.force_login(owner)
    # Exercise Django's actual development media-serving view too, regardless
    # of whether the root URLconf was imported before DEBUG was changed.
    from django.http import Http404
    from django.test import RequestFactory
    from django.views.static import serve

    with pytest.raises(Http404):
        serve(RequestFactory().get("/media/" + answer.answer_file.name),
              answer.answer_file.name, document_root=settings.MEDIA_ROOT)


def test_temporary_form_uploads_are_private(document, settings):
    storage = FormFlowStep.file_storage
    name = storage.save("temporary-cv.pdf", SimpleUploadedFile("cv.pdf", DOCUMENT))
    assert Path(storage.path(name)).is_relative_to(settings.DATA_DIR / "arc-private")
    assert not (settings.MEDIA_ROOT / "cfp_uploads" / name).exists()
    with storage.open(name) as uploaded:
        assert uploaded.read() == DOCUMENT


def test_cached_files_are_private_and_not_directly_downloadable(client, document, settings):
    _, owner = document
    cached = CachedFile.objects.create(
        expires=now() + timedelta(hours=1), filename="applications.zip",
        content_type="application/zip",
    )
    cached.file.save("applications.zip", ContentFile(DOCUMENT))
    assert Path(cached.file.path).is_relative_to(settings.DATA_DIR / "arc-private")
    assert not (settings.MEDIA_ROOT / cached.file.name).exists()
    client.force_login(owner)
    assert client.get(cached.file.url).status_code == 404
    # The existing authorised export views stream this file themselves.
    with cached.file.open() as archive:
        assert archive.read() == DOCUMENT


def test_public_private_storage_overlap_is_rejected(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    settings.DATA_DIR = tmp_path / "data"
    assert check_private_storage(None)[0].id == "pretalx_arc_application.E001"


def test_reviewer_track_restriction(client, document):
    answer, _ = document
    event = answer.question.event
    with scope(event=event):
        track = TrackFactory(event=event)
        other_track = TrackFactory(event=event)
        answer.submission.track = track
        answer.submission.save()
        phase = event.active_review_phase
        phase.proposal_visibility = "all"
        phase.can_see_speaker_names = True
        phase.save()
    user, team = team_user(event, reviewer=True)
    team.limit_tracks.add(other_track)
    assert get_document(client, answer, user).status_code == 404
    team.limit_tracks.add(track)
    assert_document(client.get(answer.answer_file.url))


def test_anonymised_reviewer_cannot_read_private_cv(client, document):
    answer, _ = document
    event = answer.question.event
    with scope(event=event):
        phase = event.active_review_phase
        phase.proposal_visibility = "all"
        phase.can_see_speaker_names = False
        phase.save()
    user, _ = team_user(event, reviewer=True)
    assert get_document(client, answer, user).status_code == 404


def test_speaker_question_ownership(client, document):
    answer, owner = document
    with scope(event=answer.question.event):
        answer.question.target = "speaker"
        answer.question.save()
        answer.speaker = answer.submission.speakers.get(user=owner)
        answer.submission = None
        answer.save()
    assert_document(get_document(client, answer, owner))
    assert get_document(client, answer, UserFactory()).status_code == 404


def test_review_document_not_visible_to_applicant_or_revoked_reviewer(client, document):
    answer, applicant = document
    event = answer.question.event
    reviewer, team = team_user(event, reviewer=True)
    with scope(event=event):
        phase = event.active_review_phase
        phase.proposal_visibility = "all"
        phase.can_see_speaker_names = True
        phase.save()
        answer.question.target = "reviewer"
        answer.question.save()
        answer.review = ReviewFactory(submission=answer.submission, user=reviewer)
        answer.submission = None
        answer.save()
    assert get_document(client, answer, applicant).status_code == 404
    assert_document(get_document(client, answer, reviewer))
    team.members.remove(reviewer)
    assert client.get(answer.answer_file.url).status_code == 404


def test_cross_event_answer_reference_is_denied(client, document):
    answer, owner = document
    other = SubmissionFactory()
    with scopes_disabled():
        other.speakers.add(SpeakerFactory(event=other.event, user=owner))
        answer.submission = other
        answer.save()
    assert get_document(client, answer, owner).status_code == 404


def test_head_requires_same_permissions_and_post_is_rejected(client, document):
    answer, owner = document
    assert client.head(answer.answer_file.url).status_code == 404
    client.force_login(owner)
    response = client.head(answer.answer_file.url)
    assert response.status_code == 200
    assert b"".join(response.streaming_content) == b""
    response.close()
    assert client.post(answer.answer_file.url).status_code == 405


def test_export_job_can_read_private_answers(document, settings):
    from pretalx.submission.domain.question import export_answer_files

    answer, _ = document
    cached = CachedFile.objects.create(
        expires=now() + timedelta(hours=1), filename="applications.zip",
        content_type="application/zip",
    )
    with scope(event=answer.question.event):
        export_answer_files(question=answer.question, cached_file=cached)
    cached.refresh_from_db()
    assert Path(cached.file.path).is_relative_to(settings.DATA_DIR / "arc-private")
    with cached.file.open("rb") as exported, ZipFile(exported) as archive:
        assert len(archive.namelist()) == 1
        assert archive.read(archive.namelist()[0]) == DOCUMENT


@pytest.mark.parametrize("operation", ["replace", "clear", "delete"])
def test_private_answer_cleanup_after_commit(document, django_capture_on_commit_callbacks, operation):
    answer, _ = document
    old_path = Path(answer.answer_file.path)
    with scope(event=answer.question.event), django_capture_on_commit_callbacks(execute=True):
        if operation == "replace":
            answer.answer_file.save("replacement.pdf", ContentFile(DOCUMENT))
        elif operation == "clear":
            answer.answer_file = ""
            answer.save()
        else:
            answer.delete(skip_log=True)
        assert old_path.exists()
    assert not old_path.exists()
    if operation == "replace":
        assert Path(answer.answer_file.path).exists()


def test_rolled_back_deletion_keeps_private_document(document, django_capture_on_commit_callbacks):
    from django.db import transaction

    answer, _ = document
    path = Path(answer.answer_file.path)
    with scope(event=answer.question.event), django_capture_on_commit_callbacks(execute=True):
        with pytest.raises(RuntimeError), transaction.atomic():
            answer.delete(skip_log=True)
            raise RuntimeError("roll back")
    assert path.exists()


def test_cached_file_expiry_can_remove_private_file(document, django_capture_on_commit_callbacks):
    cached = CachedFile.objects.create(
        expires=now() - timedelta(hours=1), filename="expired.zip",
        content_type="application/zip",
    )
    cached.file.save("expired.zip", ContentFile(DOCUMENT))
    path = Path(cached.file.path)
    with django_capture_on_commit_callbacks(execute=True):
        cached.delete()
    assert not path.exists()

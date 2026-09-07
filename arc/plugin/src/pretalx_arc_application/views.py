from pathlib import PurePosixPath

from django.http import FileResponse, HttpResponseNotFound
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_safe
from django_scopes import scope, scopes_disabled
from pretalx.submission.domain.queries.question import questions_for_user
from pretalx.submission.enums import QuestionTarget
from pretalx.submission.models import Answer
from pretalx.submission.rules import has_reviewer_access, orga_can_change_submissions


def can_download_answer(user, answer):
    """Require ownership or both object access and question visibility.

    Public-question and public-schedule flags never confer file access.
    """
    question = answer.question
    event = question.event
    if not user.is_authenticated or not user.is_active:
        return False

    if question.target == QuestionTarget.SUBMISSION:
        submission = answer.submission
        if not submission or submission.event_id != event.pk:
            return False
        if submission.speakers.filter(user=user).exists():
            return True
        target_access = orga_can_change_submissions(user, submission) or (
            has_reviewer_access(user, submission)
        )
    elif question.target == QuestionTarget.SPEAKER:
        speaker = answer.speaker
        if not speaker or speaker.event_id != event.pk:
            return False
        if speaker.user_id == user.pk:
            return True
        target_access = orga_can_change_submissions(user, speaker) or any(
            has_reviewer_access(user, submission)
            for submission in speaker.submissions.all()
        )
    elif question.target == QuestionTarget.REVIEWER:
        review = answer.review
        if not review or review.submission.event_id != event.pk:
            return False
        target_access = orga_can_change_submissions(user, review) or (
            has_reviewer_access(user, review.submission)
            and user.has_perm("submission.view_review", review)
        )
    else:
        return False

    return (
        bool(target_access)
        and questions_for_user(event, user).filter(pk=question.pk).exists()
    )


@never_cache
@require_safe
def download(request, name):
    # Return the same response for unknown documents and denied access.
    if (
        not request.user.is_authenticated
        or not request.user.is_active
        or "\\" in name
        or any(part in ("", ".", "..") for part in name.split("/"))
    ):
        return HttpResponseNotFound()

    # No event URL parameter: protection must work even for disabled workflows.
    # The exact stored name is only a lookup key, never an authorisation token.
    with scopes_disabled():
        answers = list(
            Answer.objects.filter(answer_file=name).select_related(
                "question__event",
                "submission__event",
                "speaker__event",
                "review__submission__event",
            )[:2]
        )
    if len(answers) != 1:
        return HttpResponseNotFound()
    answer = answers[0]
    with scope(event=answer.question.event):
        if not can_download_answer(request.user, answer):
            return HttpResponseNotFound()
        try:
            document = answer.answer_file.open("rb")
        except (FileNotFoundError, ValueError):
            return HttpResponseNotFound()
        response = FileResponse(
            document,
            as_attachment=True,
            filename=PurePosixPath(name).name,
            content_type="application/octet-stream",
        )
    response["X-Content-Type-Options"] = "nosniff"
    response["Referrer-Policy"] = "no-referrer"
    return response

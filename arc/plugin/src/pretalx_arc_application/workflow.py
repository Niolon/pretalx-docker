"""Small, version-tested adapters for the dedicated recruitment instance."""

import shutil
from pathlib import PurePosixPath
from tempfile import TemporaryFile
from zipfile import ZIP_DEFLATED, ZipFile

from django.forms import FileField
from django.http import FileResponse, Http404
from django.utils.cache import patch_cache_control


def question_download(view, request, *args, **kwargs):
    """Build only the currently authorised documents, without persistent exports."""
    from .views import can_download_answer

    if "cached_file" in request.GET or "async_id" in request.GET:
        raise Http404
    question = view.question  # upstream dispatch has already checked permission
    if not question or question.variant != "file":
        raise Http404
    archive = TemporaryFile(mode="w+b")
    try:
        with ZipFile(archive, "w", ZIP_DEFLATED) as bundle:
            answers = question.answers.exclude(answer_file="").filter(
                answer_file__isnull=False
            ).select_related("question__event", "submission", "speaker", "review__submission")
            for answer in answers:
                if not can_download_answer(request.user, answer):
                    continue
                name = f"{answer.pk}-{PurePosixPath(answer.answer_file.name).name}"
                with answer.answer_file.open("rb") as source, bundle.open(name, "w") as target:
                    shutil.copyfileobj(source, target)
        archive.seek(0)
        response = FileResponse(
            archive, as_attachment=True,
            filename=f"{request.event.slug}-documents.zip", content_type="application/zip",
        )
        patch_cache_control(response, private=True, no_store=True, no_cache=True, must_revalidate=True)
        response["X-Content-Type-Options"] = "nosniff"
        return response
    except Exception:
        archive.close()
        raise


def disabled_schedule_export(view, request, *args, **kwargs):
    # A static website archive has no use in a private, UI-only interview workflow.
    raise Http404


def install_workflow():
    from pretalx.cfp.flow.base import FormFlowStep
    from pretalx.orga.views.cfp import QuestionFileDownloadView
    from pretalx.orga.views.schedule import ScheduleExportDownloadView

    if getattr(FormFlowStep, "_recruitment_installed", False):
        return
    original_get_form = FormFlowStep.get_form

    def get_form(step, from_storage=False):
        form = original_get_form(step, from_storage=from_storage)
        if not form.is_bound and step.identifier in step.cfp_session.get("data", {}):
            # Empty saved data is still a submitted form, not an initial GET.
            form.is_bound = True
        for name, field in form.fields.items():
            if isinstance(field, FileField) and form.files.get(name):
                # Initial is only for the widget: server-side validation still
                # requires a live stored file, merged by upstream get_form.
                form.initial[name] = form.files[name]
        return form

    FormFlowStep.get_form = get_form
    FormFlowStep._recruitment_installed = True
    QuestionFileDownloadView.get = question_download
    ScheduleExportDownloadView.get = disabled_schedule_export

    from pretalx.cfp.views.user import SubmissionsEditView, SubmissionConfirmView
    SubmissionsEditView.template_name = "recruitment/application.html"
    SubmissionConfirmView.template_name = "recruitment/confirm.html"

    from django.core.exceptions import ValidationError
    from pretalx.common.exceptions import SendMailException
    from pretalx.schedule.interfaces.forms.schedule import ScheduleReleaseForm
    from pretalx.schedule.domain import release
    from .adapters import replace_function
    from .emails import slot_details

    def validate_interviews(schedule):
        for slot in schedule.talks.filter(submission__state__in=["accepted", "confirmed"]):
            slot_details(slot)

    original_clean = ScheduleReleaseForm.clean

    def clean(form):
        data = original_clean(form)
        if data.get("notify_speakers"):
            try:
                validate_interviews(form.event.wip_schedule)
            except SendMailException as exc:
                raise ValidationError(str(exc)) from exc
        return data

    ScheduleReleaseForm.clean = clean
    original_freeze = release.freeze_schedule

    def freeze_schedule(schedule, name, user=None, notify_speakers=True, comment=None):
        if notify_speakers:
            validate_interviews(schedule)
        return original_freeze(schedule, name, user=user, notify_speakers=notify_speakers, comment=comment)

    replace_function(release, "freeze_schedule", freeze_schedule)

    # A locally controlled instance need not send update telemetry by default.
    from pretalx.common.models.settings import hierarkey
    hierarkey.add_default("update_check_enabled", "False", bool)

    from pretalx.event.models import Event
    from .schema import safe_feature_flags, SAFE_FEATURE_FLAGS

    class RecruitmentEventQuerySet(Event.objects._queryset_class):
        def update(self, **kwargs):
            if "feature_flags" in kwargs:
                if not isinstance(kwargs["feature_flags"], dict):
                    raise ValueError("Update recruitment privacy flags with a plain dictionary, not a bulk expression.")
                kwargs["feature_flags"] = safe_feature_flags(kwargs["feature_flags"])
            return super().update(**kwargs)

    Event.objects._queryset_class = RecruitmentEventQuerySet
    original_feature = Event.get_feature_flag

    def get_feature_flag(event, feature):
        if feature in SAFE_FEATURE_FLAGS:
            return SAFE_FEATURE_FLAGS[feature]
        return original_feature(event, feature)

    Event.get_feature_flag = get_feature_flag

    from django.contrib import messages
    from django.shortcuts import redirect
    from pretalx.orga.views.mails import OutboxSend, MailDetail

    def show_mail_error(original):
        def guarded(view, *args, **kwargs):
            try:
                return original(view, *args, **kwargs)
            except SendMailException as exc:
                messages.error(view.request, str(exc))
                return redirect(view.request.event.orga_urls.outbox)
        return guarded

    OutboxSend.post = show_mail_error(OutboxSend.post)
    MailDetail.form_valid = show_mail_error(MailDetail.form_valid)

"""A fixed notice and small per-call policy form, with a fail-closed intake gate."""
from django import forms
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.dispatch import receiver
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.html import format_html
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods
from pretalx.cfp.signals import footer_link
from pretalx.orga.signals import nav_event_settings, html_above_orga_page
from .models import RecruitmentPolicy


def policy_errors(event):
    policy = RecruitmentPolicy.objects.filter(event=event).first()
    if not policy:
        return ["Configure and approve the recruitment privacy notice."]
    errors = []
    if not policy.approved:
        errors.append("Durham approval of the privacy notice is required.")
    if not policy.lawful_basis.strip():
        errors.append("Enter the approved lawful basis.")
    if not policy.retention_days:
        errors.append("Enter the approved retention period in days.")
    if not policy.hosting_policy.strip():
        errors.append("Enter the approved hosting, backup and log expiry policy.")
    return errors


def intake_open(event):
    return "pretalx_arc_application" in event.plugin_list and not policy_errors(event) and not RecruitmentPolicy.objects.filter(event=event, completed_on__isnull=False).exists()


def notice_url(event):
    return reverse("plugins:pretalx_arc_application:privacy", kwargs={"event": event.slug})


class PolicyForm(forms.ModelForm):
    class Meta:
        model = RecruitmentPolicy
        fields = ["lawful_basis", "retention_days", "hosting_policy", "approved", "completed_on"]
        widgets = {"completed_on": forms.DateInput(attrs={"type": "date"})}

    def clean(self):
        data = super().clean()
        if data.get("approved") and not all(data.get(k) for k in ("lawful_basis", "retention_days", "hosting_policy")):
            raise ValidationError("Complete all policy values before recording approval.")
        if data.get("completed_on") and data["completed_on"] > timezone.localdate():
            raise ValidationError("Recruitment completion cannot be in the future.")
        if data.get("retention_days") == 0:
            raise ValidationError("Retention must be a positive number of days.")
        return data


def get_event(slug):
    from pretalx.event.models import Event
    return get_object_or_404(Event, slug=slug, plugins__contains="pretalx_arc_application")


@never_cache
@require_http_methods(["GET"])
def notice(request, event):
    call = get_event(event)
    return render(request, "recruitment/privacy.html", {"event": call, "policy": RecruitmentPolicy.objects.filter(event=call).first(), "policy_errors": policy_errors(call)})


@never_cache
@require_http_methods(["GET", "POST"])
def policy_settings(request, event):
    call = get_event(event)
    if not request.user.is_authenticated or not request.user.is_active or not request.user.has_perm("event.update_event", call):
        raise Http404
    policy = RecruitmentPolicy.objects.filter(event=call).first() or RecruitmentPolicy(event=call)
    form = PolicyForm(request.POST if request.method == "POST" else None, instance=policy)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Recruitment privacy settings saved.")
        return redirect(request.path)
    return render(request, "recruitment/privacy_settings.html", {"form": form, "event": call})


@receiver(footer_link, dispatch_uid="recruitment_privacy_footer")
def privacy_footer(sender, **kwargs):
    return {"label": "Recruitment privacy information", "url": notice_url(sender)}


@receiver(nav_event_settings, dispatch_uid="recruitment_privacy_settings")
def settings_navigation(sender, request, **kwargs):
    url = reverse("plugins:pretalx_arc_application:privacy_settings", kwargs={"event": sender.slug})
    return [{"label": "Recruitment privacy", "url": url, "active": request.path == url}]


@receiver(html_above_orga_page, dispatch_uid="recruitment_confidentiality")
def reviewer_banner(sender, request, **kwargs):
    if "/reviews/" not in request.path and "/submissions/" not in request.path:
        return ""
    return format_html('<div class="alert alert-info">{}</div>', "Application data is confidential and must only be used for this recruitment exercise; do not forward or store documents outside approved Durham systems.")


def install_privacy():
    from pretalx.submission.models import CfP
    from pretalx.cfp.views.wizard import SubmitWizard
    from pretalx.cfp.views.event import EventCfP
    from pretalx.submission.domain import submission as domain
    from .adapters import replace_function
    original_open = CfP.is_open.func
    CfP.is_open = property(lambda cfp: intake_open(cfp.event) and original_open(cfp))
    original_dispatch = SubmitWizard.dispatch

    def dispatch(view, request, *args, **kwargs):
        if not intake_open(request.event):
            messages.error(request, "Applications are closed until the recruitment privacy information is approved, or because recruitment is complete.")
            return redirect(notice_url(request.event))
        return original_dispatch(view, request, *args, **kwargs)

    SubmitWizard.dispatch = dispatch
    for name in ("create_submission", "submit_draft"):
        original = getattr(domain, name)
        def guarded(*args, _original=original, **kwargs):
            submission = kwargs.get("submission") or args[0]
            if not intake_open(submission.event):
                raise ValidationError("Recruitment intake is closed.")
            return _original(*args, **kwargs)
        replace_function(domain, name, guarded)
    # Extending upstream templates preserves the form and its validation.
    from pretalx.cfp.views import event as event_views
    event_views.EventStartpage.template_name = "recruitment/landing.html"
    EventCfP.template_name = "recruitment/cfp.html"

    from pretalx.cfp.flow import steps
    for name in ("InfoStep", "QuestionsStep", "UserStep", "ProfileStep"):
        step = getattr(steps, name)
        step.template_name = "recruitment/" + name + ".html"

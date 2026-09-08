"""Local email preparation, with no SMTP delivery in manual mode."""

from contextvars import ContextVar
from email import policy
from email.parser import BytesParser

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.mail.backends.base import BaseEmailBackend
from django.db import transaction
from django.dispatch import receiver
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.html import strip_tags
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods
from django_scopes import scopes_disabled
from pretalx.orga.signals import nav_global, nav_event

from .models import ManualMail

_source_mail = ContextVar("manual_mail_source", default=None)
_manual_delivery = ContextVar("manual_mail_delivery", default=False)


def enabled(event=None, force_custom=False):
    """Missing configuration selects manual delivery; SMTP errors never do."""
    if event is not None and (event.mail_settings["smtp_use_custom"] or force_custom):
        return not (event.mail_settings["smtp_host"] or "").strip()
    return not (getattr(settings, "EMAIL_HOST", "") or "").strip()


def allowed(user):
    return user.is_authenticated and user.is_active and user.is_administrator


class ManualEmailBackend(BaseEmailBackend):
    def send_messages(self, email_messages):
        count = 0
        for email in email_messages:
            if not email.recipients():
                continue
            raw = email.message().as_bytes()
            parsed = BytesParser(policy=policy.default).parsebytes(raw)
            plain = parsed.get_body(preferencelist=("plain",))
            if plain is not None:
                body = plain.get_content()
            else:
                html = parsed.get_body(preferencelist=("html",))
                body = strip_tags(html.get_content()) if html else ""
            source = _source_mail.get()
            item = ManualMail(
                subject=email.subject, recipients=email.to, cc=email.cc, bcc=email.bcc,
                sender=email.from_email, body=body,
                source_mail_id=source.pk if source else None,
                requires_validation=source is not None,
            )
            try:
                with transaction.atomic():
                    item.message.save(f"{item.pk}.eml", ContentFile(raw), save=False)
                    item.save()
            except Exception:
                if item.message:
                    item.message.delete(save=False)
                raise
            count += 1
        return count


def unavailable_reason(item):
    if not item.requires_validation:
        return None
    if not item.source_mail_id:
        return "The source message no longer exists. Prepare a new email before handing it over."
    from .emails import validate_mail
    from pretalx.common.exceptions import SendMailException

    try:
        with scopes_disabled():
            validate_mail(item.source_mail)
    except SendMailException as exc:
        return str(exc)
    return None


@never_cache
@require_http_methods(["GET", "POST"])
def mailbox(request, message_id=None):
    if not allowed(request.user):
        raise Http404
    if message_id is None:
        if request.method != "GET":
            raise Http404
        from django.core.paginator import Paginator
        page = Paginator(ManualMail.objects.all(), 50).get_page(request.GET.get("page"))
        return render(request, "recruitment/manual_mail_list.html", {"page": page})
    item = get_object_or_404(ManualMail.objects.select_related("source_mail"), pk=message_id)
    reason = unavailable_reason(item)
    if request.method == "POST":
        if reason or request.POST.get("action") != "handed_over":
            raise Http404
        ManualMail.objects.filter(pk=item.pk, handed_over_at__isnull=True).update(
            handed_over_at=timezone.now(), handed_over_by=request.user,
        )
        return redirect("plugins:pretalx_arc_application:manual_mail_detail", message_id=item.pk)
    response = render(request, "recruitment/manual_mail_detail.html", {"mail": item, "unavailable_reason": reason})
    response["Referrer-Policy"] = "no-referrer"
    return response


@never_cache
@require_http_methods(["GET"])
def download_message(request, message_id):
    if not allowed(request.user):
        raise Http404
    item = get_object_or_404(ManualMail.objects.select_related("source_mail"), pk=message_id)
    if unavailable_reason(item):
        raise Http404
    try:
        source = item.message.open("rb")
    except (FileNotFoundError, ValueError):
        raise Http404 from None
    response = FileResponse(source, as_attachment=True, filename="prepared-email.eml", content_type="application/octet-stream")
    response["X-Content-Type-Options"] = "nosniff"
    response["Referrer-Policy"] = "no-referrer"
    return response


@receiver(nav_global, dispatch_uid="manual_mail_global_navigation")
@receiver(nav_event, dispatch_uid="manual_mail_event_navigation")
def navigation(sender, request, **kwargs):
    if not allowed(request.user):
        return []
    url = reverse("plugins:pretalx_arc_application:manual_mail")
    return [{"label": "Manual delivery", "url": url, "icon": "fa-envelope-o", "active": request.path.startswith(url)}]


def install_manual_mail():
    from pretalx.mail.domain import smtp
    from .adapters import replace_function

    if enabled():
        settings.EMAIL_BACKEND = "pretalx_arc_application.manual_mail.ManualEmailBackend"
    original_filter = smtp.filter_recipients

    def filter_recipients(recipients):
        if _manual_delivery.get():
            values = [recipients] if isinstance(recipients, str) else recipients
            return [address for address in values if address]
        return original_filter(recipients)

    replace_function(smtp, "filter_recipients", filter_recipients)
    original_backend = smtp.mail_backend_for_event

    def mail_backend_for_event(event, force_custom=False):
        if enabled(event, force_custom):
            return ManualEmailBackend()
        return original_backend(event, force_custom=force_custom)

    replace_function(smtp, "mail_backend_for_event", mail_backend_for_event)
    original_envelope = smtp.resolve_envelope

    def resolve_envelope(event, reply_to):
        sender, reply_to, backend = original_envelope(event, reply_to)
        if enabled(event):
            backend = ManualEmailBackend()
        return sender, reply_to, backend

    replace_function(smtp, "resolve_envelope", resolve_envelope)
    original_payload = smtp.deliver_payload

    def deliver_payload(*, event, **kwargs):
        token = _manual_delivery.set(enabled(event))
        try:
            return original_payload(event=event, **kwargs)
        finally:
            _manual_delivery.reset(token)

    replace_function(smtp, "deliver_payload", deliver_payload)
    original_delivery = smtp.deliver_persisted

    def deliver_persisted(mail):
        token = _source_mail.set(mail)
        try:
            return original_delivery(mail)
        finally:
            _source_mail.reset(token)

    replace_function(smtp, "deliver_persisted", deliver_persisted)


from django.utils.html import format_html
from pretalx.orga.signals import html_above_orga_page


@receiver(html_above_orga_page, dispatch_uid="manual_mail_mode_banner")
def mode_banner(sender, request, **kwargs):
    if not enabled(getattr(request, "event", None)):
        return ""
    if allowed(request.user):
        return format_html(
            '<div class="alert alert-info">Manual email delivery is enabled. '
            'Emails are prepared for you to pass on; they are not delivered automatically. '
            '<a href="{}">Open manual delivery</a></div>',
            reverse("plugins:pretalx_arc_application:manual_mail"),
        )
    return format_html(
        '<div class="alert alert-info">{}</div>',
        "Manual email delivery is enabled. An instance administrator must pass prepared emails on to their recipients.",
    )

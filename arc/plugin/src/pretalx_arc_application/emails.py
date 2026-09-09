"""Recruitment correspondence: per-call copy and private interview details."""

from contextvars import ContextVar

_notification_context = ContextVar("recruitment_notification", default=None)
_application_context = ContextVar("recruitment_mail_application", default=None)

from django.db import transaction
from i18nfield.strings import LazyI18nString

from pretalx.common.exceptions import SendMailException
from pretalx.mail.enums import MailTemplateRoles as Roles

SIGNOFF = "\n\nKind regards,\nThe {event_name} recruitment team"
TEMPLATES = {
    Roles.NEW_SUBMISSION: (
        "Application received — {submission_type}",
        "Hello {name},\n\nThank you for applying for {submission_type}. We have received your application.\n\n"
        "You can view your application here:\n{submission_url}\n\n"
        "Privacy information: {recruitment_privacy_url}\n\nWe will contact you when shortlisting is complete. If you have any questions, please reply to this email." + SIGNOFF,
    ),
    Roles.SUBMISSION_ACCEPT: (
        "You have been shortlisted — {event_name}",
        "Hello {name},\n\nWe are pleased to let you know that you have been shortlisted for {submission_type}.\n\n"
        "Please use the following link to confirm that you would like to proceed to interview:\n{confirmation_link}\n\n"
        "We will send your interview date, time and joining instructions separately. "
        "If you no longer wish to proceed, please reply to let us know." + SIGNOFF,
    ),
    Roles.SUBMISSION_REJECT: (
        "Update on your application — {submission_type}",
        "Hello {name},\n\nThank you for your interest in {submission_type} and for the time you put into your application.\n\n"
        "After reviewing applications, we are sorry to let you know that we will not be inviting you to interview on this occasion.\n\n"
        "We wish you all the best with your future applications." + SIGNOFF,
    ),
    Roles.NEW_SCHEDULE: (
        "{interview_subject} — {event_name}",
        "Hello {name},\n\n{interview_opening}\n\n{interview_details}\n\n"
        "Please reply if this time is unsuitable or if you need any adjustments to take part. "
        "Please tell us what would help you participate; you do not need to provide a diagnosis or medical history.\n\n"
        "We look forward to meeting you." + SIGNOFF,
    ),
}


def slot_details(slot):
    if not slot or not slot.start or not slot.end or not slot.room:
        raise SendMailException("Assign an interview date, time and location before sending an invitation.")
    if slot.end <= slot.start:
        raise SendMailException("The interview must end after it starts.")
    if not str(slot.room.speaker_info).strip():
        raise SendMailException("Add joining instructions to the interview room before sending an invitation.")
    start, end = slot.local_start, slot.local_end
    return (
        f"Position: {slot.submission.submission_type.name}\n"
        f"Date: {start:%A, %d %B %Y}\n"
        f"Time: {start:%H:%M}–{end:%H:%M} {start:%Z} (UTC{start:%z}; {slot.event.timezone})\n"
        f"Location: {slot.room.name}\n\n{slot.room.speaker_info}\n\n"
        f"Your application: {slot.submission.urls.user_base.full()}"
    )


def render_notifications(data, event):
    # The trusted Markdown placeholder sanitises this organiser-owned text.
    slots = list(data.get("create") or []) + [item["new_slot"] for item in data.get("update", [])]
    return "\n\n---\n\n".join(slot_details(slot) for slot in slots)


def send_initial_mails(submission, *, person):
    from pretalx.mail.domain.render import render_template_to_mail
    from pretalx.mail.domain.queue import save_draft
    from pretalx.mail.domain.send import send_draft, send_transient
    from pretalx.mail.domain.template import mail_template_by_role

    mail = render_template_to_mail(
        mail_template_by_role(submission.event, Roles.NEW_SUBMISSION),
        context_kwargs={"user": person, "submission": submission},
        locale=submission.get_email_locale(person.locale),
    )
    save_draft(mail, to_users=[person], submissions=[submission])
    send_draft(mail)
    if submission.event.mail_settings["mail_on_new_submission"]:
        internal = render_template_to_mail(
            mail_template_by_role(submission.event, Roles.NEW_SUBMISSION_INTERNAL),
            context_kwargs={"user": person, "submission": submission},
            safe_extra_context={"orga_url": submission.orga_urls.base},
            locale=submission.event.locale,
        )
        internal.to = submission.event.email
        send_transient(internal)


def validate_mail(mail):
    from .mail_isolation import require_single_application
    from django.core.exceptions import ValidationError
    try:
        require_single_application(mail)
    except ValidationError as exc:
        raise SendMailException("Message contains conflicting application ownership; prepare separate messages.") from exc
    role = mail.template.role if mail.template_id else None
    expected = {
        Roles.SUBMISSION_ACCEPT: {"accepted", "confirmed"},
        Roles.SUBMISSION_REJECT: {"rejected"},
        Roles.NEW_SCHEDULE: {"accepted", "confirmed"},
    }.get(role)
    if not expected:
        return
    submissions = list(mail.submissions.all())
    if not submissions or any(s.state not in expected or s.pending_state for s in submissions):
        raise SendMailException("This email no longer matches the application decision. Review the decision and create a new draft.")
    if role == Roles.NEW_SCHEDULE:
        # Match the attached calendar against current released arrangements.
        from pretalx.schedule.domain.ical import get_slot_ical
        import vobject

        expected_events = {}
        for submission in submissions:
            slots = submission.slots.filter(schedule=submission.event.current_schedule)
            for slot in slots:
                slot_details(slot)
                event = vobject.readOne(get_slot_ical(slot).serialize()).vevent
                expected_events[event.uid.value] = (
                    event.dtstart.value, event.dtend.value, event.location.value,
                    event.description.value, event.url.value,
                )
        actual = {}
        for attachment in mail.attachments or []:
            if attachment.get("content_type") == "text/calendar":
                for event in vobject.readOne(attachment["content"]).vevent_list:
                    actual[event.uid.value] = (
                        event.dtstart.value, event.dtend.value, event.location.value,
                        event.description.value, event.url.value,
                    )
        if not expected_events or actual != expected_events:
            raise SendMailException("Interview arrangements have changed. Generate a new invitation before sending.")


def install_emails():
    from .adapters import replace_function
    from pretalx.mail import template_phrases
    from pretalx.mail.domain import send, smtp
    from pretalx.mail.domain import context
    from pretalx.mail.signals import register_mail_placeholders
    from functools import wraps
    from pretalx.submission.domain import submission as domain
    from pretalx.schedule.domain import notifications, ical

    # The dedicated image installs mail defaults instance-wide, so their
    # placeholders must also work before a call enables the form plugin.
    original_placeholders = context.base_placeholders

    @wraps(original_placeholders)
    def base_placeholders(sender, **kwargs):
        result = original_placeholders(sender, **kwargs)
        return result + (interview_placeholders(sender, **kwargs) if sender else [])

    register_mail_placeholders.disconnect(dispatch_uid="pretalx_register_base_placeholders")
    replace_function(context, "base_placeholders", base_placeholders)
    register_mail_placeholders.connect(base_placeholders, dispatch_uid="pretalx_register_base_placeholders", weak=False)

    for role, (subject, text) in TEMPLATES.items():
        template_phrases.DEFAULT_PHRASES[role] = (
            LazyI18nString({"en": subject}), LazyI18nString({"en": text}),
        )
    replace_function(domain, "send_initial_mails", send_initial_mails)
    replace_function(notifications, "render_notifications", render_notifications)
    replace_function(notifications, "compute_speakers_concerned", compute_speakers_concerned)
    for name in ("get_current_notifications", "get_full_notifications"):
        original_notifications = getattr(notifications, name)
        def scoped_notifications(user, event, _original=original_notifications):
            data = _notification_context.get() or _original(user, event)
            application_id = _application_context.get()
            if application_id is None and (data.get("create") or data.get("update")):
                raise SendMailException("Select one application before including interview details in a message.")
            return {
                "create": [slot for slot in data.get("create", []) if slot.submission_id == application_id],
                "update": [item for item in data.get("update", []) if item["new_slot"].submission_id == application_id],
            }
        replace_function(notifications, name, scoped_notifications)


    def generate_notifications(schedule):
        with transaction.atomic():
            affected = {
                slot.submission_id
                for data in schedule.speakers_concerned.values()
                for slot in list(data.get("create") or []) + [item["new_slot"] for item in data.get("update", [])]
            }
            schedule.event.queued_mails.filter(
                state="draft", template__role=Roles.NEW_SCHEDULE, submissions__pk__in=affected,
            ).delete()
            return generate_individual_notifications(schedule)

    replace_function(notifications, "generate_notifications", generate_notifications)
    def count_pending_notifications(schedule):
        return sum(len({slot.submission_id for slot in list(data.get("create") or []) + [item["new_slot"] for item in data.get("update", [])]}) for data in schedule.speakers_concerned.values())
    replace_function(notifications, "count_pending_notifications", count_pending_notifications)

    original_calendar = ical.build_slot_vevent

    def build_slot_vevent(slot, calendar, **kwargs):
        original_calendar(slot, calendar, **kwargs)
        if slot.start and slot.local_end and slot.room and slot.submission:
            event = calendar.vevent_list[-1]
            event.description.value = slot_details(slot)
            event.url.value = slot.submission.urls.user_base.full()
            event.summary.value = f"Interview — {slot.submission.submission_type.name}"
            event.add("sequence").value = str(slot.schedule_id)

    replace_function(ical, "build_slot_vevent", build_slot_vevent)
    original_send = send.send_draft

    def guarded_send(mail, **kwargs):
        try:
            validate_mail(mail)
        except SendMailException as exc:
            mail.mark_failed(exc)
            raise
        return original_send(mail, **kwargs)

    replace_function(send, "send_draft", guarded_send)
    original_deliver = smtp.deliver_persisted

    def guarded_delivery(mail):
        from pretalx.submission.models import Submission
        with transaction.atomic():
            # Share locks with decision transitions: a worker cannot deliver an
            # old decision concurrently with a new decision being committed.
            list(Submission.all_objects.filter(mails=mail).order_by("pk").select_for_update())
            from pretalx.mail.models import QueuedMail
            if not QueuedMail.objects.select_for_update().filter(pk=mail.pk).exists():
                raise SendMailException("This message has been erased.")
            validate_mail(mail)
            return original_deliver(mail)

    replace_function(smtp, "deliver_persisted", guarded_delivery)


def interview_placeholders(sender, **kwargs):
    from pretalx.mail.domain.placeholders import TrustedMarkdownMailTextPlaceholder, TrustedPlainMailTextPlaceholder
    from pretalx.schedule.domain.notifications import get_current_notifications as upstream_notifications

    def get_current_notifications(user, event):
        return _notification_context.get() or upstream_notifications(user, event)

    def changed(user, event):
        return bool(get_current_notifications(user, event).get("update"))

    from .privacy import notice_url
    from pretalx.common.urls import build_absolute_uri
    return [
        TrustedPlainMailTextPlaceholder(
            "recruitment_privacy_url", ["event"],
            lambda event: build_absolute_uri("plugins:pretalx_arc_application:privacy", kwargs={"event": event.slug}),
            "Recruitment privacy information",
        ),
        TrustedPlainMailTextPlaceholder(
            "interview_subject", ["user", "event"],
            lambda user, event: "Updated interview details" if changed(user, event) else "Your interview details",
            "Your interview details",
        ),
        TrustedPlainMailTextPlaceholder(
            "interview_opening", ["user", "event"],
            lambda user, event: (
                "Your interview details have changed. Please use the updated arrangements below in place of our previous email. We apologise for any inconvenience."
                if changed(user, event) else "We would like to invite you to interview."
            ),
            "We would like to invite you to interview.",
        ),
        TrustedMarkdownMailTextPlaceholder(
            "interview_details", ["user", "event"],
            lambda user, event: render_notifications(get_current_notifications(user, event), event),
            "Date, time, timezone, location and joining instructions for your interview.",
        ),
    ]


def compute_speakers_concerned(schedule):
    """Compare private interview slots, including applicants not yet confirmed."""
    from collections import defaultdict

    previous = schedule.previous_schedule
    old_slots = {}
    if previous:
        old_slots = {
            (slot.submission_id, slot.id_suffix): slot
            for slot in previous.talks.filter(submission__isnull=False, start__isnull=False, room__isnull=False)
        }
    result = defaultdict(lambda: {"create": [], "update": []})
    for slot in schedule.talks.filter(
        submission__state__in=["accepted", "confirmed"], start__isnull=False, room__isnull=False,
    ).select_related("submission", "room"):
        old = old_slots.get((slot.submission_id, slot.id_suffix))
        if old and (old.start, old.end, old.room_id) == (slot.start, slot.end, slot.room_id):
            continue
        for speaker in slot.submission.sorted_speakers:
            if old:
                result[speaker]["update"].append({"new_slot": slot})
            else:
                result[speaker]["create"].append(slot)
    return result


def generate_individual_notifications(schedule):
    from collections import defaultdict
    from pretalx.mail.domain.queue import save_draft
    from pretalx.mail.domain.render import render_template_to_mail
    from pretalx.mail.domain.template import mail_template_by_role
    from pretalx.schedule.domain.ical import get_slot_ical
    from pretalx.common.language import language
    mails = []
    for speaker, data in schedule.speakers_concerned.items():
        groups = defaultdict(lambda: {"create": [], "update": []})
        for slot in data.get("create", []):
            groups[slot.submission_id]["create"].append(slot)
        for change in data.get("update", []):
            groups[change["new_slot"].submission_id]["update"].append(change)
        for changes in groups.values():
            slots = list(changes["create"]) + [c["new_slot"] for c in changes["update"]]
            submission = slots[0].submission
            locale = speaker.user.get_locale_for_event(schedule.event)
            token = _notification_context.set(changes)
            try:
                with language(locale):
                    attachments = [{"name": f"{slot.frab_slug}.ics", "content": get_slot_ical(slot).serialize(), "content_type": "text/calendar"} for slot in slots]
                    mail = render_template_to_mail(mail_template_by_role(schedule.event, Roles.NEW_SCHEDULE), context_kwargs={"user": speaker.user, "submission": submission}, locale=locale)
                save_draft(mail, to_users=[speaker.user], submissions=[submission], attachments=attachments)
                mails.append(mail)
            finally:
                _notification_context.reset(token)
    return mails

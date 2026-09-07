"""Make state transitions explicit and supersede unsent decision drafts."""

from contextvars import ContextVar

from django.db import transaction

from .adapters import replace_function

_transition = ContextVar("recruitment_transition", default=False)


def install_decisions():
    from pretalx.submission.domain import submission as domain
    from pretalx.submission.models import Submission
    from pretalx.submission.models.submission import SubmissionQuerySet

    original_transition = domain.set_submission_state
    original_save = Submission.save
    original_update = SubmissionQuerySet.update
    original_pending = domain.set_pending_state

    def set_submission_state(submission, new_state, **kwargs):
        with transaction.atomic():
            current = Submission.all_objects.select_for_update().get(pk=submission.pk)
            submission.state = current.state
            submission.pending_state = current.pending_state
            token = _transition.set(True)
            try:
                # Remove drafts before queuing the replacement; already sent
                # mail remains in the audit history. In-flight mail is checked
                # again by the worker immediately before delivery.
                if current.state != new_state:
                    obsolete = ["submission.state.rejected"] if new_state in {"accepted", "confirmed"} else ["submission.state.accepted", "schedule.new"]
                    if new_state not in {"accepted", "confirmed", "rejected"}:
                        obsolete.append("submission.state.rejected")
                    submission.mails.filter(state="draft", template__role__in=obsolete).delete()
                return original_transition(submission, new_state, **kwargs)
            finally:
                _transition.reset(token)

    def set_pending_state(submission, new_state):
        with transaction.atomic():
            Submission.all_objects.select_for_update().get(pk=submission.pk)
            token = _transition.set(True)
            try:
                return original_pending(submission, new_state)
            finally:
                _transition.reset(token)

    def save(submission, *args, **kwargs):
        if submission.pk and not submission._state.adding and not _transition.get():
            fields = kwargs.get("update_fields")
            if fields is None or {"state", "pending_state"}.intersection(fields):
                old = Submission.all_objects.filter(pk=submission.pk).values("state", "pending_state").first()
                if old and any(old[key] != getattr(submission, key) for key in old):
                    raise ValueError("Use set_submission_state or set_pending_state to change application decisions.")
        return original_save(submission, *args, **kwargs)

    def update(queryset, **kwargs):
        if {"state", "pending_state"}.intersection(kwargs) and not _transition.get():
            raise ValueError("Bulk decision updates bypass mail and scheduling. Use the submission domain transition functions.")
        return original_update(queryset, **kwargs)

    replace_function(domain, "set_submission_state", set_submission_state)
    replace_function(domain, "set_pending_state", set_pending_state)
    Submission.save = save
    SubmissionQuerySet.update = update

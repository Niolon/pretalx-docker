from dataclasses import dataclass

from django.db import transaction
from pretalx.submission.enums import (
    QuestionRequired,
    QuestionTarget,
    QuestionVariant,
)
from pretalx.submission.models import Question
from pretalx.submission.models.cfp import default_fields

SAFE_FEATURE_FLAGS = {
    "show_schedule": False,
    "show_featured": "never",
    "show_widget_if_not_public": False,
    "submission_public_review": False,
    "use_feedback": False,
    "attendee_signup": False,
}


@dataclass(frozen=True)
class ApplicationQuestion:
    identifier: str
    label: str
    variant: str
    required: bool = False
    help_text: str = ""
    visible_to_reviewers: bool = True


ACKNOWLEDGEMENT = "I confirm that the information in this application is accurate and that I have read the privacy information for this recruitment process."
MINIMISATION = "Do not include unnecessary phone numbers, home addresses, dates of birth, passport or visa documents, or health/EDI information. We will request any necessary additional information later."

APPLICATION_QUESTIONS = (
    ApplicationQuestion(
        identifier="arc_cv",
        label="Curriculum vitae (CV)",
        variant=QuestionVariant.FILE,
        required=True,
        help_text="Upload your current CV as a PDF.",
    ),
    ApplicationQuestion(
        identifier="arc_cover_letter",
        label="Cover letter",
        variant=QuestionVariant.FILE,
        required=True,
        help_text=(
            "Upload a PDF explaining your motivation, relevant experience and "
            "fit with this position."
        ),
    ),
    ApplicationQuestion(
        identifier="arc_degree_evidence",
        label="Evidence of required degree",
        variant=QuestionVariant.FILE,
        required=True,
        help_text="Upload a PDF of a degree certificate, final transcript showing the award, or official institutional confirmation.",
    ),
    ApplicationQuestion(
        identifier="arc_project_repository",
        label="Personal project or code repository (if applicable)",
        variant=QuestionVariant.URL,
        help_text=(
            "Link to a relevant GitHub, GitLab, portfolio or other public "
            "project. Please ensure the recruitment team can access it."
        ),
    ),
    ApplicationQuestion(
        identifier="arc_declaration",
        label=ACKNOWLEDGEMENT,
        variant=QuestionVariant.BOOLEAN,
        required=True,
        help_text="This checkbox records acknowledgement of the privacy notice, not consent to processing.",
        visible_to_reviewers=False,
    ),
)


def _application_fields():
    fields = default_fields()
    for config in fields.values():
        config["visibility"] = "do_not_ask"

    fields["name"]["visibility"] = "required"
    # A single interview type and a single content language are hidden by
    # pretalx automatically. If there are several, applicants must choose.
    fields["submission_type"]["visibility"] = "required"
    fields["content_locale"]["visibility"] = "required"
    return fields


@transaction.atomic
def configure_event(event):
    """Apply the ARC schema without deleting unrelated custom questions."""
    event.feature_flags = {**event.feature_flags, **SAFE_FEATURE_FLAGS}
    event.save(update_fields=["feature_flags"])

    event.review_phases.update(proposal_visibility="assigned", can_change_submission_state=False)

    cfp = event.cfp
    if event.submission_types.count() == 1 and str(cfp.default_type.name) == "Talk":
        cfp.default_type.name = {"en": "Position"}
        cfp.default_type.save(update_fields=["name"])
    cfp.fields = _application_fields()
    cfp.settings = {**cfp.settings, "count_length_in": "words"}
    cfp.save(update_fields=["fields", "settings"])

    for position, definition in enumerate(APPLICATION_QUESTIONS, start=10):
        question, _ = Question.all_objects.update_or_create(
            event=event,
            identifier=definition.identifier,
            defaults={
                "active": True,
                "contains_personal_data": True,
                "help_text": definition.help_text + (" " + MINIMISATION if definition.variant == QuestionVariant.FILE else ""),
                "is_public": False,
                "is_visible_to_reviewers": definition.visible_to_reviewers,
                "position": position,
                "question": definition.label,
                "question_required": (
                    QuestionRequired.REQUIRED
                    if definition.required
                    else QuestionRequired.OPTIONAL
                ),
                "target": QuestionTarget.SUBMISSION,
                "variant": definition.variant,
            },
        )
        # Prevent stale choice options if a field changed type during development.
        if question.variant not in (QuestionVariant.CHOICES, QuestionVariant.MULTIPLE):
            question.options.all().delete()


def safe_feature_flags(flags):
    return {**flags, **SAFE_FEATURE_FLAGS}

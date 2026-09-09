from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django_scopes import scopes_disabled
from pretalx.event.models import Event, Team
from pretalx.common.models.settings import GlobalSettings
from pretalx.submission.models import Question, Submission
from pretalx_arc_application.privacy import policy_errors
from pretalx_arc_application.schema import APPLICATION_QUESTIONS, ACKNOWLEDGEMENT, SAFE_FEATURE_FLAGS, _application_fields
from pretalx_arc_application.storage import check_private_storage


class Command(BaseCommand):
    help = "Check application-enforceable recruitment privacy prerequisites."

    def add_arguments(self, parser):
        parser.add_argument("--event")
        parser.add_argument("--strict", action="store_true")

    def handle(self, *args, **options):
        errors = []
        from pretalx_arc_application.models import PendingFileErasure
        if PendingFileErasure.objects.exists():
            errors.append("Retry pending private-file erasures before opening recruitment.")
        if settings.DEBUG:
            errors.append("Disable production DEBUG.")
        errors.extend(e.msg for e in check_private_storage(None))
        if GlobalSettings().settings.get("update_check_enabled", as_type=bool):
            errors.append("Disable update telemetry.")
        from pretalx.cfp.views.wizard import SubmitWizard
        from pretalx.cfp.views.event import EventStartpage, EventCfP
        from pretalx.cfp.flow import steps
        from django.template.loader import get_template
        if SubmitWizard.dispatch.__module__ != "pretalx_arc_application.privacy":
            errors.append("Recruitment intake gate is not installed.")
        templates = [EventStartpage.template_name, EventCfP.template_name] + [getattr(steps, name).template_name for name in ("InfoStep", "QuestionsStep", "UserStep", "ProfileStep")]
        for template in templates:
            if "pretalx_arc_application:privacy" not in get_template(template).template.source:
                errors.append("Restore privacy links on applicant landing/submission templates.")
        if Submission.withdraw.__module__ != "pretalx_arc_application.erasure":
            errors.append("Withdrawal erasure adapter is not installed.")
        events = Event.objects.filter(slug=options["event"]) if options["event"] else Event.objects.all()
        if not events.exists():
            errors.append("No recruitment calls found.")
        with scopes_disabled():
            for event in events:
                prefix = f"Call {event.slug}: "
                issues = policy_errors(event)
                if "pretalx_arc_application" not in event.plugin_list:
                    issues.append("Enable the recruitment plugin.")
                if any(event.feature_flags.get(k) != v for k, v in SAFE_FEATURE_FLAGS.items()):
                    issues.append("Disable public schedule, featured and review surfaces.")
                if any(event.cfp.fields.get(k, {}).get("visibility") != value["visibility"] for k, value in _application_fields().items()):
                    issues.append("Restore the minimal built-in application fields.")
                expected = {q.identifier: q for q in APPLICATION_QUESTIONS}
                questions = list(Question.all_objects.filter(event=event, active=True))
                actual = {q.identifier: q for q in questions}
                if set(actual) != set(expected):
                    issues.append("Active questions must match the minimal recruitment schema.")
                for key, definition in expected.items():
                    q = actual.get(key)
                    if not q:
                        continue
                    if str(q.question) != definition.label:
                        issues.append(f"Restore the approved question wording on {key}.")
                    if not q.contains_personal_data or q.is_public or q.is_visible_to_reviewers != definition.visible_to_reviewers:
                        issues.append(f"Correct privacy flags on {key}.")
                    if q.target != "submission" or q.variant != definition.variant or q.question_required != ("required" if definition.required else "optional"):
                        issues.append(f"Correct type and requirement on {key}.")
                declaration = actual.get("arc_declaration")
                if not declaration or str(declaration.question) != ACKNOWLEDGEMENT or "not consent" not in str(declaration.help_text):
                    issues.append("Use the exact privacy acknowledgement and non-consent explanation.")
                if event.review_phases.exclude(proposal_visibility="assigned").exists():
                    issues.append("Restrict every review phase to assigned applications.")
                teams = Team.objects.filter(organiser=event.organiser)
                applicable = [t for t in teams if t.all_events or t.limit_events.filter(pk=event.pk).exists()]
                reviewer_ids = {pk for t in applicable if t.is_reviewer for pk in t.members.values_list("pk", flat=True)}
                from pretalx.person.models import User
                from django.db.models import Q
                if User.objects.filter(pk__in=reviewer_ids).filter(Q(is_administrator=True) | Q(is_superuser=True)).exists():
                    issues.append("Use separate non-administrator accounts for assigned reviewers.")
                elevated = ("can_create_events", "can_change_teams", "can_change_organiser_settings", "can_change_event_settings", "can_change_submissions")
                if any(any(getattr(t, k) for k in elevated) and (t.is_reviewer or t.members.filter(pk__in=reviewer_ids).exists()) for t in applicable):
                    issues.append("Remove elevated/additive organiser powers from reviewer accounts.")
                if event.review_phases.filter(can_change_submission_state=True).exists():
                    issues.append("Keep application decisions with organisers.")
                receipt = event.mail_templates.filter(role="submission.new").first()
                if not receipt or "{recruitment_privacy_url}" not in str(receipt.text):
                    issues.append("Link the privacy notice in the receipt template.")
                errors.extend(prefix + issue for issue in issues)
        for error in errors:
            self.stdout.write("FAIL: " + error)
        self.stdout.write("External verification required: proxy document roots, hosting access, backups/log expiry, mail-provider retention and Durham approval. This check is not legal approval.")
        if errors and options["strict"]:
            raise CommandError(f"Privacy readiness failed: {len(errors)} requirement(s).")
        if not errors:
            self.stdout.write("PASS: application-enforceable privacy prerequisites.")

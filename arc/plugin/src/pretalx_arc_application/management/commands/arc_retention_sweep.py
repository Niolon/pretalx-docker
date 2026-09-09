from datetime import date, timedelta
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from django_scopes import scopes_disabled
from pretalx.event.models import Event
from pretalx.submission.models import Submission
from pretalx_arc_application.models import RecruitmentPolicy
from pretalx_arc_application.privacy import policy_errors
from pretalx_arc_application.erasure import erase_application


class Command(BaseCommand):
    help = "Erase expired live recruitment records; defaults to a non-mutating dry run."

    def add_arguments(self, parser):
        parser.add_argument("--event", required=True)
        parser.add_argument("--before", type=date.fromisoformat)
        parser.add_argument("--retry-file-deletions", action="store_true")
        mode = parser.add_mutually_exclusive_group()
        mode.add_argument("--dry-run", action="store_true")
        mode.add_argument("--execute", action="store_true")

    def handle(self, *args, **options):
        if options["before"] and options["before"] > timezone.localdate():
            raise CommandError("Future retention cutoffs are not permitted.")
        event = Event.objects.filter(slug=options["event"]).first()
        if not event or "pretalx_arc_application" not in event.plugin_list:
            raise CommandError("Select a recruitment call.")
        from pretalx_arc_application.file_erasure import retry_files
        if options["retry_file_deletions"]:
            if not options["execute"]:
                raise CommandError("File deletion retries require --execute.")
            remaining = retry_files(event)
            if remaining:
                raise CommandError(f"Private-file cleanup incomplete: {remaining} pending.")
            self.stdout.write("Private-file cleanup complete.")
            return
        if not options["before"]:
            raise CommandError("Specify --before for a retention sweep.")
        errors = policy_errors(event)
        if errors:
            raise CommandError(" ".join(errors))
        policy = RecruitmentPolicy.objects.get(event=event)
        if not policy.completed_on:
            raise CommandError("Recruitment has not been marked complete.")
        try:
            expires = policy.completed_on + timedelta(days=policy.retention_days)
        except OverflowError:
            raise CommandError("Retention period is out of range.") from None
        with scopes_disabled():
            ids = list(Submission.all_objects.filter(event=event).values_list("pk", flat=True)) if expires < options["before"] else []
            if not options["execute"]:
                self.stdout.write(f"DRY RUN: {len(ids)} applications eligible; no data changed.")
                return
            totals = {"applications": 0, "accounts": 0, "messages": 0, "shared_messages": 0}
            for pk in ids:
                result = erase_application(pk)
                for key, count in result.items():
                    totals[key] += count
            remaining = retry_files(event)
            if remaining:
                raise CommandError(f"Private-file cleanup incomplete: {remaining} pending. Retry after correcting storage access.")
            self.stdout.write("Deleted: " + ", ".join(f"{key}={value}" for key, value in totals.items()))
            if totals["shared_messages"]:
                self.stdout.write("Shared messages were removed in full; recreate unrelated correspondence if needed.")

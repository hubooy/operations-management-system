from django.core.management.base import BaseCommand
from ai_assistant.migration_service import migrate
from ai_assistant.policy import canonical


class Command(BaseCommand):
    help = "Approve and verify an AI domain D1 snapshot without activating production writes."

    def add_arguments(self, parser):
        parser.add_argument("--source", required=True)
        parser.add_argument(
            "--mode", required=True, choices=["dry-run", "apply", "verify-only"]
        )
        parser.add_argument("--approve-run-id", default="")

    def handle(self, *args, **options):
        self.stdout.write(
            canonical(
                migrate(options["source"], options["mode"], options["approve_run_id"])
            )
        )

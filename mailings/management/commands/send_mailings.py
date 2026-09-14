from typing import Any
from uuid import UUID

from django.core.management.base import BaseCommand, CommandError, CommandParser

from mailings.delivery import deliver_import_run, delivery_counts
from mailings.models import ImportRun


class Command(BaseCommand):
    help = "Deliver saved messages belonging to an import run. Safe to repeat for pending records."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--run", type=UUID, required=True, help="Import UUID printed by import_mailings")
        parser.add_argument("--limit", type=int, help="Maximum attempts in this invocation")
        parser.add_argument("--retry-failed", action="store_true", help="Retry failures known to occur before sending")
        parser.add_argument(
            "--retry-uncertain",
            action="store_true",
            help=(
                "Retry unknown outcomes and claims older than 5 minutes. "
                "May duplicate delivery: stop old workers first."
            ),
        )

    def handle(self, *args: Any, **options: Any) -> None:
        try:
            run = ImportRun.objects.get(pk=options["run"])
        except ImportRun.DoesNotExist as exc:
            raise CommandError("Import run does not exist") from exc
        try:
            stats = deliver_import_run(
                run,
                limit=options["limit"],
                retry_failed=options["retry_failed"],
                retry_uncertain=options["retry_uncertain"],
            )
        except KeyboardInterrupt as exc:
            raise CommandError(f"Delivery interrupted. Saved state: {delivery_counts(run)}", returncode=130) from exc
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(
            f"Attempted: {stats.attempted}; sent: {stats.sent}; failed: {stats.failed}; uncertain: {stats.uncertain}"
        )
        self.stdout.write(f"Current delivery states: {delivery_counts(run)}")

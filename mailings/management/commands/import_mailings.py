from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from mailings.delivery import deliver_import_run, delivery_counts
from mailings.services import ImportFileError, import_xlsx


class Command(BaseCommand):
    help = "Import an XLSX file and deliver its pending messages, including unfinished previous imports."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("source", type=Path, help="Path to the .xlsx file")
        parser.add_argument("--batch-size", type=int, default=500, help="Rows processed per database batch")
        parser.add_argument("--no-send", action="store_true", help="Import records without delivering messages")

    def handle(self, *args: Any, **options: Any) -> None:
        def report_error(row_number: int, message: str) -> None:
            self.stderr.write(self.style.WARNING(f"Row {row_number}: {message}"))

        try:
            import_run, stats = import_xlsx(
                options["source"],
                batch_size=options["batch_size"],
                on_row_error=report_error,
            )
        except (ImportFileError, OSError, ValueError) as exc:
            raise CommandError(str(exc)) from exc
        except KeyboardInterrupt as exc:
            raise CommandError("Import interrupted; re-run the same file to resume", returncode=130) from exc

        if not options["no_send"]:
            try:
                result = deliver_import_run(import_run)
            except KeyboardInterrupt as exc:
                self.stderr.write(f"Stopped. Resume with: python manage.py send_mailings --run {import_run.pk}")
                raise CommandError("Delivery interrupted; saved records are retained", returncode=130) from exc
            stats.sent = result.sent
            stats.delivery_failed = result.failed + result.uncertain

        self.stdout.write(self.style.SUCCESS(f"Import run: {import_run.pk}"))
        self.stdout.write(f"Processed rows: {stats.processed}")
        self.stdout.write(f"Created records: {stats.created}")
        self.stdout.write(f"Skipped records: {stats.skipped}")
        self.stdout.write(f"Erroneous rows: {stats.errors}")
        self.stdout.write(f"Sent messages: {stats.sent}")
        self.stdout.write(f"Delivery failures: {stats.delivery_failed}")
        self.stdout.write(f"Current delivery states for this file: {delivery_counts(import_run)}")

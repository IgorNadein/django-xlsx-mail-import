from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser
from openpyxl import Workbook


class Command(BaseCommand):
    help = "Generate a small XLSX file compatible with import_mailings."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("destination", type=Path)

    def handle(self, *args: Any, **options: Any) -> None:
        destination: Path = options["destination"]
        if destination.exists():
            raise CommandError(f"file already exists: {destination}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        workbook = Workbook()
        worksheet = workbook.active
        worksheet.title = "mailings"
        worksheet.append(("external_id", "user_id", "email", "subject", "message"))
        worksheet.append(("demo-001", 1, "alice@example.com", "Welcome", "Hello, Alice!"))
        worksheet.append(("demo-002", 2, "bob@example.com", "Update", "Hello, Bob!"))
        workbook.save(destination)
        self.stdout.write(self.style.SUCCESS(f"Created {destination}"))

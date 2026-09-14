from typing import Any

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Create local users with IDs 1, 2, and 3 for the sample workbook."

    def handle(self, *args: Any, **options: Any) -> None:
        user_model = get_user_model()
        created = 0
        for user_id in (1, 2, 3):
            _, was_created = user_model.objects.get_or_create(
                pk=user_id,
                defaults={"username": f"demo-user-{user_id}"},
            )
            created += int(was_created)
        self.stdout.write(self.style.SUCCESS(f"Demo users ready; created: {created}"))

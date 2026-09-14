import uuid

import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone

from mailings.models import ImportEntry, ImportRun, MailingRecord


@pytest.mark.django_db
@pytest.mark.parametrize("status", ["sent", "processing", "typo"])
def test_database_rejects_incomplete_or_unknown_delivery_state(status):
    user = get_user_model().objects.create_user(username="constraints")
    run = ImportRun.objects.create(source_filename="constraints.xlsx")
    record = MailingRecord.objects.create(
        external_id="constraints", user=user, email="a@example.com", subject="A", message="A", import_run=run
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        MailingRecord.objects.filter(pk=record.pk).update(status=status)


@pytest.mark.django_db(transaction=True)
def test_upgrade_preserves_old_jobs_and_marks_unowned_claim_uncertain():
    executor = MigrationExecutor(connection)
    latest = executor.loader.graph.leaf_nodes()
    old_target = [("mailings", "0001_initial")]
    executor.migrate(old_target)
    try:
        old_apps = executor.loader.project_state(old_target).apps
        OldUser = old_apps.get_model("auth", "User")
        OldRun = old_apps.get_model("mailings", "ImportRun")
        OldRecord = old_apps.get_model("mailings", "MailingRecord")
        user = OldUser.objects.create(username="upgrade")
        run_id = uuid.uuid4()
        OldRun.objects.create(id=run_id, source_filename="legacy.xlsx", status="completed", sent_count=1)
        sent_at = timezone.now()
        for state in ("sent", "processing", "pending"):
            OldRecord.objects.create(
                external_id=state,
                user_id=user.pk,
                email="a@example.com",
                subject="A",
                message="A",
                import_run_id=run_id,
                status=state,
                sent_at=sent_at if state == "sent" else None,
            )
        MigrationExecutor(connection).migrate(latest)
        run = ImportRun.objects.get(pk=run_id)
        assert run.status == "imported"
        assert run.sent_count == 1
        assert ImportEntry.objects.filter(import_run=run).count() == 3
        assert MailingRecord.objects.get(external_id="sent").sent_at == sent_at
        assert MailingRecord.objects.get(external_id="processing").status == "uncertain"
        assert MailingRecord.objects.get(external_id="pending").status == "pending"
    finally:
        MigrationExecutor(connection).migrate(latest)

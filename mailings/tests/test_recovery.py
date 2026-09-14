from datetime import timedelta
from io import StringIO
from unittest.mock import patch
from uuid import uuid4

import pytest
from django.contrib.auth import get_user_model
from django.core.management import CommandError, call_command
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from mailings.delivery import DeliveryNotSent, _claim_record, _eligible, _finish, deliver_import_run
from mailings.models import ImportEntry, ImportRun, MailingRecord
from mailings.services import import_xlsx
from mailings.tests.test_import_command import make_workbook

pytestmark = pytest.mark.django_db


@pytest.fixture
def source(tmp_path):
    user = get_user_model().objects.create_user(username="resume")
    path = tmp_path / "resume.xlsx"
    make_workbook(path, [(f"resume-{i}", user.pk, "test@example.com", "Subject", "Body") for i in range(3)])
    return path


def test_repeat_after_import_only_sends_pending_once(source):
    call_command("import_mailings", source, no_send=True, stdout=StringIO())
    with patch("mailings.delivery.send_email") as send:
        call_command("import_mailings", source, stdout=StringIO())
        call_command("import_mailings", source, stdout=StringIO())
    assert send.call_count == 3
    assert MailingRecord.objects.count() == 3
    assert ImportEntry.objects.count() == 9
    assert all(run.sent_count == 3 for run in ImportRun.objects.all())


def test_interrupt_during_delay_can_resume_without_replaying_sent(source):
    run, _ = import_xlsx(source)
    with (
        patch("mailings.delivery.sleep", side_effect=[None, KeyboardInterrupt]),
        pytest.raises(CommandError) as stopped,
    ):
        call_command("send_mailings", run=run.pk, stdout=StringIO())
    assert stopped.value.returncode == 130
    assert MailingRecord.objects.filter(status="sent").count() == 1
    assert MailingRecord.objects.filter(status="pending").count() == 2
    with patch("mailings.delivery.send_email") as send:
        call_command("import_mailings", source, stdout=StringIO())
    assert send.call_count == 2
    assert MailingRecord.objects.filter(status="sent").count() == 3


def test_unknown_send_result_requires_explicit_retry(source):
    run, _ = import_xlsx(source)
    with patch("mailings.delivery.send_email", side_effect=KeyboardInterrupt), pytest.raises(CommandError):
        call_command("send_mailings", run=run.pk, stdout=StringIO())
    with patch("mailings.delivery.send_email") as send:
        call_command("send_mailings", run=run.pk, stdout=StringIO())
        assert send.call_count == 2
        assert MailingRecord.objects.filter(status="uncertain").count() == 1
        call_command("send_mailings", run=run.pk, retry_uncertain=True, stdout=StringIO())
        assert send.call_count == 3
    assert MailingRecord.objects.filter(status="sent").count() == 3


def test_failed_delivery_retry_and_scope(source):
    run, _ = import_xlsx(source)
    other = ImportRun.objects.create(source_filename="other.xlsx")
    outsider = MailingRecord.objects.create(
        external_id="unrelated",
        user_id=MailingRecord.objects.first().user_id,
        email="other@example.com",
        subject="Other",
        message="Other",
        import_run=other,
    )
    ImportEntry.objects.create(import_run=other, record=outsider)
    with patch("mailings.delivery.send_email", side_effect=DeliveryNotSent("connection refused")):
        call_command("send_mailings", run=run.pk, limit=1, stdout=StringIO())
    with patch("mailings.delivery.send_email") as send:
        call_command("send_mailings", run=run.pk, stdout=StringIO())
        assert send.call_count == 2
        call_command("send_mailings", run=run.pk, retry_failed=True, stdout=StringIO())
        assert send.call_count == 3
    outsider.refresh_from_db()
    assert outsider.status == "pending"
    assert run.sent_count == 3


def test_stale_claim_recovery_requires_flag_and_preserves_active_claim(source):
    run, _ = import_xlsx(source)
    stale, active, pending = list(MailingRecord.objects.order_by("pk"))
    for record in (stale, active):
        record.status = "processing"
        record.claim_token = uuid4()
        record.claimed_at = timezone.now() - timedelta(minutes=10) if record == stale else timezone.now()
        record.save()
    with patch("mailings.delivery.send_email") as send:
        deliver_import_run(run)
        assert send.call_count == 1
        deliver_import_run(run, retry_uncertain=True)
        assert send.call_count == 2
    active.refresh_from_db()
    assert active.status == "processing"
    assert not _finish(stale, MailingRecord.Status.FAILED, "late worker")
    stale.refresh_from_db()
    assert stale.status == "sent"


def test_limit_sends_only_requested_number(source):
    run, _ = import_xlsx(source)
    with patch("mailings.delivery.send_email") as send:
        assert deliver_import_run(run, limit=1).attempted == 1
        assert deliver_import_run(run, limit=1).attempted == 1
        assert send.call_count == 2
    assert MailingRecord.objects.filter(status="pending").count() == 1


def test_huge_user_id_does_not_abort_next_valid_row(tmp_path):
    user = get_user_model().objects.create_user(username="range")
    path = tmp_path / "bounds.xlsx"
    make_workbook(
        path,
        [
            ("huge", "99999999999999999999999999999999", "a@example.com", "A", "A"),
            ("fine", user.pk, "a@example.com", "B", "B"),
        ],
    )
    run, stats = import_xlsx(path)
    assert (stats.processed, stats.created, stats.errors) == (2, 1, 1)
    assert run.error_count == 1


def test_claim_is_atomic_and_sleep_is_outside_transaction(source):
    run, _ = import_xlsx(source)
    record = MailingRecord.objects.first()
    query = _eligible(retry_failed=False, retry_uncertain=False)
    with CaptureQueriesContext(connection) as queries:
        claimed = _claim_record(record.pk, query)
    assert claimed is not None
    assert queries[0]["sql"].startswith("UPDATE")
    assert _claim_record(record.pk, query) is None


def test_failure_mid_import_retains_checkpoint_and_can_resume(source):
    from mailings.services import _persist_batch

    calls = 0

    def fail_second(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("file read stopped")
        return _persist_batch(*args, **kwargs)

    with patch("mailings.services._persist_batch", side_effect=fail_second), pytest.raises(OSError):
        import_xlsx(source, batch_size=1)
    first = ImportRun.objects.get()
    assert first.status == "failed"
    assert (first.processed_count, first.created_count) == (1, 1)
    with patch("mailings.delivery.send_email") as send:
        call_command("import_mailings", source, stdout=StringIO())
    assert send.call_count == 3
    assert MailingRecord.objects.filter(status="sent").count() == 3

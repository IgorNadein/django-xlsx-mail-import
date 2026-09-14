from __future__ import annotations

from io import StringIO
from pathlib import Path
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.core.management import CommandError, call_command
from openpyxl import Workbook

from mailings.delivery import DeliveryNotSent
from mailings.models import ImportRun, MailingRecord


def make_workbook(path: Path, rows: list[tuple[object, ...]], headers: tuple[str, ...] | None = None) -> None:
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.append(headers or ("external_id", "user_id", "email", "subject", "message"))
    for row in rows:
        worksheet.append(row)
    workbook.save(path)


@pytest.fixture
def user():
    return get_user_model().objects.create_user(username="alice", email="alice@example.com")


@pytest.mark.django_db
def test_imports_valid_rows_and_sends_only_created_records(tmp_path: Path, user) -> None:
    source = tmp_path / "mailings.xlsx"
    make_workbook(
        source,
        [
            ("ext-1", user.pk, "alice@example.com", "Welcome", "Hello"),
            ("ext-2", user.pk, "alice@example.com", "News", "Update"),
        ],
    )
    stdout = StringIO()

    with patch("mailings.delivery.send_email") as mocked_send:
        call_command("import_mailings", source, stdout=stdout)

    assert MailingRecord.objects.count() == 2
    assert MailingRecord.objects.filter(status=MailingRecord.Status.SENT).count() == 2
    assert mocked_send.call_count == 2
    assert "Processed rows: 2" in stdout.getvalue()
    assert "Created records: 2" in stdout.getvalue()
    assert "Sent messages: 2" in stdout.getvalue()
    run = ImportRun.objects.get()
    assert run.created_count == 2
    assert run.sent_count == 2


@pytest.mark.django_db
def test_repeated_file_is_idempotent(tmp_path: Path, user) -> None:
    source = tmp_path / "mailings.xlsx"
    make_workbook(source, [("ext-1", user.pk, "alice@example.com", "Welcome", "Hello")])

    with patch("mailings.delivery.send_email") as mocked_send:
        call_command("import_mailings", source)
        call_command("import_mailings", source)

    assert MailingRecord.objects.count() == 1
    assert mocked_send.call_count == 1
    second_run = ImportRun.objects.order_by("started_at").last()
    assert second_run is not None
    assert second_run.processed_count == 1
    assert second_run.created_count == 0
    assert second_run.skipped_count == 1


@pytest.mark.django_db
def test_reports_invalid_and_missing_user_rows_without_stopping(tmp_path: Path, user) -> None:
    source = tmp_path / "mailings.xlsx"
    make_workbook(
        source,
        [
            ("bad-email", user.pk, "not-an-email", "Subject", "Message"),
            ("missing-user", 999_999, "valid@example.com", "Subject", "Message"),
            ("valid", user.pk, "valid@example.com", "Subject", "Message"),
        ],
    )
    stderr = StringIO()

    call_command("import_mailings", source, "--no-send", "--batch-size", "2", stderr=stderr)

    assert MailingRecord.objects.values_list("external_id", flat=True).get() == "valid"
    run = ImportRun.objects.get()
    assert (run.processed_count, run.created_count, run.error_count) == (3, 1, 2)
    assert "Row 2: email is invalid" in stderr.getvalue()
    assert "does not exist" in stderr.getvalue()


@pytest.mark.django_db
def test_duplicate_inside_file_is_skipped(tmp_path: Path, user) -> None:
    source = tmp_path / "mailings.xlsx"
    make_workbook(
        source,
        [
            ("same", user.pk, "alice@example.com", "First", "Message"),
            ("same", user.pk, "alice@example.com", "Second", "Message"),
        ],
    )

    call_command("import_mailings", source, "--no-send", "--batch-size", "10")

    record = MailingRecord.objects.get()
    assert record.subject == "First"
    run = ImportRun.objects.get()
    assert (run.created_count, run.skipped_count) == (1, 1)


@pytest.mark.django_db
def test_duplicate_across_batches_is_skipped(tmp_path: Path, user) -> None:
    source = tmp_path / "mailings.xlsx"
    make_workbook(
        source,
        [
            ("same", user.pk, "alice@example.com", "First", "Message"),
            ("other", user.pk, "alice@example.com", "Other", "Message"),
            ("same", user.pk, "alice@example.com", "Repeated", "Message"),
        ],
    )

    call_command("import_mailings", source, "--no-send", "--batch-size", "2")

    run = ImportRun.objects.get()
    assert MailingRecord.objects.count() == 2
    assert (run.created_count, run.skipped_count) == (2, 1)


@pytest.mark.django_db
def test_no_send_leaves_records_pending(tmp_path: Path, user) -> None:
    source = tmp_path / "mailings.xlsx"
    make_workbook(source, [("ext-1", user.pk, "alice@example.com", "Welcome", "Hello")])

    with patch("mailings.delivery.send_email") as mocked_send:
        call_command("import_mailings", source, "--no-send")

    assert MailingRecord.objects.get().status == MailingRecord.Status.PENDING
    mocked_send.assert_not_called()


@pytest.mark.django_db
def test_delivery_failure_is_recorded_and_next_message_is_processed(tmp_path: Path, user) -> None:
    source = tmp_path / "mailings.xlsx"
    make_workbook(
        source,
        [
            ("ext-1", user.pk, "alice@example.com", "One", "First"),
            ("ext-2", user.pk, "alice@example.com", "Two", "Second"),
        ],
    )

    with patch("mailings.delivery.send_email", side_effect=[DeliveryNotSent("SMTP unavailable"), None]):
        call_command("import_mailings", source)

    failed = MailingRecord.objects.get(external_id="ext-1")
    sent = MailingRecord.objects.get(external_id="ext-2")
    assert failed.status == MailingRecord.Status.FAILED
    assert failed.last_error == "SMTP unavailable"
    assert sent.status == MailingRecord.Status.SENT
    run = ImportRun.objects.get()
    assert (run.sent_count, run.delivery_failed_count) == (1, 1)


@pytest.mark.django_db
def test_missing_header_aborts_without_creating_run(tmp_path: Path) -> None:
    source = tmp_path / "mailings.xlsx"
    make_workbook(source, [], headers=("external_id", "user_id", "email", "subject"))

    with pytest.raises(CommandError, match="missing required headers: message"):
        call_command("import_mailings", source)

    assert not ImportRun.objects.exists()


@pytest.mark.django_db
def test_corrupt_workbook_is_reported_as_command_error(tmp_path: Path) -> None:
    source = tmp_path / "mailings.xlsx"
    source.write_text("not an XLSX archive")

    with pytest.raises(CommandError, match="cannot read workbook"):
        call_command("import_mailings", source)

    assert not ImportRun.objects.exists()

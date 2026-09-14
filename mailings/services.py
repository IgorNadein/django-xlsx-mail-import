from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from random import randint
from time import sleep
from typing import Any

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.utils import timezone
from openpyxl import load_workbook
from openpyxl.worksheet.worksheet import Worksheet

from .models import ImportRun, MailingRecord

logger = logging.getLogger("mailings.delivery")
REQUIRED_HEADERS = ("external_id", "user_id", "email", "subject", "message")


class ImportFileError(ValueError):
    """Raised when a workbook cannot be imported at all."""


class RowValidationError(ValueError):
    """Raised for one malformed workbook row."""


@dataclass(frozen=True, slots=True)
class MailingCandidate:
    row_number: int
    external_id: str
    user_id: int
    email: str
    subject: str
    message: str


@dataclass(slots=True)
class ImportStats:
    processed: int = 0
    created: int = 0
    skipped: int = 0
    errors: int = 0
    sent: int = 0
    delivery_failed: int = 0


def _is_blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _required_text(value: Any, field: str, max_length: int | None = None) -> str:
    if _is_blank(value):
        raise RowValidationError(f"{field} is required")
    text = str(value).strip()
    if max_length is not None and len(text) > max_length:
        raise RowValidationError(f"{field} exceeds {max_length} characters")
    return text


def _external_id(value: Any) -> str:
    if isinstance(value, bool):
        raise RowValidationError("external_id must be a string or number")
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return _required_text(value, "external_id", 128)


def _user_id(value: Any) -> int:
    if isinstance(value, bool):
        raise RowValidationError("user_id must be a positive integer")
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise RowValidationError("user_id must be a positive integer") from exc
    if isinstance(value, float) and not value.is_integer():
        raise RowValidationError("user_id must be a positive integer")
    if number <= 0:
        raise RowValidationError("user_id must be a positive integer")
    return number


def _candidate(row_number: int, values: dict[str, Any]) -> MailingCandidate:
    email = _required_text(values["email"], "email", 254)
    try:
        validate_email(email)
    except ValidationError as exc:
        raise RowValidationError("email is invalid") from exc
    return MailingCandidate(
        row_number=row_number,
        external_id=_external_id(values["external_id"]),
        user_id=_user_id(values["user_id"]),
        email=email,
        subject=_required_text(values["subject"], "subject", 255),
        message=_required_text(values["message"], "message"),
    )


def _header_indexes(worksheet: Worksheet) -> dict[str, int]:
    header_row = next(worksheet.iter_rows(min_row=1, max_row=1, values_only=True), None)
    if header_row is None:
        raise ImportFileError("workbook is empty")
    indexes: dict[str, int] = {}
    for index, raw_name in enumerate(header_row):
        if _is_blank(raw_name):
            continue
        name = str(raw_name).strip().lower()
        if name in indexes:
            raise ImportFileError(f"duplicate header: {name}")
        indexes[name] = index
    missing = [name for name in REQUIRED_HEADERS if name not in indexes]
    if missing:
        raise ImportFileError(f"missing required headers: {', '.join(missing)}")
    return indexes


def _rows(worksheet: Worksheet, indexes: dict[str, int]) -> Iterator[tuple[int, dict[str, Any]]]:
    for row_number, row in enumerate(worksheet.iter_rows(min_row=2, values_only=True), start=2):
        if all(_is_blank(value) for value in row):
            continue
        yield row_number, {name: row[index] if index < len(row) else None for name, index in indexes.items()}


def _batches(source: Iterable[Any], size: int) -> Iterator[list[Any]]:
    batch: list[Any] = []
    for item in source:
        batch.append(item)
        if len(batch) == size:
            yield batch
            batch = []
    if batch:
        yield batch


def _persist_batch(
    rows: Sequence[tuple[int, dict[str, Any]]],
    import_run: ImportRun,
    stats: ImportStats,
    on_row_error: Callable[[int, str], None],
) -> None:
    candidates: list[MailingCandidate] = []
    for row_number, values in rows:
        stats.processed += 1
        try:
            candidates.append(_candidate(row_number, values))
        except RowValidationError as exc:
            stats.errors += 1
            on_row_error(row_number, str(exc))

    if not candidates:
        return

    external_ids = {item.external_id for item in candidates}
    existing_ids = set(MailingRecord.objects.filter(external_id__in=external_ids).values_list("external_id", flat=True))
    stats.skipped += sum(item.external_id in existing_ids for item in candidates)
    candidates = [item for item in candidates if item.external_id not in existing_ids]

    user_model = get_user_model()
    user_ids = set(user_model.objects.filter(pk__in={item.user_id for item in candidates}).values_list("pk", flat=True))
    unique_candidates: list[MailingCandidate] = []
    seen_ids: set[str] = set()
    for item in candidates:
        if item.user_id not in user_ids:
            stats.errors += 1
            on_row_error(item.row_number, f"user_id {item.user_id} does not exist")
        elif item.external_id in seen_ids:
            stats.skipped += 1
        else:
            seen_ids.add(item.external_id)
            unique_candidates.append(item)

    if not unique_candidates:
        return

    records = [
        MailingRecord(
            external_id=item.external_id,
            user_id=item.user_id,
            email=item.email,
            subject=item.subject,
            message=item.message,
            import_run=import_run,
        )
        for item in unique_candidates
    ]
    MailingRecord.objects.bulk_create(records, batch_size=len(records), ignore_conflicts=True)
    inserted = MailingRecord.objects.filter(
        import_run=import_run, external_id__in={item.external_id for item in unique_candidates}
    ).count()
    stats.created += inserted
    stats.skipped += len(records) - inserted


def import_xlsx(
    source: str | Path,
    *,
    batch_size: int = 500,
    on_row_error: Callable[[int, str], None] | None = None,
) -> tuple[ImportRun, ImportStats]:
    path = Path(source)
    if path.suffix.lower() != ".xlsx":
        raise ImportFileError("source file must have the .xlsx extension")
    if not path.is_file():
        raise ImportFileError(f"file does not exist: {path}")
    if batch_size <= 0:
        raise ImportFileError("batch size must be greater than zero")

    report_error = on_row_error or (lambda _row, _message: None)
    try:
        workbook = load_workbook(path, read_only=True, data_only=True)
    except Exception as exc:
        raise ImportFileError(f"cannot read workbook: {exc}") from exc
    import_run: ImportRun | None = None
    stats = ImportStats()
    try:
        worksheet = workbook.active
        indexes = _header_indexes(worksheet)
        import_run = ImportRun.objects.create(source_filename=path.name)
        for batch in _batches(_rows(worksheet, indexes), batch_size):
            _persist_batch(batch, import_run, stats, report_error)
        _complete_run(import_run, stats, ImportRun.Status.COMPLETED)
        return import_run, stats
    except Exception:
        if import_run is not None:
            _complete_run(import_run, stats, ImportRun.Status.FAILED)
        raise
    finally:
        workbook.close()


def _complete_run(import_run: ImportRun, stats: ImportStats, status: str) -> None:
    import_run.status = status
    import_run.finished_at = timezone.now()
    import_run.processed_count = stats.processed
    import_run.created_count = stats.created
    import_run.skipped_count = stats.skipped
    import_run.error_count = stats.errors
    import_run.sent_count = stats.sent
    import_run.delivery_failed_count = stats.delivery_failed
    import_run.save(
        update_fields=(
            "status",
            "finished_at",
            "processed_count",
            "created_count",
            "skipped_count",
            "error_count",
            "sent_count",
            "delivery_failed_count",
        )
    )


def send_email(email: str, subject: str, message: str) -> None:
    delay = randint(5, 20)
    sleep(delay)
    logger.info("Send EMAIL to=%s subject=%r message=%r delay=%ss", email, subject, message, delay)


def _claim_record(record_id: int) -> MailingRecord | None:
    with transaction.atomic():
        record = MailingRecord.objects.select_for_update().get(pk=record_id)
        if record.status != MailingRecord.Status.PENDING:
            return None
        record.status = MailingRecord.Status.PROCESSING
        record.save(update_fields=("status",))
        return record


def deliver_import_run(import_run: ImportRun, stats: ImportStats, *, chunk_size: int = 100) -> None:
    record_ids = (
        MailingRecord.objects.filter(import_run=import_run, status=MailingRecord.Status.PENDING)
        .order_by("pk")
        .values_list("pk", flat=True)
        .iterator(chunk_size=chunk_size)
    )
    for record_id in record_ids:
        record = _claim_record(record_id)
        if record is None:
            continue
        try:
            send_email(record.email, record.subject, record.message)
        except Exception as exc:  # delivery failures must not stop the remaining batch
            MailingRecord.objects.filter(pk=record.pk).update(
                status=MailingRecord.Status.FAILED,
                last_error=str(exc)[:2000],
            )
            stats.delivery_failed += 1
        else:
            MailingRecord.objects.filter(pk=record.pk).update(
                status=MailingRecord.Status.SENT,
                sent_at=timezone.now(),
                last_error="",
            )
            stats.sent += 1
    _complete_run(import_run, stats, ImportRun.Status.COMPLETED)

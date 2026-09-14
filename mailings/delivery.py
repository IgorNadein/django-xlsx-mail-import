from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import timedelta
from random import randint
from time import sleep

from django.db.models import Count, F, Q
from django.utils import timezone

from .models import ImportRun, MailingRecord

logger = logging.getLogger("mailings.delivery")
STALE_AFTER = timedelta(minutes=5)


class DeliveryNotSent(Exception):
    """The transport can prove it failed before the external side effect."""


class DeliveryCancelled(KeyboardInterrupt):
    """An interrupt during the simulated delay; no message was logged."""


@dataclass
class DeliveryStats:
    attempted: int = 0
    sent: int = 0
    failed: int = 0
    uncertain: int = 0


def send_email(email: str, subject: str, message: str) -> None:
    delay = randint(5, 20)
    try:
        sleep(delay)
    except KeyboardInterrupt as exc:
        raise DeliveryCancelled from exc
    except Exception as exc:
        raise DeliveryNotSent(str(exc)) from exc
    # Log only after the required wait. The real provider boundary would be here.
    logger.info("Send EMAIL to=%s subject=%r message=%r delay=%ss", email, subject, message, delay)


def delivery_counts(import_run: ImportRun) -> dict[str, int]:
    counts = dict.fromkeys(MailingRecord.Status.values, 0)
    counts.update(
        MailingRecord.objects.filter(entries__import_run=import_run)
        .values("status")
        .order_by()
        .annotate(amount=Count("pk"))
        .values_list("status", "amount")
    )
    return counts


def _eligible(*, retry_failed: bool, retry_uncertain: bool) -> Q:
    states = [MailingRecord.Status.PENDING]
    if retry_failed:
        states.append(MailingRecord.Status.FAILED)
    if retry_uncertain:
        states.append(MailingRecord.Status.UNCERTAIN)
    query = Q(status__in=states)
    if retry_uncertain:
        query |= Q(status=MailingRecord.Status.PROCESSING) & (
            Q(claimed_at__lte=timezone.now() - STALE_AFTER) | Q(claimed_at__isnull=True)
        )
    return query


def _claim_record(record_id: int, eligible: Q) -> MailingRecord | None:
    token = uuid.uuid4()
    # Compare-and-set is a single atomic UPDATE on both supported databases.
    # No transaction or row lock is held while waiting or logging.
    claimed = MailingRecord.objects.filter(eligible, pk=record_id).update(
        status=MailingRecord.Status.PROCESSING,
        claim_token=token,
        claimed_at=timezone.now(),
        attempts=F("attempts") + 1,
        last_error="",
    )
    if not claimed:
        return None
    return MailingRecord.objects.get(pk=record_id, claim_token=token)


def _finish(record: MailingRecord, status: str, error: str = "") -> bool:
    # An old process cannot overwrite the state of a later recovery attempt.
    return bool(
        MailingRecord.objects.filter(
            pk=record.pk,
            status=MailingRecord.Status.PROCESSING,
            claim_token=record.claim_token,
        ).update(
            status=status,
            last_error=error[:2000],
            claim_token=None,
            claimed_at=None,
            sent_at=timezone.now() if status == MailingRecord.Status.SENT else None,
        )
    )


def _deliver(record: MailingRecord, stats: DeliveryStats) -> None:
    stats.attempted += 1
    try:
        send_email(record.email, record.subject, record.message)
    except DeliveryCancelled:
        _finish(record, MailingRecord.Status.PENDING, "Stopped during delay; nothing was sent")
        raise
    except DeliveryNotSent as exc:
        _finish(record, MailingRecord.Status.FAILED, str(exc))
        stats.failed += 1
    except KeyboardInterrupt:
        _finish(record, MailingRecord.Status.UNCERTAIN, "Interrupted at the delivery boundary; verify before retry")
        raise
    except Exception as exc:
        _finish(record, MailingRecord.Status.UNCERTAIN, str(exc))
        stats.uncertain += 1
    else:
        if _finish(record, MailingRecord.Status.SENT):
            stats.sent += 1
        else:
            stats.uncertain += 1
            logger.error("Delivery claim lost for external_id=%s; manual reconciliation needed", record.external_id)


def deliver_import_run(
    import_run: ImportRun,
    *,
    retry_failed: bool = False,
    retry_uncertain: bool = False,
    limit: int | None = None,
    chunk_size: int = 100,
) -> DeliveryStats:
    if limit is not None and limit < 1:
        raise ValueError("limit must be positive")
    if not 1 <= chunk_size <= 1000:
        raise ValueError("chunk size must be between 1 and 1000")
    stats = DeliveryStats()
    eligible = _eligible(retry_failed=retry_failed, retry_uncertain=retry_uncertain)
    cursor = 0
    while limit is None or stats.attempted < limit:
        size = chunk_size if limit is None else min(chunk_size, limit - stats.attempted)
        ids = list(
            MailingRecord.objects.filter(
                eligible,
                entries__import_run=import_run,
                pk__gt=cursor,
            )
            .order_by("pk")
            .values_list("pk", flat=True)[:size]
        )
        if not ids:
            break
        for record_id in ids:
            record = _claim_record(record_id, eligible)
            if record is not None:
                _deliver(record, stats)
        cursor = ids[-1]
    return stats

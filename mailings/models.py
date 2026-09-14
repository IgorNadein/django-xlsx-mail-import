import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q


class ImportRun(models.Model):
    class Status(models.TextChoices):
        RUNNING = "running", "Running"
        IMPORTED = "imported", "Import finished"
        FAILED = "failed", "Failed"
        INTERRUPTED = "interrupted", "Interrupted"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    source_filename = models.CharField(max_length=255)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.RUNNING)
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    processed_count = models.PositiveIntegerField(default=0)
    created_count = models.PositiveIntegerField(default=0)
    skipped_count = models.PositiveIntegerField(default=0)
    error_count = models.PositiveIntegerField(default=0)

    @property
    def sent_count(self) -> int:
        return self.entries.filter(record__status=MailingRecord.Status.SENT).count()

    @property
    def delivery_failed_count(self) -> int:
        return self.entries.filter(record__status=MailingRecord.Status.FAILED).count()

    class Meta:
        ordering = ("-started_at",)

    def __str__(self) -> str:
        return f"{self.source_filename} ({self.started_at:%Y-%m-%d %H:%M})"


class MailingRecord(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        PROCESSING = "processing", "Processing"
        SENT = "sent", "Sent"
        FAILED = "failed", "Failed"
        UNCERTAIN = "uncertain", "Outcome unknown"

    external_id = models.CharField(max_length=128, unique=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="mailing_records")
    email = models.EmailField()
    subject = models.CharField(max_length=255)
    message = models.TextField()
    import_run = models.ForeignKey(ImportRun, on_delete=models.PROTECT, related_name="records")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING, db_index=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    attempts = models.PositiveIntegerField(default=0)
    claim_token = models.UUIDField(null=True, editable=False)
    claimed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("id",)
        indexes = [models.Index(fields=("import_run", "status"), name="mail_run_status_idx")]
        constraints = [
            models.CheckConstraint(
                condition=Q(status__in=("pending", "processing", "sent", "failed", "uncertain")),
                name="mail_valid_status",
            ),
            models.CheckConstraint(
                condition=(Q(status="sent", sent_at__isnull=False) | (~Q(status="sent") & Q(sent_at__isnull=True))),
                name="mail_sent_timestamp",
            ),
            models.CheckConstraint(
                condition=(
                    Q(status="processing", claim_token__isnull=False, claimed_at__isnull=False)
                    | (~Q(status="processing") & Q(claim_token__isnull=True, claimed_at__isnull=True))
                ),
                name="mail_processing_claim",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.external_id}: {self.email}"


class ImportEntry(models.Model):
    """Membership also includes duplicates, allowing a file to resume its own jobs."""

    import_run = models.ForeignKey(ImportRun, on_delete=models.CASCADE, related_name="entries")
    record = models.ForeignKey(MailingRecord, on_delete=models.PROTECT, related_name="entries")

    class Meta:
        constraints = [models.UniqueConstraint(fields=("import_run", "record"), name="unique_import_record")]

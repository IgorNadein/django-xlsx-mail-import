import uuid

from django.conf import settings
from django.db import models


class ImportRun(models.Model):
    class Status(models.TextChoices):
        RUNNING = "running", "Running"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    source_filename = models.CharField(max_length=255)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.RUNNING)
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    processed_count = models.PositiveIntegerField(default=0)
    created_count = models.PositiveIntegerField(default=0)
    skipped_count = models.PositiveIntegerField(default=0)
    error_count = models.PositiveIntegerField(default=0)
    sent_count = models.PositiveIntegerField(default=0)
    delivery_failed_count = models.PositiveIntegerField(default=0)

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

    class Meta:
        ordering = ("id",)
        indexes = [models.Index(fields=("import_run", "status"), name="mail_run_status_idx")]

    def __str__(self) -> str:
        return f"{self.external_id}: {self.email}"

from django.contrib import admin
from django.db.models import Model
from django.http import HttpRequest

from .models import ImportRun, MailingRecord


class AuditAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj: Model | None = None) -> bool:
        return False


@admin.register(ImportRun)
class ImportRunAdmin(AuditAdmin):
    list_display = (
        "source_filename",
        "status",
        "started_at",
        "processed_count",
        "created_count",
        "error_count",
        "sent_count",
    )
    list_filter = ("status", "started_at")
    search_fields = ("source_filename",)
    readonly_fields = (*tuple(field.name for field in ImportRun._meta.fields), "sent_count", "delivery_failed_count")


@admin.register(MailingRecord)
class MailingRecordAdmin(AuditAdmin):
    list_display = ("external_id", "email", "user", "status", "created_at", "sent_at")
    list_filter = ("status", "created_at")
    search_fields = ("external_id", "email", "subject")
    readonly_fields = tuple(field.name for field in MailingRecord._meta.fields)

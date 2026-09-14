from django.contrib import admin

from .models import ImportRun, MailingRecord


@admin.register(ImportRun)
class ImportRunAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
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
    readonly_fields = tuple(field.name for field in ImportRun._meta.fields)


@admin.register(MailingRecord)
class MailingRecordAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("external_id", "email", "user", "status", "created_at", "sent_at")
    list_filter = ("status", "created_at")
    search_fields = ("external_id", "email", "subject")
    readonly_fields = tuple(field.name for field in MailingRecord._meta.fields)

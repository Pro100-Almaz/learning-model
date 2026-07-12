from django.contrib import admin

from apps.generation.models import GenerationJob, GenerationStep


class GenerationStepInline(admin.TabularInline):
    model = GenerationStep
    extra = 0
    fields = ("created_at", "question_index", "kind", "status", "message", "question")
    readonly_fields = fields
    can_delete = False
    show_change_link = True


@admin.register(GenerationJob)
class GenerationJobAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "topic",
        "count",
        "status",
        "user",
        "created_count",
        "skipped_count",
        "failed_count",
        "created_at",
    )
    list_filter = ("status", "topic")
    search_fields = ("topic", "user__email", "celery_task_id")
    readonly_fields = (
        "celery_task_id",
        "created_at",
        "started_at",
        "finished_at",
        "created_count",
        "skipped_count",
        "failed_count",
        "error",
    )
    inlines = [GenerationStepInline]


@admin.register(GenerationStep)
class GenerationStepAdmin(admin.ModelAdmin):
    list_display = ("id", "job", "question_index", "kind", "status", "created_at")
    list_filter = ("kind", "status")
    search_fields = ("message", "job__topic")
    readonly_fields = ("created_at",)

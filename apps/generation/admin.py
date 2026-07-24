from django import forms
from django.contrib import admin, messages

from agents_and_engine.math_engine import (
    DIFFICULTY_BY_TARGET,
    PROFILE_SUBJECT_MAX_SCORE,
    available_topics,
)
from apps.generation.models import GenerationJob, GenerationStep

_DIFF_LABELS = {1: "easy", 2: "medium", 3: "hard"}


def _target_score_help() -> str:
    """Build the target_score help text from the real difficulty thresholds.

    Derived from ``DIFFICULTY_BY_TARGET`` (source of truth in math_engine) so
    the wording can never drift out of sync with the actual bands. Produces
    e.g. "28-40 -> hard; 18-27 -> medium; 0-17 -> easy".
    """
    bands = sorted(DIFFICULTY_BY_TARGET, key=lambda pair: pair[0], reverse=True)
    parts: list[str] = []
    upper = PROFILE_SUBJECT_MAX_SCORE
    for threshold, level in bands:
        label = _DIFF_LABELS.get(level, f"level {level}")
        parts.append(f"{threshold}–{upper} → {label}")
        upper = threshold - 1
    return (
        "Intended score for профильная математика "
        f"(0–{PROFILE_SUBJECT_MAX_SCORE}) — NOT the ЕНТ total. "
        "Higher score → harder questions. "
        f"Bands: {'; '.join(parts)}. "
        "Leave blank to use the topic’s default difficulty."
    )


class GenerationJobAdminForm(forms.ModelForm):
    """Add-form for a generation batch: only the inputs, with rich help text."""

    class Meta:
        model = GenerationJob
        fields = ("topic", "count", "target_score", "language")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if "topic" in self.fields:
            self.fields["topic"] = forms.ChoiceField(
                choices=[(t, t) for t in available_topics()],
                help_text="Math topic to generate. Each maps to a blueprint spec.",
            )
        if "count" in self.fields:
            self.fields["count"] = forms.IntegerField(
                min_value=1,
                max_value=20,
                help_text="How many questions to generate in this batch (1–20).",
            )
        if "target_score" in self.fields:
            self.fields["target_score"] = forms.IntegerField(
                required=False,
                min_value=0,
                max_value=PROFILE_SUBJECT_MAX_SCORE,
                help_text=_target_score_help(),
            )
        if "language" in self.fields:
            self.fields["language"].help_text = (
                "Output language for every question in this batch."
            )


class GenerationStepInline(admin.TabularInline):
    model = GenerationStep
    extra = 0
    fields = ("created_at", "question_index", "kind", "status", "message", "question")
    readonly_fields = fields
    can_delete = False
    show_change_link = True


@admin.register(GenerationJob)
class GenerationJobAdmin(admin.ModelAdmin):
    form = GenerationJobAdminForm

    list_display = (
        "id",
        "topic",
        "count",
        "target_score",
        "language",
        "status",
        "user",
        "created_count",
        "skipped_count",
        "failed_count",
        "created_at",
    )
    list_filter = ("status", "language", "topic")
    search_fields = ("topic", "user__email", "celery_task_id")

    _RESULT_FIELDS = (
        "status",
        "user",
        "celery_task_id",
        "created_at",
        "started_at",
        "finished_at",
        "created_count",
        "skipped_count",
        "failed_count",
        "error",
    )
    _INPUT_FIELDS = ("topic", "count", "target_score", "language")

    add_fieldsets = (
        (
            None,
            {
                "fields": _INPUT_FIELDS,
                "description": (
                    "Fill in the parameters and save to start a generation batch. "
                    "It runs in the background (Celery worker): the Architect → "
                    "Storyteller → Critic → Publisher pipeline runs once per "
                    "question. Reopen this job to watch the steps and the "
                    "created / skipped / failed counts."
                ),
            },
        ),
    )
    change_fieldsets = (
        ("Request", {"fields": ("topic", "count", "target_score", "language", "user")}),
        (
            "Result",
            {
                "fields": (
                    "status",
                    "created_count",
                    "skipped_count",
                    "failed_count",
                    "error",
                )
            },
        ),
        (
            "Execution",
            {"fields": ("celery_task_id", "created_at", "started_at", "finished_at")},
        ),
    )

    inlines = [GenerationStepInline]

    def get_inline_instances(self, request, obj=None):
        if obj is None:
            return []
        return super().get_inline_instances(request, obj)

    def get_fieldsets(self, request, obj=None):
        return self.add_fieldsets if obj is None else self.change_fieldsets

    def get_readonly_fields(self, request, obj=None):
        if obj is None:
            return self._RESULT_FIELDS
        return self._RESULT_FIELDS + self._INPUT_FIELDS

    def save_model(self, request, obj, form, change):
        if not change and not obj.user_id:
            obj.user = request.user
        super().save_model(request, obj, form, change)
        if not change:
            # Lazy import --> apps.generation.tasks pulls in the heavy LangGraph stack, which
            # the admin process shouldn't pay for until a job is actually run.
            from apps.generation import tasks

            async_result = tasks.run_generation_job.delay(obj.pk)
            obj.celery_task_id = async_result.id or ""
            obj.save(update_fields=["celery_task_id"])
            self.message_user(
                request,
                f"Generation started for “{obj.topic}” ×{obj.count} "
                f"({obj.get_language_display()}). Refresh this page to watch progress.",
                level=messages.SUCCESS,
            )


@admin.register(GenerationStep)
class GenerationStepAdmin(admin.ModelAdmin):
    list_display = ("id", "job", "question_index", "kind", "status", "created_at")
    list_filter = ("kind", "status")
    search_fields = ("message", "job__topic")
    readonly_fields = ("created_at",)

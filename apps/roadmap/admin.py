from collections import Counter

from django.contrib import admin

from apps.roadmap.models import ChapterLadderSession, Roadmap, RoadmapItem


class RoadmapItemInline(admin.TabularInline):
    model = RoadmapItem
    extra = 0
    fields = ("order", "lesson", "micro_test", "weak_tag", "rationale", "status", "completed_at")
    readonly_fields = ("completed_at",)
    autocomplete_fields = ("lesson", "micro_test", "weak_tag")


@admin.register(Roadmap)
class RoadmapAdmin(admin.ModelAdmin):
    list_display = ("id", "student", "source", "is_active", "created_at")
    list_filter = ("is_active", "source")
    search_fields = ("student__email",)
    autocomplete_fields = ("student", "source_attempt")
    inlines = [RoadmapItemInline]


@admin.register(RoadmapItem)
class RoadmapItemAdmin(admin.ModelAdmin):
    list_display = ("id", "roadmap", "order", "lesson", "status", "completed_at")
    list_filter = ("status",)
    autocomplete_fields = ("roadmap", "lesson", "micro_test", "weak_tag")


@admin.register(ChapterLadderSession)
class ChapterLadderSessionAdmin(admin.ModelAdmin):
    """Read-only view of a placement so support/content can see *why* a student
    was placed where they were. Sessions are server-managed — never hand-edited."""

    list_display = ("id", "student", "module", "is_complete", "verdict_summary", "created_at")
    list_filter = ("is_complete", "module")
    search_fields = ("student__email",)
    readonly_fields = (
        "student",
        "module",
        "attempt",
        "is_complete",
        "verdict_summary",
        "state",
        "created_at",
        "updated_at",
    )

    @admin.display(description="verdicts")
    def verdict_summary(self, obj: ChapterLadderSession) -> str:
        per_topic = (obj.state or {}).get("per_topic", {})
        counts = Counter(st.get("verdict") for st in per_topic.values())
        return ", ".join(f"{k or '—'}:{v}" for k, v in counts.items()) or "—"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

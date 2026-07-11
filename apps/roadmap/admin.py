from django.contrib import admin

from apps.roadmap.models import Roadmap, RoadmapItem


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

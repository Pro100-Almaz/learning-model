from django.contrib import admin

from apps.gamification.models import Streak, StudentProgress, XPEvent


@admin.register(XPEvent)
class XPEventAdmin(admin.ModelAdmin):
    list_display = ("id", "student", "amount", "reason", "created_at")
    list_filter = ("reason",)
    search_fields = ("student__email",)
    autocomplete_fields = ("student",)
    readonly_fields = ("created_at",)


@admin.register(StudentProgress)
class StudentProgressAdmin(admin.ModelAdmin):
    list_display = ("id", "student", "total_xp", "level_code")
    list_filter = ("level_code",)
    search_fields = ("student__email",)
    autocomplete_fields = ("student",)


@admin.register(Streak)
class StreakAdmin(admin.ModelAdmin):
    list_display = ("id", "student", "current_streak", "longest_streak", "last_active_date")
    search_fields = ("student__email",)
    autocomplete_fields = ("student",)

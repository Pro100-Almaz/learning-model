from django.contrib import admin

from .models import Lesson, Module, Tag


@admin.register(Module)
class ModuleAdmin(admin.ModelAdmin):
    list_display = ("id", "title", "slug", "subject", "order")
    list_filter = ("subject",)
    search_fields = ("title", "slug")
    ordering = ("order", "id")
    prepopulated_fields = {"slug": ("title",)}


@admin.register(Lesson)
class LessonAdmin(admin.ModelAdmin):
    list_display = ("id", "title", "module", "video_provider", "duration_sec", "order")
    list_filter = ("video_provider", "module")
    search_fields = ("title", "description")
    ordering = ("module", "order", "id")
    list_select_related = ("module",)
    autocomplete_fields = ("module",)


@admin.register(Tag)
class TagAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "slug")
    search_fields = ("name", "slug")
    ordering = ("name",)
    prepopulated_fields = {"slug": ("name",)}

from django.contrib import admin

from apps.content.models import ClassGrade, Lesson, Module, Subject, Tag


@admin.register(Subject)
class SubjectAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "slug")
    search_fields = ("name", "slug")
    ordering = ("name",)
    prepopulated_fields = {"slug": ("name",)}


@admin.register(ClassGrade)
class ClassGradeAdmin(admin.ModelAdmin):
    list_display = ("id", "grade", "subject")
    list_filter = ("subject",)
    search_fields = ("grade", "subject__name", "subject__slug")
    ordering = ("subject__name", "grade")
    list_select_related = ("subject",)
    autocomplete_fields = ("subject",)


@admin.register(Module)
class ModuleAdmin(admin.ModelAdmin):
    list_display = ("id", "title", "slug", "subject", "class_grade", "order")
    list_filter = ("class_grade__subject", "class_grade__grade")
    search_fields = ("title", "slug")
    ordering = ("order", "id")
    list_select_related = ("class_grade__subject",)
    autocomplete_fields = ("class_grade",)
    prepopulated_fields = {"slug": ("title",)}

    @admin.display(description="Subject", ordering="class_grade__subject")
    def subject(self, obj):
        return obj.class_grade.subject


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

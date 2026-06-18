from django.contrib import admin
from import_export.admin import ImportExportMixin

from .models import GrantThreshold, Specialty, University
from .resources import UniversityImportResource


class SpecialtyInline(admin.TabularInline):
    model = Specialty
    extra = 0
    fields = ("code", "name", "required_subjects")


class GrantThresholdInline(admin.TabularInline):
    model = GrantThreshold
    extra = 0
    fields = ("year", "min_score")


@admin.register(University)
class UniversityAdmin(ImportExportMixin, admin.ModelAdmin):
    list_display = ("code", "name", "city")
    search_fields = ("code", "name", "city")
    ordering = ("name",)
    inlines = [SpecialtyInline]
    # The bulk universities Excel is imported here.
    resource_classes = [UniversityImportResource]


@admin.register(Specialty)
class SpecialtyAdmin(ImportExportMixin, admin.ModelAdmin):
    list_display = ("code", "name", "university")
    list_select_related = ("university",)
    search_fields = ("code", "name", "university__name", "university__code")
    list_filter = ("university",)
    inlines = [GrantThresholdInline]
    resource_classes = [UniversityImportResource]


@admin.register(GrantThreshold)
class GrantThresholdAdmin(ImportExportMixin, admin.ModelAdmin):
    list_display = ("specialty", "year", "min_score")
    list_select_related = ("specialty", "specialty__university")
    list_filter = ("year",)
    search_fields = (
        "specialty__name",
        "specialty__code",
        "specialty__university__name",
    )
    resource_classes = [UniversityImportResource]

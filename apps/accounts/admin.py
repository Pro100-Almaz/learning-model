"""Django admin registration for the accounts app."""

from django.contrib import admin

from .models import ExpectedScore, StudentProfile


class ExpectedScoreInline(admin.TabularInline):
    model = ExpectedScore
    extra = 0


@admin.register(StudentProfile)
class StudentProfileAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "user",
        "target_university",
        "target_specialty",
        "target_score",
        "onboarding_completed",
    )
    list_filter = ("onboarding_completed",)
    search_fields = ("user__email",)
    autocomplete_fields = ()
    raw_id_fields = ("user", "target_university", "target_specialty")
    inlines = [ExpectedScoreInline]


@admin.register(ExpectedScore)
class ExpectedScoreAdmin(admin.ModelAdmin):
    list_display = ("id", "profile", "subject", "score")
    list_filter = ("subject",)
    search_fields = ("subject", "profile__user__email")

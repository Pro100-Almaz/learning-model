"""Django admin registration for the accounts app."""

from django.contrib import admin

from apps.accounts.models import ExpectedScore, StudentProfile


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
    list_filter = ("onboarding_completed", "subjects")
    search_fields = ("user__email",)
    autocomplete_fields = ()
    raw_id_fields = ("user", "target_university", "target_specialty")
    filter_horizontal = ("subjects",)
    inlines = [ExpectedScoreInline]


@admin.register(ExpectedScore)
class ExpectedScoreAdmin(admin.ModelAdmin):
    list_display = ("id", "profile", "subject", "score")
    list_filter = ("subject",)
    search_fields = ("subject__name", "subject__slug", "profile__user__email")
    list_select_related = ("subject", "profile")
    autocomplete_fields = ("subject",)

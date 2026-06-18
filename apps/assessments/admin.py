"""Admin for assessments.

Editorial invariants enforced here:
    * Each Question must have >= 1 tag.
    * Each Question must have exactly one correct AnswerOption.
"""

from django import forms
from django.contrib import admin
from django.core.exceptions import ValidationError
from django.forms.models import BaseInlineFormSet

from .models import (
    AnswerOption,
    AttemptAnswer,
    Question,
    Test,
    TestAttempt,
    TestQuestion,
)


# ---------------------------------------------------------------------------
# Question + AnswerOption
# ---------------------------------------------------------------------------


class AnswerOptionInlineFormSet(BaseInlineFormSet):
    """Require exactly one correct option among non-deleted, saved options."""

    def clean(self):
        super().clean()
        if any(self.errors):
            return

        correct_count = 0
        live_count = 0
        for form in self.forms:
            if not form.cleaned_data:
                continue
            if form.cleaned_data.get("DELETE"):
                continue
            live_count += 1
            if form.cleaned_data.get("is_correct"):
                correct_count += 1

        if live_count == 0:
            raise ValidationError("A question must have at least one option.")
        if correct_count != 1:
            raise ValidationError(
                "A question must have exactly one correct option "
                f"(found {correct_count})."
            )


class AnswerOptionInline(admin.TabularInline):
    model = AnswerOption
    extra = 4
    min_num = 2
    formset = AnswerOptionInlineFormSet
    fields = ("text", "is_correct")


class QuestionAdminForm(forms.ModelForm):
    class Meta:
        model = Question
        fields = "__all__"

    def clean(self):
        cleaned = super().clean()
        tags = cleaned.get("tags")
        # ManyToMany on add-form: queryset is empty for unsaved objects,
        # but Django still passes a list-like through cleaned_data.
        if tags is not None:
            try:
                count = tags.count() if hasattr(tags, "count") else len(tags)
            except TypeError:
                count = 0
            if count < 1:
                raise ValidationError("A question must have at least one tag.")
        return cleaned


@admin.register(Question)
class QuestionAdmin(admin.ModelAdmin):
    form = QuestionAdminForm
    list_display = ("id", "short_text", "difficulty", "lesson")
    list_filter = ("difficulty", "tags", "lesson")
    search_fields = ("text", "explanation")
    filter_horizontal = ("tags",)
    inlines = [AnswerOptionInline]

    @admin.display(description="Question")
    def short_text(self, obj: Question) -> str:
        return (obj.text or "")[:80]


# ---------------------------------------------------------------------------
# Test + TestQuestion
# ---------------------------------------------------------------------------


class TestQuestionInline(admin.TabularInline):
    model = TestQuestion
    extra = 1
    autocomplete_fields = ("question",)
    fields = ("order", "question")
    ordering = ("order",)


@admin.register(Test)
class TestAdmin(admin.ModelAdmin):
    list_display = ("id", "type", "title", "lesson", "time_limit_sec")
    list_filter = ("type", "lesson")
    search_fields = ("title",)
    inlines = [TestQuestionInline]


# ---------------------------------------------------------------------------
# Attempts (read-mostly diagnostics)
# ---------------------------------------------------------------------------


class AttemptAnswerInline(admin.TabularInline):
    model = AttemptAnswer
    extra = 0
    readonly_fields = ("question", "selected_option", "is_correct")
    can_delete = False


@admin.register(TestAttempt)
class TestAttemptAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "student",
        "test",
        "score",
        "is_completed",
        "started_at",
        "finished_at",
    )
    list_filter = ("is_completed", "test__type")
    search_fields = ("student__email", "test__title")
    readonly_fields = ("started_at", "finished_at")
    inlines = [AttemptAnswerInline]


@admin.register(AttemptAnswer)
class AttemptAnswerAdmin(admin.ModelAdmin):
    list_display = ("id", "attempt", "question", "selected_option", "is_correct")
    list_filter = ("is_correct",)
    search_fields = ("attempt__student__email",)

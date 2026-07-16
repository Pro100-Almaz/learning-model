"""Admin validation invariants: tag count and one-correct-option rule."""

import pytest
from django.contrib.admin.sites import AdminSite
from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory

from apps.assessments.admin import (
    AnswerOptionInline,
    AnswerOptionInlineFormSet,
    QuestionAdmin,
    QuestionAdminForm,
)
from apps.assessments.models import Question
from apps.content.models import Tag


def _fake_request():
    """Build a request-like object satisfying admin's permission checks."""
    request = RequestFactory().get("/")

    class _SuperUser(AnonymousUser):
        is_active = True
        is_staff = True
        is_superuser = True

        def has_perm(self, perm, obj=None):
            return True

        def has_module_perms(self, app_label):
            return True

    request.user = _SuperUser()
    return request


pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# AnswerOption inline formset
# ---------------------------------------------------------------------------


def _make_question():
    return Question.objects.create(text="Q?", explanation="x")


def _formset(question, rows):
    """Build a bound formset instance with the given rows."""
    prefix = "options"
    data = {
        f"{prefix}-TOTAL_FORMS": str(len(rows)),
        f"{prefix}-INITIAL_FORMS": "0",
        f"{prefix}-MIN_NUM_FORMS": "0",
        f"{prefix}-MAX_NUM_FORMS": "1000",
    }
    for i, row in enumerate(rows):
        for k, v in row.items():
            data[f"{prefix}-{i}-{k}"] = v
    inline = AnswerOptionInline(parent_model=Question, admin_site=AdminSite())
    FormSetClass = inline.get_formset(request=_fake_request())
    return FormSetClass(data=data, instance=question)


def test_inline_rejects_zero_correct_options():
    question = _make_question()
    fs = _formset(
        question,
        [
            {"text": "A", "is_correct": ""},
            {"text": "B", "is_correct": ""},
        ],
    )
    assert fs.is_valid() is False
    assert any(
        "exactly one correct option" in str(e) for e in fs.non_form_errors()
    )


def test_inline_rejects_two_correct_options():
    question = _make_question()
    fs = _formset(
        question,
        [
            {"text": "A", "is_correct": "on"},
            {"text": "B", "is_correct": "on"},
        ],
    )
    assert fs.is_valid() is False
    assert any(
        "exactly one correct option" in str(e) for e in fs.non_form_errors()
    )


def test_inline_accepts_exactly_one_correct():
    question = _make_question()
    fs = _formset(
        question,
        [
            {"text": "A", "is_correct": "on"},
            {"text": "B", "is_correct": ""},
        ],
    )
    assert fs.is_valid() is True


def test_inline_uses_correct_formset_class():
    # Guards against regressions where the custom formset gets dropped.
    inline = AnswerOptionInline(parent_model=Question, admin_site=AdminSite())
    FormSetClass = inline.get_formset(request=_fake_request())
    assert issubclass(FormSetClass, AnswerOptionInlineFormSet)


# ---------------------------------------------------------------------------
# Question form: at least one tag
# ---------------------------------------------------------------------------


def test_question_form_rejects_no_tags():
    form = QuestionAdminForm(
        data={
            "text": "Question?",
            "explanation": "because",
            "difficulty": "1",
            "tags": [],  # 0 tags
        }
    )
    assert form.is_valid() is False
    # Non-field errors carry our message
    errors = form.non_field_errors()
    assert any("at least one tag" in str(e) for e in errors) or "tags" in form.errors


def test_question_form_accepts_one_or_more_tags():
    tag = Tag.objects.create(name="Trig", slug="trig")
    form = QuestionAdminForm(
        data={
            "text": "Question?",
            "explanation": "because",
            "difficulty": "1",
            "language": "russian",
            "tags": [tag.pk],
        }
    )
    assert form.is_valid() is True, form.errors


# ---------------------------------------------------------------------------
# Sanity: registry wiring
# ---------------------------------------------------------------------------


def test_question_admin_uses_custom_form_and_inline():
    assert QuestionAdmin.form is QuestionAdminForm
    assert any(
        issubclass(inline, AnswerOptionInline) for inline in QuestionAdmin.inlines
    )

"""Translation registrations for the careers app (django-modeltranslation).

Human-readable names shown to the student are localized. Institution/specialty
codes and the required_subjects payload are language-neutral identifiers and
are left untranslated; scores/years are numeric.
"""

from modeltranslation.translator import TranslationOptions, register

from .models import Specialty, University


@register(University)
class UniversityTranslationOptions(TranslationOptions):
    fields = ("name", "city")


@register(Specialty)
class SpecialtyTranslationOptions(TranslationOptions):
    fields = ("name",)

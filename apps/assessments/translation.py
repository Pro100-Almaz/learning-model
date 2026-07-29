"""Translation registrations for the assessments app (django-modeltranslation).

Only ``Test.title`` (an authored, user-facing label) is registered.

Deliberately NOT registered — these are already language-aware by a different
mechanism, so modeltranslation columns would be redundant or wrong:

* ``Question`` / ``AnswerOption`` — stored one row per language (see
  ``Question.language`` and ``config.SUPPORTED_LANGUAGES``); each language is a
  separately generated row, not a translation of a shared row.
* ``TutorNote.note`` — cached LLM feedback generated in the question's own
  language; it is per-(question, option) generated content, not authored text
  to be translated into every language.
"""

from modeltranslation.translator import TranslationOptions, register

from .models import Test


@register(Test)
class TestTranslationOptions(TranslationOptions):
    fields = ("title",)

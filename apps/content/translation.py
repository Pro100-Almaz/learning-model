"""Translation registrations for the content app (django-modeltranslation).

Only fields shown to the student in their own language are registered. Numeric
fields (order, duration_sec, grade), slugs, and enum/choice codes are left
alone — they are language-neutral. The one non-text field here is Lesson's
video, which IS localized: a Kazakh lesson points at a different video (and
possibly a different provider) than the Russian one, so both video_url and
video_provider are translated to keep the embed coherent per language.
"""

from modeltranslation.translator import TranslationOptions, register

from .models import Lesson, Module, Subject, Tag


@register(Subject)
class SubjectTranslationOptions(TranslationOptions):
    fields = ("name",)


@register(Module)
class ModuleTranslationOptions(TranslationOptions):
    fields = ("title", "description")


@register(Lesson)
class LessonTranslationOptions(TranslationOptions):
    fields = ("title", "description", "video_url", "video_provider")


@register(Tag)
class TagTranslationOptions(TranslationOptions):
    fields = ("name", "description")

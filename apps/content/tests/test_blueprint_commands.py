"""Folder-driven curriculum ingestion and safe cleanup."""

import json
import tempfile
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

import config
from apps.assessments.models import (
    AttemptAnswer,
    Question,
    TestAttempt,
)
from apps.assessments.models import (
    Test as AssessmentTest,
)
from apps.content.models import ClassGrade, Lesson, Module, Subject, Tag
from apps.generation.admin import GenerationJobAdminForm
from apps.generation.models import HiddenBlueprintTopic


class BlueprintCommandTests(TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.settings_override = override_settings(BASE_DIR=self.root)
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        self.folder = self.root / "agents_and_engine" / "qadam_blueprints"
        self.folder.mkdir(parents=True)

    def write_blueprint(self, topic="sample_topic", slug="sample-tag"):
        (self.folder / f"{topic}.json").write_text(
            json.dumps(
                {
                    "topic": topic,
                    "display_name": "Sample lesson",
                    "curriculum_ref": "8 класс — Квадратные уравнения",
                    "tag": {"slug": slug, "name": "Sample tag"},
                    "answer": {"type": "roots"},
                    "parameters": {},
                    "constraints_template": f"{topic}.j2",
                }
            ),
            encoding="utf-8",
        )

    def run_command(self, name, *args):
        output = StringIO()
        call_command(
            name,
            "agents_and_engine/qadam_blueprints",
            *args,
            stdout=output,
            stderr=StringIO(),
        )
        return output.getvalue()

    def test_ingest_seed_only_creates_curriculum_once(self):
        self.write_blueprint()
        self.run_command("ingest_blueprints", "--seed-only")
        self.run_command("ingest_blueprints", "--seed-only")

        self.assertEqual(Module.objects.count(), 1)
        self.assertEqual(Tag.objects.count(), 1)
        self.assertEqual(Lesson.objects.count(), 1)
        self.assertEqual(Lesson.objects.get().topic, "sample_topic")
        self.assertEqual(Question.objects.count(), 0)

    def test_ingest_requires_openai_key_before_seeding(self):
        self.write_blueprint()
        with patch.object(config.config, "OPENAI_API_KEY", None):
            with self.assertRaisesMessage(CommandError, "OPENAI_API_KEY is missing"):
                self.run_command("ingest_blueprints")
        self.assertFalse(Lesson.objects.exists())

    @patch("apps.content.management.commands.ingest_blueprints.generate_one")
    def test_ingest_generates_one_per_missing_difficulty(self, generate_one):
        self.write_blueprint()

        def publish(topic, target_score):
            difficulty = {10: 1, 22: 2, 34: 3}[target_score]
            lesson = Lesson.objects.get(topic=topic)
            question = Question.objects.create(
                text=f"question {difficulty}",
                explanation="",
                content_hash=f"{target_score:064d}",
                difficulty=difficulty,
                language=config.DEFAULT_LANGUAGE,
                lesson=lesson,
            )
            question.tags.add(lesson.tag)
            return {"question_id": question.pk, "was_duplicate": False}

        generate_one.side_effect = publish
        self.assertIn(
            "missing difficulties: [1, 2, 3]",
            self.run_command("ingest_blueprints", "--dry-run"),
        )
        self.assertEqual(Question.objects.count(), 0)

        self.run_command("ingest_blueprints")
        self.run_command("ingest_blueprints")

        self.assertEqual(Question.objects.count(), 3)
        self.assertEqual(generate_one.call_count, 3)
        self.assertEqual(
            {call.args[1] for call in generate_one.call_args_list},
            {10, 22, 34},
        )

    @patch("apps.content.management.commands.ingest_blueprints.generate_one")
    def test_ingest_fails_after_bounded_attempts_when_no_question_is_saved(
        self, generate_one
    ):
        self.write_blueprint()
        generate_one.return_value = {}

        with self.assertRaises(CommandError):
            self.run_command("ingest_blueprints")

        self.assertEqual(generate_one.call_count, 9)
        self.assertTrue(Lesson.objects.filter(topic="sample_topic").exists())

    def test_existing_question_in_another_language_counts_for_its_difficulty(self):
        self.write_blueprint()
        self.run_command("ingest_blueprints", "--seed-only")
        lesson = Lesson.objects.get()
        question = Question.objects.create(
            text="existing Russian question",
            explanation="",
            content_hash="d" * 64,
            difficulty=2,
            language="ru",
            lesson=lesson,
        )
        question.tags.add(lesson.tag)

        output = self.run_command("ingest_blueprints", "--dry-run")

        self.assertIn("missing difficulties: [1, 3]", output)

    def test_ingest_reuses_original_seed_curriculum_lesson(self):
        self.write_blueprint(topic="quadratic_equations")
        subject = Subject.objects.create(slug="math", name="Математика")
        grade = ClassGrade.objects.create(grade=12, subject=subject)
        module = Module.objects.create(
            slug="algebra-basics", title="Algebra", class_grade=grade
        )
        tag = Tag.objects.create(slug="sample-tag", name="Sample tag")
        lesson = Lesson.objects.create(
            module=module, tag=tag, title="Original lesson", video_url=""
        )

        self.run_command("ingest_blueprints", "--seed-only")
        self.assertEqual(Lesson.objects.count(), 1)
        self.assertEqual(Module.objects.count(), 1)
        lesson.refresh_from_db()
        self.assertEqual(lesson.topic, "quadratic_equations")

        self.run_command("remove_blueprints")
        self.assertFalse(Lesson.objects.exists())
        self.assertFalse(Tag.objects.exists())
        self.assertFalse(Module.objects.exists())

    def test_nonblueprint_files_and_empty_folder_are_noops(self):
        (self.folder / "data.json").write_text('{"other": true}', encoding="utf-8")
        (self.folder / "broken.json").write_text("{", encoding="utf-8")
        (self.folder / "notes.md").write_text("hello", encoding="utf-8")
        self.assertIn("no blueprints found", self.run_command("ingest_blueprints"))
        self.assertIn("no blueprints found", self.run_command("remove_blueprints"))
        self.assertFalse(Lesson.objects.exists())

    def test_paths_cannot_leave_app_root(self):
        with self.assertRaises(CommandError):
            call_command("ingest_blueprints", "../outside")

    def test_remove_deletes_unanswered_content_and_unused_curriculum(self):
        self.write_blueprint()
        self.run_command("ingest_blueprints", "--seed-only")
        lesson = Lesson.objects.get()
        question = Question.objects.create(
            text="generated", explanation="", content_hash="a" * 64, lesson=lesson
        )
        question.tags.add(lesson.tag)
        AssessmentTest.objects.create(type="micro", title="practice", lesson=lesson)

        self.run_command("remove_blueprints", "--dry-run")
        self.assertTrue(Question.objects.filter(pk=question.pk).exists())
        self.run_command("remove_blueprints")
        self.run_command("remove_blueprints")

        self.assertFalse(Question.objects.exists())
        self.assertFalse(AssessmentTest.objects.exists())
        self.assertFalse(Lesson.objects.exists())
        self.assertFalse(Tag.objects.exists())
        self.assertFalse(Module.objects.exists())

    def test_remove_hides_admin_topic_and_ingest_restores_it(self):
        self.write_blueprint(topic="quadratic_equations")

        def admin_topics():
            return {
                value
                for value, _label in GenerationJobAdminForm().fields["topic"].choices
            }

        self.assertIn("quadratic_equations", admin_topics())
        self.assertIn("quadratic_equations_vieta", admin_topics())

        self.run_command("remove_blueprints", "--dry-run")
        self.assertFalse(HiddenBlueprintTopic.objects.exists())

        self.run_command("remove_blueprints")
        self.run_command("remove_blueprints")
        self.assertIn(
            "quadratic_equations",
            set(HiddenBlueprintTopic.objects.values_list("topic", flat=True)),
        )
        self.assertNotIn("quadratic_equations", admin_topics())
        self.assertIn("quadratic_equations_vieta", admin_topics())

        self.run_command("ingest_blueprints", "--seed-only")
        self.assertFalse(HiddenBlueprintTopic.objects.exists())
        self.assertIn("quadratic_equations", admin_topics())

    def test_remove_keeps_answered_question_and_linked_curriculum(self):
        self.write_blueprint()
        self.run_command("ingest_blueprints", "--seed-only")
        lesson = Lesson.objects.get()
        answered = Question.objects.create(
            text="answered", explanation="", content_hash="b" * 64, lesson=lesson
        )
        unanswered = Question.objects.create(
            text="unanswered", explanation="", content_hash="c" * 64, lesson=lesson
        )
        answered.tags.add(lesson.tag)
        unanswered.tags.add(lesson.tag)
        test = AssessmentTest.objects.create(
            type="micro", title="practice", lesson=lesson
        )
        user = get_user_model().objects.create_user(
            email="student@example.com", password="testpassword123"
        )
        attempt = TestAttempt.objects.create(student=user, test=test)
        AttemptAnswer.objects.create(attempt=attempt, question=answered)

        self.run_command("remove_blueprints")

        self.assertTrue(Question.objects.filter(pk=answered.pk).exists())
        self.assertFalse(Question.objects.filter(pk=unanswered.pk).exists())
        self.assertTrue(
            HiddenBlueprintTopic.objects.filter(topic="sample_topic").exists()
        )
        self.assertTrue(Lesson.objects.filter(pk=lesson.pk).exists())
        self.assertTrue(TestAttempt.objects.filter(pk=attempt.pk).exists())
        self.assertTrue(AttemptAnswer.objects.filter(question=answered).exists())

    def test_remove_refuses_shared_tag(self):
        self.write_blueprint()
        second = self.root / "ubt_question_engine" / "ubt_blueprints"
        second.mkdir(parents=True)
        (second / "other_topic.json").write_text(
            json.dumps(
                {
                    "topic": "other_topic",
                    "tag": {"slug": "sample-tag", "name": "Sample tag"},
                }
            ),
            encoding="utf-8",
        )
        with self.assertRaises(CommandError):
            self.run_command("remove_blueprints")

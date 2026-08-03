from unittest.mock import Mock, patch

from django.test import SimpleTestCase
from pydantic import ValidationError

from web_harvester import agents_web, trust
from web_harvester.schemas import FieldClassification, WebSearch
from web_harvester.source_policy import FieldType, SourceStrategy


class SearchTests(SimpleTestCase):
    def test_search_constrains_and_defensively_filters_results(self):
        response = {
            "results": [
                {
                    "url": "https://kaznmu.edu.kz/a",
                    "raw_content": "medical page",
                },
                {
                    "url": "https://kbtu.edu.kz/a",
                    "raw_content": "cross-field page",
                },
                {
                    "url": "https://kaznmu.edu.kz.evil.com/a",
                    "raw_content": "lookalike page",
                },
                {
                    "url": "https://kaznmu.edu.kz/a",
                    "raw_content": "duplicate page",
                },
                {"url": "https://testcenter.kz/empty", "content": ""},
            ]
        }

        with patch.object(agents_web._client, "search", return_value=response) as search:
            pages = agents_web.search(
                "Medicine",
                "6B101",
                FieldType.MEDICINE,
                SourceStrategy.PRIMARY,
                max_results=4,
            )

        self.assertEqual(pages, [("https://kaznmu.edu.kz/a", "medical page")])
        self.assertEqual(
            search.call_args.kwargs["include_domains"],
            sorted(
                trust.allowed_domains(
                    FieldType.MEDICINE,
                    SourceStrategy.PRIMARY,
                )
            ),
        )
        self.assertEqual(search.call_args.kwargs["include_raw_content"], "text")
        self.assertEqual(search.call_args.kwargs["max_results"], 4)
        self.assertIn("medicine", search.call_args.kwargs["query"])

    def test_fallback_search_uses_only_fallback_domains(self):
        response = {
            "results": [{"url": "https://univision.kz/a", "content": "fallback page"}]
        }

        with patch.object(agents_web._client, "search", return_value=response) as search:
            pages = agents_web.search(
                "Medicine",
                "6B101",
                FieldType.MEDICINE,
                SourceStrategy.FALLBACK,
            )

        self.assertEqual(pages, [("https://univision.kz/a", "fallback page")])
        self.assertEqual(
            search.call_args.kwargs["include_domains"],
            sorted(trust.FALLBACK_DOMAINS),
        )

    def test_search_failure_returns_empty_and_invalid_limit_propagates(self):
        with (
            patch.object(
                agents_web._client,
                "search",
                side_effect=RuntimeError("external detail"),
            ) as search,
            self.assertLogs(agents_web.logger, level="WARNING") as logs,
        ):
            pages = agents_web.search(
                "Medicine",
                "6B101",
                FieldType.MEDICINE,
                SourceStrategy.PRIMARY,
            )

        self.assertEqual(pages, [])
        self.assertNotIn("external detail", " ".join(logs.output))

        with self.assertRaises(ValueError):
            agents_web.search(
                "Medicine",
                "6B101",
                FieldType.MEDICINE,
                SourceStrategy.PRIMARY,
                max_results=0,
            )
        self.assertEqual(search.call_count, 1)

    def test_truncate_content_preserves_short_text_and_limits_long_text(self):
        self.assertEqual(agents_web.truncate_content("short", 20), "short")
        truncated = agents_web.truncate_content("one two three four", 12)
        self.assertTrue(truncated.startswith("one two"))
        self.assertTrue(truncated.endswith("[Content Truncated]"))


class StructuredAgentTests(SimpleTestCase):
    def test_classification_returns_only_canonical_field(self):
        structured = FieldClassification(field_type=FieldType.TECHNICAL)
        classifier = Mock()
        classifier.invoke.return_value = structured
        with patch.object(agents_web, "_classifier", classifier):
            result = agents_web.classify("Software engineering", "6B061")

        self.assertIs(result, FieldType.TECHNICAL)
        with self.assertRaises(ValidationError):
            FieldClassification(field_type="unknown")

    def test_classification_failure_returns_none_without_error_detail(self):
        classifier = Mock()
        classifier.invoke.side_effect = RuntimeError("external detail")
        with (
            patch.object(agents_web, "_classifier", classifier),
            self.assertLogs(agents_web.logger, level="WARNING") as logs,
        ):
            result = agents_web.classify("Unknown", "X")

        self.assertIsNone(result)
        self.assertNotIn("external detail", " ".join(logs.output))

    def test_extract_handles_empty_success_and_failure(self):
        extractor = Mock()
        with patch.object(agents_web, "_extractor", extractor):
            self.assertIsNone(agents_web.extract("Medicine", "6B101", []))
            extractor.invoke.assert_not_called()

        expected = WebSearch(
            ubt_score=80,
            sources=["https://kaznmu.edu.kz/a"],
        )
        pages = [("https://kaznmu.edu.kz/a", "page")]
        extractor = Mock()
        extractor.invoke.return_value = expected
        with patch.object(agents_web, "_extractor", extractor):
            self.assertIs(agents_web.extract("Medicine", "6B101", pages), expected)

        extractor = Mock()
        extractor.invoke.side_effect = RuntimeError("external detail")
        with (
            patch.object(agents_web, "_extractor", extractor),
            self.assertLogs(agents_web.logger, level="WARNING"),
        ):
            self.assertIsNone(agents_web.extract("Medicine", "6B101", pages))

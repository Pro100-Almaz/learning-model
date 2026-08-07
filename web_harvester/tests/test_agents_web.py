from unittest.mock import Mock, patch

from django.test import SimpleTestCase
from pydantic import ValidationError

from apps.careers.models import (
    AdmissionRoute,
    ApplicantBackground,
    FundingType,
    InstructionLanguage,
    ScoreType,
)
from web_harvester import agents_web, trust
from web_harvester.schemas import AdmissionExtraction, ClaimEvidence, FieldClassification
from web_harvester.search_planning import (
    SearchFact,
    SearchPage,
    SearchTarget,
    build_queries,
)
from web_harvester.source_policy import FieldType, SourceStrategy

TARGET = SearchTarget(
    profession_name="Doctor",
    program_group_code="B086",
    program_group_name="General medicine",
    year=2026,
    legacy_codes=("5B130100",),
    alternative_names=("Общая медицина",),
)


class SearchTests(SimpleTestCase):
    def test_search_constrains_and_defensively_filters_each_fact_query(self):
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
                TARGET,
                FieldType.MEDICINE,
                SourceStrategy.PRIMARY,
                max_results=4,
            )

        self.assertEqual(
            {(page.fact, page.url, page.content) for page in pages},
            {(fact, "https://kaznmu.edu.kz/a", "medical page") for fact in SearchFact},
        )
        self.assertEqual(search.call_count, len(build_queries(TARGET)))
        for call in search.call_args_list:
            self.assertEqual(
                call.kwargs["include_domains"],
                sorted(
                    trust.allowed_domains(
                        FieldType.MEDICINE,
                        SourceStrategy.PRIMARY,
                    )
                ),
            )
            self.assertEqual(call.kwargs["include_raw_content"], "text")
            self.assertEqual(call.kwargs["max_results"], 4)
            self.assertIn("2026", call.kwargs["query"])

    def test_fallback_search_uses_only_fallback_domains(self):
        response = {
            "results": [{"url": "https://univision.kz/a", "content": "fallback page"}]
        }

        with patch.object(agents_web._client, "search", return_value=response) as search:
            pages = agents_web.search(
                TARGET,
                FieldType.MEDICINE,
                SourceStrategy.FALLBACK,
            )

        self.assertEqual({page.url for page in pages}, {"https://univision.kz/a"})
        for call in search.call_args_list:
            self.assertEqual(
                call.kwargs["include_domains"],
                sorted(trust.FALLBACK_DOMAINS),
            )

    def test_one_failed_query_does_not_cancel_remaining_fact_queries(self):
        success = {"results": [{"url": "https://testcenter.kz/a", "content": "page"}]}
        with (
            patch.object(
                agents_web._client,
                "search",
                side_effect=[RuntimeError("external detail"), success],
            ) as search,
            patch.object(
                agents_web,
                "build_queries",
                return_value=build_queries(TARGET)[:2],
            ),
            self.assertLogs(agents_web.logger, level="WARNING") as logs,
        ):
            pages = agents_web.search(
                TARGET,
                FieldType.MEDICINE,
                SourceStrategy.PRIMARY,
            )

        self.assertEqual(len(pages), 1)
        self.assertEqual(search.call_count, 2)
        self.assertNotIn("external detail", " ".join(logs.output))

    def test_invalid_result_limit_is_rejected_before_search(self):
        with (
            patch.object(agents_web._client, "search") as search,
            self.assertRaises(ValueError),
        ):
            agents_web.search(
                TARGET,
                FieldType.MEDICINE,
                SourceStrategy.PRIMARY,
                max_results=0,
            )
        search.assert_not_called()

    def test_truncate_content_preserves_short_text_and_limits_long_text(self):
        self.assertEqual(agents_web.truncate_content("short", 20), "short")
        truncated = agents_web.truncate_content("one two three four", 12)
        self.assertTrue(truncated.startswith("one two"))
        self.assertTrue(truncated.endswith("[Content Truncated]"))


class StructuredAgentTests(SimpleTestCase):
    def test_classification_returns_only_canonical_field(self):
        structured = FieldClassification(field_type=FieldType.MEDICINE)
        classifier = Mock()
        classifier.invoke.return_value = structured
        with patch.object(agents_web, "_classifier", classifier):
            result = agents_web.classify(TARGET)

        self.assertIs(result, FieldType.MEDICINE)
        message = classifier.invoke.call_args.args[0][1][1]
        self.assertIn("B086", message)
        self.assertIn("General medicine", message)
        with self.assertRaises(ValidationError):
            FieldClassification(field_type="unknown")

    def test_classification_failure_returns_none_without_error_detail(self):
        classifier = Mock()
        classifier.invoke.side_effect = RuntimeError("external detail")
        with (
            patch.object(agents_web, "_classifier", classifier),
            self.assertLogs(agents_web.logger, level="WARNING") as logs,
        ):
            result = agents_web.classify(TARGET)

        self.assertIsNone(result)
        self.assertNotIn("external detail", " ".join(logs.output))

    def test_extract_handles_empty_success_and_failure(self):
        extractor = Mock()
        with patch.object(agents_web, "_extractor", extractor):
            self.assertIsNone(agents_web.extract(TARGET, []))
            extractor.invoke.assert_not_called()

        expected = AdmissionExtraction(
            threshold_claims=[
                {
                    "score": 80,
                    "score_type": ScoreType.LEGAL_MINIMUM,
                    "year": 2026,
                    "program_group_code": "B086",
                    "university_name": None,
                    "admission_route": AdmissionRoute.STANDARD,
                    "admission_route_details": None,
                    "funding_type": FundingType.GRANT_AND_PAID,
                    "applicant_background": ApplicantBackground.GENERAL_SECONDARY,
                    "applicant_background_details": None,
                    "quota_category": "not applicable",
                    "instruction_language": InstructionLanguage.LANGUAGE_INDEPENDENT,
                    "evidence": ClaimEvidence(
                        source_url="https://kaznmu.edu.kz/a",
                        excerpt="minimum score 80",
                    ),
                }
            ]
        )
        pages = [
            SearchPage(
                fact=SearchFact.UNIVERSITY_MINIMUM,
                query='"B086" 2026 threshold',
                url="https://kaznmu.edu.kz/a",
                content="page",
            )
        ]
        extractor = Mock()
        extractor.invoke.return_value = expected
        with patch.object(agents_web, "_extractor", extractor):
            self.assertIs(agents_web.extract(TARGET, pages), expected)
        message = extractor.invoke.call_args.args[0][1][1]
        self.assertIn("REQUESTED FACT: university_minimum", message)
        self.assertIn("Requested year: 2026", message)

        extractor = Mock()
        extractor.invoke.side_effect = RuntimeError("external detail")
        with (
            patch.object(agents_web, "_extractor", extractor),
            self.assertLogs(agents_web.logger, level="WARNING"),
        ):
            self.assertIsNone(agents_web.extract(TARGET, pages))

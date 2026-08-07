from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models.deletion import ProtectedError
from django.test import TestCase

from apps.careers.models import (
    AdmissionRoute,
    AdmissionSource,
    AdmissionThreshold,
    ApplicantBackground,
    EducationalProgramGroup,
    FundingType,
    GrantThreshold,
    InstructionLanguage,
    ScoreType,
    Specialty,
    University,
)


class AdmissionModelTests(TestCase):
    def setUp(self):
        self.program_group = EducationalProgramGroup.objects.create(
            code="B057",
            name="Information technology",
        )
        self.university = University.objects.create(
            name="Test Technical University",
            city="Almaty",
            code="TTU",
        )
        self.specialty = Specialty.objects.create(
            university=self.university,
            program_group=self.program_group,
            name="Software engineering",
            code="6B06101",
        )
        self.source = AdmissionSource.objects.create(
            url="https://example.gov.kz/admissions-2026",
            title="Admissions rules for 2026",
            publisher="Official authority",
            content_fingerprint="a" * 64,
        )

    def threshold_values(self, **overrides):
        values = {
            "program_group": self.program_group,
            "source": self.source,
            "year": 2026,
            "score": 50,
            "score_type": ScoreType.LEGAL_MINIMUM,
            "admission_route": AdmissionRoute.STANDARD,
            "funding_type": FundingType.GRANT_AND_PAID,
            "applicant_background": ApplicantBackground.GENERAL_SECONDARY,
            "quota_category": "not_applicable",
            "instruction_language": InstructionLanguage.LANGUAGE_INDEPENDENT,
            "evidence_excerpt": "The minimum score for this category is 50.",
        }
        values.update(overrides)
        return values

    def assert_field_error(self, instance, field):
        with self.assertRaises(ValidationError) as raised:
            instance.full_clean()
        self.assertIn(field, raised.exception.message_dict)

    def test_valid_legal_minimum_can_have_national_scope(self):
        threshold = AdmissionThreshold(**self.threshold_values())

        threshold.full_clean()
        threshold.save()

        self.assertIsNone(threshold.university_id)
        self.assertIsNone(threshold.specialty_id)

    def test_valid_university_minimum(self):
        threshold = AdmissionThreshold(
            **self.threshold_values(
                university=self.university,
                specialty=self.specialty,
                score=80,
                score_type=ScoreType.UNIVERSITY_MINIMUM,
                funding_type=FundingType.PAID,
            )
        )

        threshold.full_clean()
        threshold.save()

        self.assertEqual(threshold.university, self.university)

    def test_valid_historical_grant_cutoff(self):
        threshold = AdmissionThreshold(
            **self.threshold_values(
                university=self.university,
                specialty=self.specialty,
                score=105,
                score_type=ScoreType.HISTORICAL_GRANT_CUTOFF,
                funding_type=FundingType.GRANT,
                quota_category="general_competition",
                instruction_language=InstructionLanguage.KAZAKH,
            )
        )

        threshold.full_clean()
        threshold.save()

        self.assertEqual(threshold.score, 105)

    def test_shortened_tvet_score_of_25_is_valid_in_its_context(self):
        threshold = AdmissionThreshold(
            **self.threshold_values(
                score=25,
                admission_route=AdmissionRoute.SHORTENED_RELATED,
                applicant_background=ApplicantBackground.TVET_POSTSECONDARY,
            )
        )

        threshold.full_clean()
        threshold.save()

        self.assertEqual(threshold.score, 25)

    def test_score_must_be_between_1_and_140(self):
        self.assert_field_error(
            AdmissionThreshold(**self.threshold_values(score=0)),
            "score",
        )
        self.assert_field_error(
            AdmissionThreshold(**self.threshold_values(score=141)),
            "score",
        )

    def test_year_must_be_between_2000_and_2100(self):
        self.assert_field_error(
            AdmissionThreshold(**self.threshold_values(year=1999)),
            "year",
        )
        self.assert_field_error(
            AdmissionThreshold(**self.threshold_values(year=2101)),
            "year",
        )

    def test_university_minimum_requires_university(self):
        threshold = AdmissionThreshold(
            **self.threshold_values(score_type=ScoreType.UNIVERSITY_MINIMUM)
        )

        self.assert_field_error(threshold, "university")

    def test_historical_grant_cutoff_requires_grant_funding(self):
        threshold = AdmissionThreshold(
            **self.threshold_values(
                university=self.university,
                score_type=ScoreType.HISTORICAL_GRANT_CUTOFF,
                funding_type=FundingType.PAID,
            )
        )

        self.assert_field_error(threshold, "funding_type")

    def test_other_route_requires_details(self):
        threshold = AdmissionThreshold(
            **self.threshold_values(
                admission_route=AdmissionRoute.OTHER,
                admission_route_details="   ",
            )
        )

        self.assert_field_error(threshold, "admission_route_details")

    def test_other_background_requires_details(self):
        threshold = AdmissionThreshold(
            **self.threshold_values(
                applicant_background=ApplicantBackground.OTHER,
                applicant_background_details="   ",
            )
        )

        self.assert_field_error(threshold, "applicant_background_details")

    def test_specialty_requires_its_own_university(self):
        other_university = University.objects.create(
            name="Other University",
            city="Astana",
            code="OU",
        )
        threshold = AdmissionThreshold(
            **self.threshold_values(
                university=other_university,
                specialty=self.specialty,
            )
        )

        self.assert_field_error(threshold, "specialty")

    def test_specialty_requires_its_own_program_group(self):
        other_group = EducationalProgramGroup.objects.create(
            code="B058",
            name="Information security",
        )
        threshold = AdmissionThreshold(
            **self.threshold_values(
                program_group=other_group,
                university=self.university,
                specialty=self.specialty,
            )
        )

        self.assert_field_error(threshold, "specialty")

    def test_evidence_and_quota_cannot_contain_only_whitespace(self):
        self.assert_field_error(
            AdmissionThreshold(**self.threshold_values(evidence_excerpt="  ")),
            "evidence_excerpt",
        )
        self.assert_field_error(
            AdmissionThreshold(**self.threshold_values(quota_category="  ")),
            "quota_category",
        )

    def test_exact_duplicate_with_null_scope_is_rejected(self):
        AdmissionThreshold.objects.create(**self.threshold_values())

        with self.assertRaises(IntegrityError), transaction.atomic():
            AdmissionThreshold.objects.create(**self.threshold_values())

    def test_different_sources_may_preserve_conflicting_scores(self):
        other_source = AdmissionSource.objects.create(
            url=self.source.url,
            title=self.source.title,
            publisher=self.source.publisher,
            content_fingerprint="b" * 64,
        )
        AdmissionThreshold.objects.create(**self.threshold_values(score=50))
        AdmissionThreshold.objects.create(
            **self.threshold_values(source=other_source, score=55)
        )

        self.assertEqual(AdmissionThreshold.objects.count(), 2)

    def test_referenced_catalog_and_source_objects_are_protected(self):
        threshold = AdmissionThreshold.objects.create(
            **self.threshold_values(
                university=self.university,
                specialty=self.specialty,
            )
        )

        for protected_object in (
            threshold.program_group,
            threshold.university,
            threshold.specialty,
            threshold.source,
        ):
            with self.subTest(model=type(protected_object).__name__):
                with self.assertRaises(ProtectedError):
                    protected_object.delete()

    def test_legacy_grant_threshold_remains_available(self):
        legacy = GrantThreshold.objects.create(
            specialty=self.specialty,
            year=2024,
            min_score=100,
        )

        self.assertEqual(legacy.min_score, 100)

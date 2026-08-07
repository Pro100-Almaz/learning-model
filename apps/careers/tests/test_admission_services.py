from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from apps.careers.admission_services import (
    latest_applicable_thresholds,
    near_miss_grant_cutoffs,
    qualifying_grant_cutoffs,
)
from apps.careers.models import (
    AdmissionRoute,
    AdmissionSource,
    AdmissionThreshold,
    ApplicantBackground,
    EducationalProgramGroup,
    FundingType,
    InstructionLanguage,
    ScoreType,
    Specialty,
    University,
)


class UniversityListCutoffTests(APITestCase):
    """The catalog keeps the legacy number and adds the typed canonical one."""

    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            email="catalog@example.com",
            password="testpass1234",
        )
        cls.university = University.objects.create(
            name="ENU",
            city="Astana",
            code="ENU",
        )
        cls.specialty = Specialty.objects.create(
            university=cls.university,
            name="Software engineering",
            code="6B061",
        )
        group = EducationalProgramGroup.objects.create(
            code="B057",
            name="Information technology",
        )
        source = AdmissionSource.objects.create(
            url="https://source.example.kz/b057/2025",
            content_fingerprint="b057-2025-118",
            retrieved_at=timezone.now(),
        )
        AdmissionThreshold.objects.create(
            program_group=group,
            university=cls.university,
            specialty=cls.specialty,
            source=source,
            year=2025,
            score=118,
            score_type=ScoreType.HISTORICAL_GRANT_CUTOFF,
            admission_route=AdmissionRoute.STANDARD,
            funding_type=FundingType.GRANT,
            applicant_background=ApplicantBackground.GENERAL_SECONDARY,
            quota_category="general competition",
            instruction_language=InstructionLanguage.LANGUAGE_INDEPENDENT,
            evidence_excerpt="B057 grant cutoff 118",
            evidence_location="table 1",
            verified_at=timezone.now(),
        )

    def test_specialty_exposes_typed_latest_grant_cutoff(self):
        self.client.force_authenticate(self.user)

        response = self.client.get(reverse("v1:careers:universities"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        specialty_row = response.json()[0]["specialties"][0]
        self.assertIsNone(specialty_row["latest_threshold"])
        self.assertEqual(
            specialty_row["latest_grant_cutoff"],
            {
                "score": 118,
                "year": 2025,
                "score_type": ScoreType.HISTORICAL_GRANT_CUTOFF.value,
                "source_url": "https://source.example.kz/b057/2025",
            },
        )


class AdmissionServiceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.group = EducationalProgramGroup.objects.create(
            code="B086",
            name="General medicine",
        )
        cls.other_group = EducationalProgramGroup.objects.create(
            code="B057",
            name="Information technology",
        )
        cls.university = University.objects.create(
            name="KazNMU",
            city="Almaty",
            code="KAZNMU",
        )

    def source(self, url: str = "https://kaznmu.edu.kz/a") -> AdmissionSource:
        source, _ = AdmissionSource.objects.get_or_create(
            url=url,
            content_fingerprint=url,
            defaults={"retrieved_at": timezone.now()},
        )
        return source

    def threshold(self, **overrides) -> AdmissionThreshold:
        source_url = overrides.pop("source_url", "https://kaznmu.edu.kz/a")
        verified = overrides.pop("verified", True)
        fields = {
            "program_group": self.group,
            "university": None,
            "specialty": None,
            "source": self.source(source_url),
            "year": 2025,
            "score": 100,
            "score_type": ScoreType.HISTORICAL_GRANT_CUTOFF,
            "admission_route": AdmissionRoute.STANDARD,
            "admission_route_details": "",
            "funding_type": FundingType.GRANT,
            "applicant_background": ApplicantBackground.GENERAL_SECONDARY,
            "applicant_background_details": "",
            "quota_category": "general competition",
            "instruction_language": InstructionLanguage.LANGUAGE_INDEPENDENT,
            "evidence_excerpt": "B086 grant cutoff 100",
            "evidence_location": "table 1",
            "verified_at": timezone.now() if verified else None,
        }
        fields.update(overrides)
        return AdmissionThreshold.objects.create(**fields)

    def test_legal_minimum_is_not_reported_as_a_grant_cutoff(self):
        self.threshold(
            score=50,
            score_type=ScoreType.LEGAL_MINIMUM,
            funding_type=FundingType.GRANT_AND_PAID,
        )

        qualifying = qualifying_grant_cutoffs(120)

        self.assertEqual(qualifying, [])

    def test_shortened_tvet_score_does_not_qualify_a_school_graduate(self):
        self.threshold(
            score=25,
            admission_route=AdmissionRoute.SHORTENED_RELATED,
            applicant_background=ApplicantBackground.TVET_POSTSECONDARY,
        )

        qualifying = qualifying_grant_cutoffs(120)

        self.assertEqual(qualifying, [])

    def test_grant_cutoff_qualifies_when_predicted_score_is_high_enough(self):
        self.threshold(score=100)

        qualifying = qualifying_grant_cutoffs(118)

        self.assertEqual(len(qualifying), 1)
        entry = qualifying[0]
        self.assertEqual(entry["min_score"], 100)
        self.assertEqual(entry["margin"], 18)
        self.assertEqual(entry["specialty_name"], "General medicine")
        self.assertEqual(entry["university_name"], "")
        self.assertEqual(entry["program_group_code"], "B086")
        self.assertEqual(entry["year"], 2025)
        self.assertEqual(entry["score_type"], ScoreType.HISTORICAL_GRANT_CUTOFF)
        self.assertEqual(entry["source_url"], "https://kaznmu.edu.kz/a")

    def test_unverified_threshold_is_never_returned(self):
        self.threshold(score=100, verified=False)

        self.assertEqual(qualifying_grant_cutoffs(140), [])

    def test_latest_is_resolved_per_context_not_globally(self):
        self.threshold(year=2024, score=95)
        self.threshold(year=2025, score=105, source_url="https://kaznmu.edu.kz/b")
        self.threshold(
            program_group=self.other_group,
            year=2023,
            score=80,
            source_url="https://kaznmu.edu.kz/c",
        )

        qualifying = qualifying_grant_cutoffs(140)

        self.assertEqual(
            sorted(entry["min_score"] for entry in qualifying),
            [80, 105],
        )
        self.assertNotIn(95, [entry["min_score"] for entry in qualifying])

    def test_language_independent_threshold_matches_a_requested_language(self):
        self.threshold(instruction_language=InstructionLanguage.LANGUAGE_INDEPENDENT)
        self.threshold(
            program_group=self.other_group,
            instruction_language=InstructionLanguage.KAZAKH,
            source_url="https://kaznmu.edu.kz/b",
        )
        self.threshold(
            program_group=self.other_group,
            instruction_language=InstructionLanguage.ENGLISH,
            source_url="https://kaznmu.edu.kz/c",
            score=90,
        )

        thresholds = latest_applicable_thresholds(
            score_type=ScoreType.HISTORICAL_GRANT_CUTOFF,
            admission_route=AdmissionRoute.STANDARD,
            funding_type=FundingType.GRANT,
            applicant_background=ApplicantBackground.GENERAL_SECONDARY,
            instruction_language=InstructionLanguage.KAZAKH,
        )

        languages = sorted(threshold.instruction_language for threshold in thresholds)
        self.assertEqual(
            languages,
            [InstructionLanguage.KAZAKH, InstructionLanguage.LANGUAGE_INDEPENDENT],
        )

    def test_conflicting_source_rows_are_not_averaged(self):
        self.threshold(score=100)
        self.threshold(score=110, source_url="https://kaznmu.edu.kz/b")

        qualifying = qualifying_grant_cutoffs(140)

        self.assertEqual(
            sorted(entry["min_score"] for entry in qualifying),
            [100, 110],
        )

    def test_grant_only_request_also_matches_grant_and_paid_rows(self):
        # A grant cutoff must be grant-funded, so combined funding is exercised
        # through a legal minimum instead.
        self.threshold(
            score=50,
            score_type=ScoreType.LEGAL_MINIMUM,
            funding_type=FundingType.GRANT_AND_PAID,
        )

        thresholds = latest_applicable_thresholds(
            score_type=ScoreType.LEGAL_MINIMUM,
            admission_route=AdmissionRoute.STANDARD,
            funding_type=FundingType.GRANT,
            applicant_background=ApplicantBackground.GENERAL_SECONDARY,
        )

        self.assertEqual([threshold.score for threshold in thresholds], [50])

    def test_university_specific_cutoff_reports_the_university(self):
        self.threshold(university=self.university)

        entry = qualifying_grant_cutoffs(140)[0]

        self.assertEqual(entry["university_name"], "KazNMU")

    def test_near_miss_returns_only_cutoffs_just_above_the_predicted_score(self):
        self.threshold(score=100)
        self.threshold(
            program_group=self.other_group,
            score=112,
            source_url="https://kaznmu.edu.kz/b",
        )
        self.threshold(
            program_group=self.other_group,
            university=self.university,
            score=140,
            source_url="https://kaznmu.edu.kz/c",
        )

        near_miss = near_miss_grant_cutoffs(105, within=10)

        self.assertEqual(len(near_miss), 1)
        self.assertEqual(near_miss[0]["min_score"], 112)
        self.assertEqual(near_miss[0]["points_needed"], 7)

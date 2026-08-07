from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from web_harvester.models import CandidateClaim
from web_harvester.source_policy import FieldType, SourceStrategy

PAYLOAD = {
    "score": 50,
    "year": 2026,
    "program_group_code": "B086",
    "evidence": {
        "source_url": "https://kaznmu.edu.kz/a",
        "excerpt": "B086, minimum score 50",
        "location": "table 2",
    },
}


def build_claim(**overrides) -> CandidateClaim:
    fields = {
        "profession_name": "Doctor",
        "program_group_code": "B086",
        "target_year": 2026,
        "field_type": FieldType.MEDICINE.value,
        "source_strategy": SourceStrategy.PRIMARY.value,
        "source_tier": 1,
        "confidence": "High",
        "claim_type": "threshold",
        "status": "accepted",
        "source_url": "https://kaznmu.edu.kz/a",
        "evidence_excerpt": "B086, minimum score 50",
        "evidence_location": "table 2",
        "payload": PAYLOAD,
        "payload_fingerprint": "f" * 64,
    }
    fields.update(overrides)
    return CandidateClaim(**fields)


class CandidateClaimModelTests(TestCase):
    def test_accepted_claim_with_evidence_is_saved(self):
        claim = build_claim()
        claim.full_clean()
        claim.save()

        stored = CandidateClaim.objects.get()
        self.assertEqual(stored.status, "accepted")
        self.assertEqual(stored.payload["score"], 50)
        self.assertIsNotNone(stored.harvested_at)
        self.assertIsNotNone(stored.updated_at)

    def test_blank_evidence_excerpt_is_rejected(self):
        claim = build_claim(evidence_excerpt="   ")

        with self.assertRaises(ValidationError) as error:
            claim.full_clean()

        self.assertIn("evidence_excerpt", error.exception.message_dict)

    def test_accepted_claim_cannot_have_rejection_reason(self):
        claim = build_claim(rejection_reason="wrong_year")

        with self.assertRaises(ValidationError) as error:
            claim.full_clean()

        self.assertIn("rejection_reason", error.exception.message_dict)

    def test_rejected_claim_requires_rejection_reason(self):
        claim = build_claim(status="rejected")

        with self.assertRaises(ValidationError) as error:
            claim.full_clean()

        self.assertIn("rejection_reason", error.exception.message_dict)

    def test_rejected_claim_with_reason_is_valid(self):
        claim = build_claim(
            status="rejected",
            rejection_reason="wrong_year",
            rejection_detail="Threshold year does not match target year.",
        )
        claim.full_clean()
        claim.save()

        self.assertEqual(CandidateClaim.objects.get().rejection_reason, "wrong_year")

    def test_source_metadata_must_match_strategy(self):
        primary = build_claim(source_tier=2, confidence="Low")
        fallback = build_claim(
            source_strategy=SourceStrategy.FALLBACK.value,
            source_tier=1,
            confidence="High",
        )

        for claim in (primary, fallback):
            with self.assertRaises(ValidationError) as error:
                claim.full_clean()
            self.assertIn("source_tier", error.exception.message_dict)
            self.assertIn("confidence", error.exception.message_dict)

    def test_duplicate_payload_fingerprint_is_rejected_by_constraint(self):
        build_claim().save()

        with self.assertRaises(IntegrityError), transaction.atomic():
            build_claim(profession_name="Doctor (rerun)").save()

        self.assertEqual(CandidateClaim.objects.count(), 1)

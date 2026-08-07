from datetime import date

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models.deletion import ProtectedError
from django.test import TestCase

from apps.careers.identity import (
    AmbiguousIdentityError,
    normalize_identifier,
    resolve_profession,
    resolve_program_group,
)
from apps.careers.models import (
    AdmissionSource,
    AliasLanguage,
    EducationalProgramGroup,
    Profession,
    ProfessionIdentifier,
    ProfessionIdentifierScheme,
    ProfessionProgramGroup,
    ProfessionProgramRelationship,
    ProgramGroupAlias,
    ProgramIdentifierScheme,
)


class IdentityModelTests(TestCase):
    def setUp(self):
        self.source = AdmissionSource.objects.create(
            url="https://example.gov.kz/classifier",
            title="Official classifier",
            publisher="Official authority",
            content_fingerprint="c" * 64,
        )
        self.program_group = EducationalProgramGroup.objects.create(
            code="B009",
            name="Mathematics teacher training",
        )
        self.profession = Profession.objects.create(
            slug="mathematics-teacher",
            name="Mathematics teacher",
        )

    def alias_values(self, **overrides):
        values = {
            "program_group": self.program_group,
            "scheme": ProgramIdentifierScheme.LEGACY_SPECIALTY_CODE,
            "value": "5B010900",
            "source": self.source,
            "evidence_excerpt": "The legacy specialty maps to group B009.",
        }
        values.update(overrides)
        return values

    def identifier_values(self, **overrides):
        values = {
            "profession": self.profession,
            "scheme": ProfessionIdentifierScheme.NATIONAL_OCCUPATION_CLASSIFIER,
            "value": "2330-1",
            "source": self.source,
            "evidence_excerpt": "The classifier identifies mathematics teachers.",
        }
        values.update(overrides)
        return values

    def test_normalization_is_unicode_whitespace_and_case_stable(self):
        self.assertEqual(normalize_identifier("  Ｂ009\n"), "b009")
        self.assertEqual(
            normalize_identifier("  Mathematics   TEACHER "),
            "mathematics teacher",
        )

    def test_current_program_group_code_resolves_without_an_alias(self):
        resolved = resolve_program_group("  b009  ")

        self.assertEqual(resolved, self.program_group)

    def test_legacy_code_resolves_only_through_a_verified_alias(self):
        self.assertIsNone(resolve_program_group("5B010900"))
        alias = ProgramGroupAlias(**self.alias_values())
        alias.full_clean()
        alias.save()

        resolved = resolve_program_group(
            "  5b010900 ",
            scheme=ProgramIdentifierScheme.LEGACY_SPECIALTY_CODE,
        )

        self.assertEqual(resolved, self.program_group)

    def test_program_name_is_not_inferred_without_an_alias(self):
        self.assertIsNone(resolve_program_group("Mathematics"))

    def test_alternative_name_requires_a_language(self):
        alias = ProgramGroupAlias(
            **self.alias_values(
                scheme=ProgramIdentifierScheme.ALTERNATIVE_NAME,
                value="Подготовка учителей математики",
            )
        )

        with self.assertRaises(ValidationError) as raised:
            alias.full_clean()

        self.assertIn("language", raised.exception.message_dict)

    def test_code_alias_rejects_a_language(self):
        alias = ProgramGroupAlias(**self.alias_values(language=AliasLanguage.RUSSIAN))

        with self.assertRaises(ValidationError) as raised:
            alias.full_clean()

        self.assertIn("language", raised.exception.message_dict)

    def test_alias_rejects_reversed_validity_dates(self):
        alias = ProgramGroupAlias(
            **self.alias_values(
                valid_from=date(2020, 1, 1),
                valid_to=date(2019, 12, 31),
            )
        )

        with self.assertRaises(ValidationError) as raised:
            alias.full_clean()

        self.assertIn("valid_to", raised.exception.message_dict)

    def test_alias_cannot_shadow_another_current_group_code(self):
        other_group = EducationalProgramGroup.objects.create(
            code="B010",
            name="Physics teacher training",
        )
        alias = ProgramGroupAlias(
            **self.alias_values(program_group=other_group, value="B009")
        )

        with self.assertRaises(ValidationError) as raised:
            alias.full_clean()

        self.assertIn("value", raised.exception.message_dict)

    def test_duplicate_alias_is_rejected_after_normalization(self):
        ProgramGroupAlias.objects.create(**self.alias_values(value="5B010900"))

        with self.assertRaises(IntegrityError), transaction.atomic():
            ProgramGroupAlias.objects.create(**self.alias_values(value=" 5b010900 "))

    def test_resolver_detects_unvalidated_cross_scheme_ambiguity(self):
        other_group = EducationalProgramGroup.objects.create(
            code="B010",
            name="Physics teacher training",
        )
        ProgramGroupAlias.objects.create(
            **self.alias_values(
                program_group=other_group,
                scheme=ProgramIdentifierScheme.ALTERNATIVE_CODE,
                value="B009",
            )
        )

        with self.assertRaises(AmbiguousIdentityError):
            resolve_program_group("B009")

        self.assertEqual(
            resolve_program_group(
                "B009",
                scheme=ProgramIdentifierScheme.CURRENT_GROUP_CODE,
            ),
            self.program_group,
        )

    def test_profession_resolves_by_slug_and_classifier_identifier(self):
        identifier = ProfessionIdentifier(**self.identifier_values())
        identifier.full_clean()
        identifier.save()

        self.assertEqual(
            resolve_profession("mathematics-teacher"),
            self.profession,
        )
        self.assertEqual(
            resolve_profession(
                "2330-1",
                scheme=ProfessionIdentifierScheme.NATIONAL_OCCUPATION_CLASSIFIER,
            ),
            self.profession,
        )

    def test_profession_alternative_name_requires_language(self):
        identifier = ProfessionIdentifier(
            **self.identifier_values(
                scheme=ProfessionIdentifierScheme.ALTERNATIVE_NAME,
                value="Учитель математики",
            )
        )

        with self.assertRaises(ValidationError) as raised:
            identifier.full_clean()

        self.assertIn("language", raised.exception.message_dict)

    def test_profession_can_have_multiple_explicit_program_routes(self):
        related_group = EducationalProgramGroup.objects.create(
            code="B055",
            name="Mathematics",
        )
        ProfessionProgramGroup.objects.create(
            profession=self.profession,
            program_group=self.program_group,
            relationship_type=ProfessionProgramRelationship.DIRECT,
            source=self.source,
            evidence_excerpt="The direct teacher-training route.",
        )
        ProfessionProgramGroup.objects.create(
            profession=self.profession,
            program_group=related_group,
            relationship_type=ProfessionProgramRelationship.RELATED,
            source=self.source,
            evidence_excerpt="A related mathematics route.",
        )

        self.assertCountEqual(
            self.profession.program_groups.values_list("code", flat=True),
            ["B009", "B055"],
        )

    def test_duplicate_profession_program_mapping_is_rejected(self):
        values = {
            "profession": self.profession,
            "program_group": self.program_group,
            "relationship_type": ProfessionProgramRelationship.DIRECT,
            "source": self.source,
            "evidence_excerpt": "An explicit mapping.",
        }
        ProfessionProgramGroup.objects.create(**values)

        with self.assertRaises(IntegrityError), transaction.atomic():
            ProfessionProgramGroup.objects.create(**values)

    def test_identity_records_require_evidence(self):
        alias = ProgramGroupAlias(**self.alias_values(evidence_excerpt="  "))
        identifier = ProfessionIdentifier(**self.identifier_values(evidence_excerpt="  "))
        link = ProfessionProgramGroup(
            profession=self.profession,
            program_group=self.program_group,
            relationship_type=ProfessionProgramRelationship.DIRECT,
            source=self.source,
            evidence_excerpt="  ",
        )

        for instance in (alias, identifier, link):
            with self.subTest(model=type(instance).__name__):
                with self.assertRaises(ValidationError) as raised:
                    instance.full_clean()
                self.assertIn("evidence_excerpt", raised.exception.message_dict)

    def test_identity_dependencies_are_protected(self):
        identifier = ProfessionIdentifier.objects.create(**self.identifier_values())
        alias = ProgramGroupAlias.objects.create(**self.alias_values())
        link = ProfessionProgramGroup.objects.create(
            profession=self.profession,
            program_group=self.program_group,
            relationship_type=ProfessionProgramRelationship.DIRECT,
            source=self.source,
            evidence_excerpt="An explicit mapping.",
        )

        for protected_object in (
            identifier.profession,
            alias.program_group,
            link.source,
        ):
            with self.subTest(model=type(protected_object).__name__):
                with self.assertRaises(ProtectedError):
                    protected_object.delete()

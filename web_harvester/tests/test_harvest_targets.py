from django.test import TestCase

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
from web_harvester.management.commands.harvest_engine import build_search_targets


class HarvestTargetTests(TestCase):
    def setUp(self):
        self.source = AdmissionSource.objects.create(
            url="https://example.gov.kz/identity",
            publisher="Official authority",
            content_fingerprint="d" * 64,
        )

    def test_targets_use_current_codes_and_label_legacy_codes_as_aliases(self):
        group = EducationalProgramGroup.objects.create(
            code="B009",
            name="Mathematics teacher training",
        )
        profession = Profession.objects.create(
            slug="mathematics-teacher",
            name="Mathematics teacher",
        )
        ProfessionProgramGroup.objects.create(
            profession=profession,
            program_group=group,
            relationship_type=ProfessionProgramRelationship.DIRECT,
            source=self.source,
            evidence_excerpt="Official mapping.",
        )
        ProgramGroupAlias.objects.create(
            program_group=group,
            scheme=ProgramIdentifierScheme.LEGACY_SPECIALTY_CODE,
            value="5B010900",
            source=self.source,
            evidence_excerpt="Official legacy mapping.",
        )
        ProgramGroupAlias.objects.create(
            program_group=group,
            scheme=ProgramIdentifierScheme.ALTERNATIVE_NAME,
            value="Математика мұғалімдерін даярлау",
            language=AliasLanguage.KAZAKH,
            source=self.source,
            evidence_excerpt="Official Kazakh name.",
        )
        ProfessionIdentifier.objects.create(
            profession=profession,
            scheme=ProfessionIdentifierScheme.ALTERNATIVE_NAME,
            value="Учитель математики",
            language=AliasLanguage.RUSSIAN,
            source=self.source,
            evidence_excerpt="Official Russian occupation name.",
        )

        target = build_search_targets(2026)[0]

        self.assertEqual(target.program_group_code, "B009")
        self.assertEqual(target.legacy_codes, ("5B010900",))
        self.assertCountEqual(
            target.alternative_names,
            ("Математика мұғалімдерін даярлау", "Учитель математики"),
        )
        self.assertEqual(target.year, 2026)

    def test_inactive_professions_and_groups_are_excluded(self):
        inactive_group = EducationalProgramGroup.objects.create(
            code="B999",
            name="Inactive group",
            is_active=False,
        )
        profession = Profession.objects.create(
            slug="inactive-profession",
            name="Inactive profession",
        )
        ProfessionProgramGroup.objects.create(
            profession=profession,
            program_group=inactive_group,
            relationship_type=ProfessionProgramRelationship.RELATED,
            source=self.source,
            evidence_excerpt="Old mapping.",
        )

        self.assertEqual(build_search_targets(2026), [])

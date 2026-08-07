"""Seed the minimum canonical identity the harvester needs to build targets.

Run with:  python manage.py shell < scripts/verify/seed_identity.py

Idempotent. Creates one profession, one program group, one evidenced link, and
one university. Without these rows `harvest_engine` correctly does nothing: the
harvester must never invent profession-to-program mappings.
"""

from django.utils import timezone

from apps.careers.models import (
    AdmissionSource,
    EducationalProgramGroup,
    Profession,
    ProfessionProgramGroup,
    ProfessionProgramRelationship,
    ProgramGroupAlias,
    ProgramIdentifierScheme,
    University,
)

profession, _ = Profession.objects.get_or_create(
    slug="vrach",
    defaults={"name": "Врач", "is_active": True},
)
group, _ = EducationalProgramGroup.objects.get_or_create(
    code="B086",
    defaults={"name": "Общая медицина", "is_active": True},
)
university, _ = University.objects.get_or_create(
    code="KAZNMU",
    defaults={
        "name": "Казахский национальный медицинский университет",
        "city": "Алматы",
    },
)
source, _ = AdmissionSource.objects.get_or_create(
    url="https://adilet.zan.kz/rus/docs/V1800017650",
    content_fingerprint="seed-identity-classifier",
    defaults={
        "title": "Классификатор направлений подготовки кадров",
        "publisher": "adilet.zan.kz",
        "retrieved_at": timezone.now(),
        "original_language": "ru",
    },
)
link, _ = ProfessionProgramGroup.objects.get_or_create(
    profession=profession,
    program_group=group,
    defaults={
        "relationship_type": ProfessionProgramRelationship.DIRECT,
        "source": source,
        "evidence_excerpt": "B086 Общая медицина — подготовка врачей общей практики.",
    },
)
ProgramGroupAlias.objects.get_or_create(
    program_group=group,
    scheme=ProgramIdentifierScheme.LEGACY_SPECIALTY_CODE,
    value="5B130100",
    defaults={"source": source, "evidence_excerpt": "5B130100 Общая медицина"},
)

print("profession        :", profession.name, f"({profession.slug})")
print("program group     :", group.code, group.name)
print("university        :", university.code, university.name)
print("identity link     :", link)
print("harvest targets   :", ProfessionProgramGroup.objects.count())

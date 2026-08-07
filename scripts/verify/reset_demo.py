"""Remove everything the verification scripts created.

Run with:  python manage.py shell < scripts/verify/reset_demo.py

Deletes candidate claims, canonical thresholds, and the seeded identity rows.
Leaves any other data alone.
"""

from apps.careers.models import (
    AdmissionSource,
    AdmissionThreshold,
    EducationalProgramGroup,
    Profession,
    ProfessionProgramGroup,
    ProgramGroupAlias,
    University,
)
from web_harvester.models import CandidateClaim
from web_harvester.models import Profession as HarvestedProfession

print("candidate claims     :", CandidateClaim.objects.all().delete())
print("admission thresholds :", AdmissionThreshold.objects.all().delete())
print("harvested professions:", HarvestedProfession.objects.all().delete())
print("identity links       :", ProfessionProgramGroup.objects.all().delete())
print("group aliases        :", ProgramGroupAlias.objects.all().delete())
print("program groups       :", EducationalProgramGroup.objects.all().delete())
print("professions          :", Profession.objects.all().delete())
print("universities         :", University.objects.all().delete())
print("admission sources    :", AdmissionSource.objects.all().delete())

"""Canonical profession fields and source groups for web harvesting.

Common and field-specific domains form the primary search pool. Fallback
domains are considered only when that primary search produces no usable result.
This module defines configuration only; it does not search or validate URLs.
"""

from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType


class FieldType(StrEnum):
    """Supported profession fields, represented by stable stored values."""

    MEDICINE = "medicine"
    EDUCATION = "education"
    TECHNICAL = "technical"
    CREATIVE = "creative"
    BUSINESS_AND_ECONOMICS = "business_economics"
    HUMANITIES_AND_LAW = "humanities_law"
    AGRICULTURE = "agriculture"
    SPORT_AND_TOURISM = "sport_tourism"
    MILITARY_AND_SECURITY = "military_security"


class SourceStrategy(StrEnum):
    """Source group selected for one harvesting attempt."""

    PRIMARY = "primary"
    FALLBACK = "fallback"


COMMON_PRIMARY_DOMAINS = frozenset(
    {
        "adilet.zan.kz",
        "testcenter.kz",
        "egov.kz",
        "gov.kz",
        "zan.gov.kz",
    }
)


FIELD_PRIMARY_DOMAINS: Mapping[FieldType, frozenset[str]] = MappingProxyType(
    {
        FieldType.MEDICINE: frozenset(
            {
                "kaznmu.edu.kz",
                "amu.edu.kz",
                "qmu.edu.kz",
                "smu.edu.kz",
                "ospanov.university",
                "skma.edu.kz",
                "nu.edu.kz",
                "kaznu.kz",
            }
        ),
        FieldType.EDUCATION: frozenset(
            {
                "abai.university",
                "enu.kz",
                "kaznu.kz",
                "sdu.edu.kz",
                "nu.edu.kz",
                "buketov.edu.kz",
                "ppu.edu.kz",
                "dulaty.kz",
                "korkyt.edu.kz",
                "wku.edu.kz",
                "kimep.kz",
            }
        ),
        FieldType.TECHNICAL: frozenset(
            {
                "satbayev.university",
                "kbtu.edu.kz",
                "iitu.edu.kz",
                "astanait.edu.kz",
                "energo.university",
                "kstu.kz",
                "ektu.kz",
                "atu.edu.kz",
                "kaznu.kz",
                "enu.kz",
                "nu.edu.kz",
                "sdu.edu.kz",
            }
        ),
        FieldType.CREATIVE: frozenset(
            {
                "kaznai.kz",
                "kaznui.edu.kz",
                "conservatoire.edu.kz",
                "kazgasa.kz",
                "turan-edu.kz",
                "kaznu.kz",
                "enu.kz",
            }
        ),
        FieldType.BUSINESS_AND_ECONOMICS: frozenset(
            {
                "narxoz.kz",
                "almau.edu.kz",
                "kimep.kz",
                "mnu.kz",
                "turan-edu.kz",
                "kbtu.edu.kz",
                "kaznu.kz",
            }
        ),
        FieldType.HUMANITIES_AND_LAW: frozenset(
            {
                "mnu.kz",
                "ablaikhan.kz",
                "kimep.kz",
                "enu.kz",
                "kaznu.kz",
            }
        ),
        FieldType.AGRICULTURE: frozenset(
            {
                "kaznaru.edu.kz",
                "kazatu.edu.kz",
                "wkatu.edu.kz",
            }
        ),
        FieldType.SPORT_AND_TOURISM: frozenset(
            {
                "kazast.edu.kz",
                "iuth.edu.kz",
            }
        ),
        FieldType.MILITARY_AND_SECURITY: frozenset(
            {
                "alpolac.edu.kz",
            }
        ),
    }
)


# Secondary aggregators are intentionally excluded from high-confidence search.
FALLBACK_DOMAINS = frozenset(
    {
        "studenthub.kz",
        "univision.kz",
    }
)

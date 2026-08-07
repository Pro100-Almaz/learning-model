from typing import Optional
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from apps.careers.models import (
    AdmissionRoute,
    ApplicantBackground,
    FundingType,
    InstructionLanguage,
    ScoreType,
)
from web_harvester.source_policy import FieldType


class StrictSchema(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=True)


def _clean_required_text(value: str, field_name: str) -> str:
    cleaned = " ".join(value.split())
    if not cleaned:
        raise ValueError(f"{field_name} must not be blank")
    return cleaned


class FieldClassification(BaseModel):
    """Structured field selected for one profession."""

    field_type: FieldType = Field(
        description="The single supported field that best matches the profession."
    )


class WebSearch(BaseModel):
    ubt_score: Optional[int] = Field(
        ge=0,
        le=140,
        default=None,
        description=(
            "The minimum threshold score required for this profession on the "
            "Kazakhstan Unified National Testing (ҰБТ/UNT). Must be a valid "
            "whole number between 0 and 140. Set to null if a specific score "
            "threshold is not defined."
        ),
    )

    subjects: list[str] = Field(
        default_factory=list,
        description=(
            "A structured list of elective high school subjects (for example, "
            "Mathematics and Physics) required or highly recommended to qualify "
            "for this profession's academic track. Defaults to an empty list."
        ),
    )

    universities: list[str] = Field(
        default_factory=list,
        description=(
            "A list of accredited higher education institutions offering "
            "specialized degree programs or majors corresponding to this "
            "profession. Defaults to an empty list."
        ),
    )

    sources: list[str] = Field(
        default_factory=list,
        description=(
            "A collection of exact, fully qualified URLs or reliable reference "
            "names visited by the search agent while looking up details for this "
            "profession. Used for validation and citation back-tracking. Defaults "
            "to an empty list."
        ),
    )


class ClaimEvidence(StrictSchema):
    """Exact page evidence supporting one extracted claim."""

    source_url: str = Field(
        min_length=1,
        max_length=1000,
        description="Exact URL of the supplied page supporting this claim.",
    )
    excerpt: str = Field(
        min_length=1,
        max_length=1500,
        description="Short source excerpt, row text, or phrase supporting the claim.",
    )
    location: str | None = Field(
        default=None,
        max_length=250,
        description="Optional page, table, section, row, or file location.",
    )

    @field_validator("source_url")
    @classmethod
    def validate_source_url(cls, value: str) -> str:
        cleaned = value.strip()
        parsed = urlsplit(cleaned)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("source_url must be an absolute http(s) URL")
        return cleaned

    @field_validator("excerpt")
    @classmethod
    def validate_excerpt(cls, value: str) -> str:
        return _clean_required_text(value, "excerpt")

    @field_validator("location")
    @classmethod
    def validate_location(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = " ".join(value.split())
        return cleaned or None


class ThresholdClaim(StrictSchema):
    """Evidence-bearing candidate UNT threshold with explicit context."""

    score: int = Field(
        ge=1,
        le=140,
        description="Exact UNT score stated by the source. Unknown means no claim.",
    )
    score_type: ScoreType = Field(
        description="Legal minimum, university minimum, or historical grant cutoff."
    )
    year: int = Field(
        ge=2000,
        le=2100,
        description="Admission or completed competition year stated by the source.",
    )
    program_group_code: str | None = Field(
        default=None,
        max_length=20,
        description="Official program-group code, or null for a national rule.",
    )
    university_name: str | None = Field(
        default=None,
        max_length=250,
        description="University name when the claim is institution-specific.",
    )
    admission_route: AdmissionRoute | None = Field(
        default=None,
        description="Route stated by the source; null only when truly unknown."
    )
    admission_route_details: str | None = Field(default=None, max_length=250)
    funding_type: FundingType | None = Field(
        default = None,
        description="Funding scope stated by the source; null only when unknown."
    )
    applicant_background: ApplicantBackground | None = Field(
        default=None,
        description="Applicant background stated by the source; null only when unknown."
    )
    applicant_background_details: str | None = Field(default=None, max_length=250)
    quota_category: str | None = Field(
        default=None,
        description="Quota or competition category stated by the source."
    )
    instruction_language: InstructionLanguage | None = Field(
        default=None,
        description="Instruction language stated by the source; null only when unknown."
    )
    evidence: ClaimEvidence

    @field_validator(
        "program_group_code",
        "university_name",
        "admission_route_details",
        "applicant_background_details",
        "quota_category",
        mode="after",
    )
    @classmethod
    def clean_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = " ".join(value.split())
        return cleaned or None

    @model_validator(mode="after")
    def validate_context_dependencies(self):
        if (
            self.score_type == ScoreType.UNIVERSITY_MINIMUM
            and self.university_name is None
        ):
            raise ValueError("university minimum claims require university_name")
        if (
            self.score_type == ScoreType.HISTORICAL_GRANT_CUTOFF
            and self.funding_type != FundingType.GRANT
        ):
            raise ValueError("historical grant cutoff claims require grant funding")
        return self


class ProgramIdentityClaim(StrictSchema):
    """Evidence that a program group or name matches the target profession."""

    program_group_code: str | None = Field(default=None, max_length=20)
    program_group_name: str = Field(min_length=1, max_length=250)
    evidence: ClaimEvidence

    @field_validator("program_group_name")
    @classmethod
    def validate_program_group_name(cls, value: str) -> str:
        return _clean_required_text(value, "program_group_name")

    @field_validator("program_group_code")
    @classmethod
    def validate_program_group_code(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = " ".join(value.split())
        return cleaned or None


class ProfileSubjectRequirementClaim(StrictSchema):
    """Evidence-bearing profile subject requirement for a program group."""

    subjects: list[str] = Field(min_length=1)
    year: int | None = Field(default=None, ge=2000, le=2100)
    program_group_code: str | None = Field(default=None, max_length=20)
    evidence: ClaimEvidence

    @field_validator("subjects")
    @classmethod
    def validate_subjects(cls, value: list[str]) -> list[str]:
        return [_clean_required_text(subject, "subjects") for subject in value]

    @field_validator("program_group_code")
    @classmethod
    def validate_subject_program_group_code(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = " ".join(value.split())
        return cleaned or None


class UniversityOfferingClaim(StrictSchema):
    """Evidence that a university offers the target group or program."""

    university_name: str = Field(min_length=1, max_length=250)
    program_group_code: str | None = Field(default=None, max_length=20)
    program_name: str | None = Field(default=None, max_length=250)
    year: int | None = Field(default=None, ge=2000, le=2100)
    evidence: ClaimEvidence

    @field_validator("university_name")
    @classmethod
    def validate_university_name(cls, value: str) -> str:
        return _clean_required_text(value, "university_name")

    @field_validator("program_group_code", "program_name")
    @classmethod
    def clean_offering_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = " ".join(value.split())
        return cleaned or None

    @model_validator(mode="after")
    def validate_offering_identity(self):
        if self.program_group_code is None and self.program_name is None:
            raise ValueError(
                "university offerings require program_group_code or program_name"
            )
        return self


class AdmissionExtraction(StrictSchema):
    """Structured candidate claims extracted from supplied source pages."""

    program_identities: list[ProgramIdentityClaim] = Field(default_factory=list)
    threshold_claims: list[ThresholdClaim] = Field(default_factory=list)
    subject_requirements: list[ProfileSubjectRequirementClaim] = Field(
        default_factory=list
    )
    university_offerings: list[UniversityOfferingClaim] = Field(default_factory=list)

    def source_urls(self) -> list[str]:
        urls = [
            claim.evidence.source_url
            for claims in (
                self.program_identities,
                self.threshold_claims,
                self.subject_requirements,
                self.university_offerings,
            )
            for claim in claims
        ]
        return list(dict.fromkeys(urls))

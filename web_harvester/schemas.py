'''
Structures of the output for agent 1
'''

from __future__ import annotations
from pydantic import BaseModel, Field

class HarvestedSource(BaseModel):
    url: str
    raw_text: str
    fetched_at: str = Field(description = "ISO-8601 UTC timestamp of the fetch")
    reachable: bool = Field(description = "True only if the fetch returned page content; False for dead link/timeout/empty.")

class HarvestResult(BaseModel):
    specialty_code: str
    sources: list[HarvestedSource]

class Provenance(BaseModel):
    value: str | float | None = Field(description = "The extracted value, or Null if absent")
    as_of: int | None = Field(description = "Year the value applies to, None if absent")
    carried_forward: bool = Field(default = False, description = "Always False at extraction")

class University(BaseModel):
    name: Provenance
    passing_score: Provenance
    tuition: Provenance

class SpecialtyDocument(BaseModel):
    specialty_code: str
    field: Provenance
    subject_combination: Provenance
    threshold: Provenance
    universities: list[University]
    professions: list[Provenance]
    source_year_confidence: str = Field(description = "'low' when year is inferred, else 'high'")


from pydantic import BaseModel, Field
from typing import Optional

class WebSearch(BaseModel):
    ubt_score: Optional[int] = Field(ge = 0, le = 140, default = None, description = "The minimum threshold score required for this profession on the "
                                                                     "Kazakhstan Unified National Testing (ҰБТ/UNT). Must be a valid whole number "
                                                                     "between 0 and 140. Set to null if a specific score threshold is not defined.")

    subjects: list[str] = Field(default_factory = list, description = "A structured list of elective high school subjects (e.g., ['Mathematics', 'Physics']) "
                                                                           "required or highly recommended to qualify for this profession's academic track. "
                                                                           "Defaults to an empty list.")

    universities: list[str] = Field(default_factory = list, description = "A list of accredited higher education institutions (universities or academies) "
                                                                               "offering specialized degree programs or majors corresponding to this profession. "
                                                                               "Defaults to an empty list.")

    sources: list[str] = Field(default_factory = list, description = "A collection of exact, fully qualified URLs or reliable reference names "
                                                                          "visited by the search agent while looking up details for this profession. "
                                                                          "Used for validation and citation back-tracking. Defaults to an empty list.")

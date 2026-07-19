from pydantic import BaseModel, Field

class AnalysisOut(BaseModel):
    summary: str = Field(default="", description = "give a 2–3 sentence plain-language read of where the student stands — name their strongest area and their single biggest gap.")
    priorities: list[str] = Field(default_factory=list, description = "list the weak/improving topics to tackle first, most-urgent first; each item names one topic and one concrete action")
    university_outlook: str = Field(default="", description = "state what their current math predicts, and — if there's a near-miss — motivate with the exact points needed ('ты в N баллах от …'); do not invent professions not in the db, be straight")
    encouragement: str = Field(default="", description = "write one short, warm closing line — honest, not fluffy")
    language: str = Field(default = "", description = "The language this analysis is written in, e.g. 'russian' or 'kazakh', matching the requested language.")
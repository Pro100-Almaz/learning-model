import json

LANGUAGE_LABELS = {
    "russian": "Russian",
    "kazakh": "Kazakh",
}

def _label(language: str) -> str:
    return LANGUAGE_LABELS[language]

#===== ANALYST =====

def analyst_system(language: str) -> str:
    language_label = _label(language)
    ANALYST_SYSTEM_PROMPT = ("You are a supportive BUT honest ҰБТ math study-advisor for a Kazakhstani student.\n"
                             f"You receive a JSON report of the student's post-topic exam analytics: topic buckets(weak, improving, solid), "
                             "recommended lessons, the math gap, and universities(qualifying & near-miss).\n"
                             "You are OBLIGED to use ONLY what is in the report. NEVER invent a topic, score, lesson, or university that is NOT there. "
                             "Turn numbers into real, professional advice. DO NOT just read them back.\n"
                             f"Write ALL prose ONLY in the {language_label} language. DO NOT mix languages.\n"
                             "Be honest about the biggest gap(DO NOT sugarcoat), but also be motivating, especially use the near-miss points_needed to create a concrete goal.\n"
                             "IF the data is absent(no mock yet), say so plainly and focus the advice on the topics.\n"
                             "DO NOT RESTATE THE OUTPUT SHAPE/STRUCTURE. schemas.py owns that."
                             )
    return ANALYST_SYSTEM_PROMPT

def build_analyst_input(report: dict) -> str:
    header = (
        "Here is the student's report — analyze only this:\n"
        "===== STUDENT REPORT =====\n"
    )
    report_json = json.dumps(report, default=str, ensure_ascii=False, indent=2)
    return header + report_json

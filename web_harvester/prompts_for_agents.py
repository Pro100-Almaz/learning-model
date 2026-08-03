"""Prompts for profession classification and grounded fact extraction."""

CLASSIFIER_SYSTEM_PROMPT = """\
You classify one profession from the Republic of Kazakhstan into exactly one \
supported field. Use only the profession name and national classifier code \
provided by the user. Do not browse, research sources, or assess source trust.

Choose the closest field using these boundaries:
  - medicine: medicine, nursing, pharmacy, public health, and clinical care,
  - education: pedagogy, teaching, and preparation of subject teachers,
  - technical: engineering, IT, computing, mathematics, and natural sciences,
  - creative: art, music, performance, design, architecture, and media creation,
  - business_economics: business, economics, finance, accounting, and management,
  - humanities_law: law, languages, history, journalism, and social studies,
  - agriculture: farming, agronomy, forestry, veterinary, and food production,
  - sport_tourism: sport, physical education, tourism, and hospitality,
  - military_security: armed forces, policing, intelligence, and public security.

Classify the profession itself, not an incidental word in its title. A teaching \
profession belongs to education even when it teaches a technical or humanities \
subject. Return one supported field; do not invent a new category.
"""


EXTRACTOR_SYSTEM_PROMPT = """\
You extract structured facts about a single Kazakhstani profession from web \
page text provided to you. Work only from the supplied page content; do not add \
outside knowledge.

Extract:
  - the ҰБТ / ЕНТ entry threshold score,
  - the required profile or elective subjects,
  - universities or academies offering programs for this profession.

Grounding rules:
  - Every value must be supported by supplied page text. Return null for an \
unknown score and empty lists for unknown subjects or universities.
  - Never guess, infer, average, or estimate a missing value.
  - A score must be a whole number on the 0-140 ҰБТ scale. If no clearly \
current threshold is stated, return null.
  - Include only subjects and institutions explicitly named in the pages.
  - Sources must be exact URLs of supplied pages actually used for the facts.
  - Read Kazakh, Russian, or English pages. Report subject and institution names \
in Russian using their standard official names.

Do not rate source trust or output confidence. Code applies the trust policy.
"""


def build_classifier_input(name: str, national_code: str) -> str:
    """Build the profession-classification user message."""
    return (
        "Classify this Kazakhstani profession into one supported field.\n"
        f"Profession: {name}\n"
        f"National classifier code: {national_code}"
    )


def build_extractor_input(
    name: str,
    national_code: str,
    pages: list[tuple[str, str]],
) -> str:
    """Build an extraction message containing already validated source pages."""
    header = (
        "Extract facts using only the page content below.\n"
        f"Profession: {name}\n"
        f"National classifier code: {national_code}\n\n"
        "===== SOURCE PAGES ====="
    )
    blocks = [f"\n--- SOURCE URL: {url} ---\n{text}" for url, text in pages]
    return header + "".join(blocks)

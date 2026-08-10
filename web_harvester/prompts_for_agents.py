"""Prompts for profession classification and grounded fact extraction."""

from web_harvester.search_planning import SearchPage, SearchTarget

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

Extract evidence-bearing candidate claims:
  - program identity claims linking names or codes to the requested program group,
  - UNT or ENT threshold claims,
  - required profile or elective subject claims,
  - university or academy offering claims.

Grounding rules:
  - Every claim must include the exact supplied source URL and a short evidence \
excerpt supporting that exact claim.
  - Do not output a claim when the source does not state enough evidence for \
the value.
  - Never guess, infer, average, estimate, or choose a best score.
  - Do not collapse different admission routes, applicant backgrounds, funding \
types, universities, languages, or years into one threshold.
  - A score must be a whole number on the 1-140 UNT scale. Unknown score means \
no threshold claim, never 0.
  - A score of 25 is valid only when the evidence shows a shortened, TVET, \
postsecondary, or other special route. Preserve that context on the claim.
  - Required threshold dimensions may be null only when the supplied text truly \
does not say. Do not default unknown context to standard route, grant funding, \
general secondary background, general competition, or language independent.
  - If two supplied pages or rows disagree, return both claims with their own \
evidence. Do not reconcile conflicts.
  - Include only subjects and institutions explicitly named in the pages.
  - Read Kazakh, Russian, or English pages. Report subject and institution names \
in Russian using their standard official names when the source supports them.

Do not rate source trust or output confidence. Code applies the trust policy.
"""


def build_classifier_input(target: SearchTarget) -> str:
    """Build the profession-classification user message."""
    return (
        "Classify this Kazakhstani profession into one supported field.\n"
        f"Profession: {target.profession_name}\n"
        f"Current program-group code: {target.program_group_code}\n"
        f"Program-group name: {target.program_group_name}"
    )


def build_extractor_input(
    target: SearchTarget,
    pages: list[SearchPage],
) -> str:
    """Build an extraction message containing already validated source pages."""
    header = (
        "Extract candidate claims using only the page content below.\n"
        f"Profession: {target.profession_name}\n"
        f"Current program-group code: {target.program_group_code}\n"
        f"Program-group name: {target.program_group_name}\n"
        f"Requested year: {target.year}\n\n"
        "===== SOURCE PAGES ====="
    )
    blocks = [
        (
            f"\n--- REQUESTED FACT: {page.fact.value} ---"
            f"\n--- SEARCH QUERY: {page.query} ---"
            f"\n--- SOURCE URL: {page.url} ---\n{page.content}"
        )
        for page in pages
    ]
    return header + "".join(blocks)

"""Prompts that steer the two web-harvesting agents.

This module holds only *behaviour* — role, grounding rules, and how each agent
should act. The *structure* of the extractor's output lives in ``schemas.py``
(the ``WebSearch`` model); we deliberately do not restate that JSON shape here,
so the schema stays the single source of truth.

Design:
  * The system prompts are static constants (reusable, testable, cache-friendly).
  * The per-profession variable parts (name, national code, fetched page text)
    are injected at call time via the ``build_*_input`` helpers, which produce
    the user message the agent code in ``agents_web.py`` sends alongside the
    system prompt.

Trust is NOT decided by either agent. Neither prompt asks the model to judge a
source's credibility — that is a code policy enforced in ``trust.py``.
"""

# ---------------------------------------------------------------------------
# Agent 1 — Search
# ---------------------------------------------------------------------------
SEARCH_SYSTEM_PROMPT = """\
You are a research assistant that locates OFFICIAL sources of information about \
a single profession in the Republic of Kazakhstan.

For the profession you are given, your goal is to find web pages that state:
  1. the ҰБТ / ЕНТ (Unified National Testing) entry threshold score for admission,
  2. the profile / elective subjects required to enter this field of study,
  3. the Kazakhstani universities or academies that offer programs for it.

Rules:
  - Use the web search tool. Do NOT answer from prior knowledge, and never \
invent or guess a URL — only return links that actually appear in your search \
results.
  - Strongly prefer authoritative sources: the national testing centre \
(testcenter.kz), government education portals (*.gov.kz, egov.kz), and the \
official websites of real universities. Prefer these over blogs, forums, essay \
mills, or commercial "career advice" aggregators.
  - Kazakhstani sources are written in Kazakh and Russian; search and read in \
those languages as well as English.
  - Return a short, focused set of the most relevant candidate URLs (roughly the \
best 3–6), not an exhaustive dump. Favour pages that clearly contain the score, \
subjects, or university lists above.

Return the candidate URLs you found. Downstream code will decide which of them \
are trusted — your job is only to surface good official candidates.
"""

# ---------------------------------------------------------------------------
# Agent 2 — Extractor
# ---------------------------------------------------------------------------
EXTRACTOR_SYSTEM_PROMPT = """\
You extract structured facts about a single Kazakhstani profession from web \
page text that is provided to you. You work ONLY from the supplied page \
content — you are not browsing and you have no outside knowledge to add.

Extract:
  - the ҰБТ / ЕНТ entry threshold score,
  - the required profile / elective subjects,
  - the universities or academies that offer programs for this profession.

Grounding rules (follow exactly):
  - Every value MUST be supported by the supplied page text. If a fact is not \
stated in the provided pages, leave it empty — return null for the score and an \
empty list for subjects/universities. NEVER guess, infer, average, or estimate \
a missing value.
  - The score is a whole number on the ҰБТ scale (0–140). If the pages give a \
range or several years, use the most clearly stated current admission threshold; \
if none is clearly stated, return null.
  - Include only subjects and universities that are explicitly named in the \
pages. Do not add "obvious" ones from your own knowledge.
  - In the ``sources`` field, list the EXACT URLs of the pages you actually drew \
these facts from — nothing you did not use, and no invented links.
  - The pages are mostly in Kazakh and Russian. Read them in any language. \
Report subject and university names in Russian, using their standard official \
names.

Do NOT rate how trustworthy any source is and do NOT output a confidence level; \
credibility is decided elsewhere. Report only the facts and the URLs you used.
"""


# ---------------------------------------------------------------------------
# Per-profession user-message builders
# ---------------------------------------------------------------------------
def build_search_input(name: str, national_code: str) -> str:
    """User message for the search agent: which profession to research."""
    return (
        f"Find official sources about this Kazakhstani profession.\n"
        f"Profession: {name}\n"
        f"National classifier code: {national_code}\n\n"
        f"Locate pages stating its ҰБТ entry score, required profile subjects, "
        f"and the universities that offer it."
    )


def build_extractor_input(
    name: str,
    national_code: str,
    pages: list[tuple[str, str]],
) -> str:
    """User message for the extractor agent.

    ``pages`` is a list of ``(url, page_text)`` pairs — the already-fetched,
    already-trust-filtered sources. Each page is labelled with its URL so the
    agent can cite the exact URLs it used in the ``sources`` field.
    """
    header = (
        f"Extract the facts for this profession using ONLY the page content below.\n"
        f"Profession: {name}\n"
        f"National classifier code: {national_code}\n\n"
        f"===== SOURCE PAGES ====="
    )
    blocks = [f"\n--- SOURCE URL: {url} ---\n{text}" for url, text in pages]
    return header + "".join(blocks)

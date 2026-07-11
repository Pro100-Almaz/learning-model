from __future__ import annotations

HARVESTER_SYSTEM = (
      "You research ONE Kazakhstani higher-education specialty. Use only the "
      "`web_search` and `fetch_url` tools provided — never rely on prior "
      "knowledge for facts.\n"
      "- You may fetch ONLY from the allowlisted hosts you are given. Never fetch "
      "any other host; the fetch tool will refuse it anyway.\n"
      "- Find, for the CURRENT admission cycle: the ҰБТ/UNT subject-combination, "
      "the threshold score, the state-grant (грант) count, and the universities "
      "that offer the specialty (with passing score and tuition).\n"
      "- Capture the RAW page text of each source. Do NOT summarize, translate, "
      "reformat, or interpret it — later stages handle that.\n"
      "- Prefer Tier-1 official sources; use university sites to fill gaps.\n"
      "- Report each source with its URL and whether the fetch returned content."
  )

def build_harvester_prompt(specialty_code: str, allowed: frozenset[str]) -> str:
    hosts = ", ".join(sorted(allowed))
    return (
        f"Research specialty: {specialty_code} "
        f"You may only fetch from these hosts: {hosts} "
        f"Find the current-cycle subject-combination, threshold score, "
        f"grant count, and the universities that offer the specialty (with passing score)"
    )

EXTRACTOR_SYSTEM = (
    "You convert RAW Kazakhstani university-admission source text into ONE "
    "structured specialty document. Use ONLY the text provided — never add "
    "facts from prior knowledge.\n"
    "- Wrap EVERY value in the provenance envelope {value, as_of, carried_forward}. "
    "Set carried_forward to false always. Set as_of to the year the value applies to.\n"
    "- Read the year from the document's OWN title/headers. If you had to infer it, "
    "set source_year_confidence to \"low\"; otherwise \"high\".\n"
    "- Bilingual rule: Kazakh is canonical, Russian goes in parentheses. If only "
    "ONE language is present, store it as-is and flag the missing side. "
    "NEVER machine-translate.\n"
    "- If a field is absent in the sources, set its value to null. Do NOT guess."
)

def build_extractor_prompt(specialty_code: str, sources: list) -> str:
    blocks = "\n\n".join(
        f"SOURCE: {s.url}\n{s.raw_text}" for s in sources if s.reachable
    )
    return (
        f"Specialty code: {specialty_code}\n"
        f"Extract field, subject_combination, threshold, grants, universities[] "
        f"(name, passing_score, tuition) and professions[] from these sources: \n\n"
        f"{blocks}"
    )




'''
  If you'd rather lean fully on OpenAI JSON responses for Agent 1 too, there's a middle path: OpenAI's
  Responses API offers a built-in web_search tool plus JSON structured output in the same call. The
  catch is the allowlist — with the built-in search you don't control which URLs it fetches, so you'd
  have to gate/verify hosts after the fact rather than before fetching (which is what Part 1 asks
  for). That's the trade-off to weigh: less code vs. weaker pre-fetch enforcement.
'''
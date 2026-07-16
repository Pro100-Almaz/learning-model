from urllib.parse import urlparse
from typing import Literal, Tuple, Optional, List

allowlist = {
    "Tier_1": frozenset({
        "testcenter.kz",
        "univision.kz",
        "gov.kz",
        "egov.kz",
    }), #1st tier sources: the credible ones
    "Tier_2": frozenset({
        "sdu.edu.kz",
        "kaznu.edu.kz",
        "enic-kazakhstan.edu.kz"
    }), #2nd tier sources: applied when 1st tier is lacking info
}

def domain_of(url): #extracting the domain of the source
    if not url:
        return ""

    if not url.startswith(('http://', 'https://')):
        parsed = urlparse(f"http://{url}")
    else:
        parsed = urlparse(url)

    domain = parsed.hostname or ""

    domain = domain.removeprefix("www.")

    return domain

def tier_of(url):
    domain = domain_of(url)

    if not domain:
        return None

    def matches_tier(target_domain: str, allowed_set: frozenset) -> bool:
        for allowed in allowed_set:
            suffix = "." + allowed
            if target_domain == allowed or target_domain.endswith(suffix):
                return True
        return False

    if matches_tier(domain, allowlist["Tier_1"]):
        return 1

    if matches_tier(domain, allowlist["Tier_2"]):
        return 2

    return None

def filter_and_rank(urls: list[str]) -> list[str]:
    ranked_urls: List[tuple[str, int]] = []

    for url in urls:
        tier_url = tier_of(url)
        if tier_url is not None:
            ranked_urls.append((url, tier_url))

    ranked_urls.sort(key = lambda item: item[1])

    return [url for url, tier_url in ranked_urls]

def confidence_for(tier) -> Literal["High", "Low"]:
    if tier == 1:
        return "High"
    elif tier == 2:
        return "Low"

def stamp(sources : list[str]) -> Tuple[Optional[int], Optional[Literal["High", "Low"]]]:
    if not sources:
        return None, None

    detected_tiers = []
    for url in sources:
        tier = tier_of(url)
        if tier is not None:
            detected_tiers.append(tier)

    if not detected_tiers:
        return None, None

    best_tier = min(detected_tiers)

    confidence = confidence_for(best_tier)

    return best_tier, confidence

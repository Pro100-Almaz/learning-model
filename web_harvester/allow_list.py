from __future__ import annotations
from urllib.parse import urlparse # for pulling hostname from url

PRIMARY_HOSTS: frozenset[str] = frozenset({
    "testcenter.kz",
    "gov.kz",
    "egov.kz"
})

# Tier-2: the 5 most ҰБТ-based universities — the national universities that
# admit the bulk of their students on ҰБТ-score state grants (bare registrable
# domain; is_allowed also matches subdomains like welcome.kaznu.kz).
SECONDARY_HOSTS: frozenset[str] = frozenset({
    "kaznu.kz",              # Al-Farabi Kazakh National University (KazNU)
    "enu.kz",                # L.N. Gumilyov Eurasian National University (ENU)
    "satbayev.university",   # Satbayev University (KazNTU)
    "kaznpu.kz",             # Abai Kazakh National Pedagogical University
    "kaznaru.edu.kz",        # Kazakh National Agrarian Research University
    "buketov.edu.kz",
    "auezov.edu.kz",
    "sdu.edu.kz",
})

def allowed_hosts() -> frozenset[str]:
    return PRIMARY_HOSTS | SECONDARY_HOSTS
    #return frozenset(union(PRIMARY_HOSTS, SECONDARY_HOSTS))

def is_allowed(url:str) -> bool:
    hostname = urlparse(url).hostname
    if hostname is None:
        return False
    hostname = hostname.lower()
    allowed = allowed_hosts()
    return hostname in allowed or any(
        hostname.endswith("." + host) for host in allowed
    )

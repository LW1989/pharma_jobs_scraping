"""Shared URL helpers."""

import re
from urllib.parse import urlparse, urlunparse

# Ad and analytics parameters the research spreadsheet carries. They change per
# click, so leaving them in makes an unchanged career page look like a changed
# one on every sync — and they are noise in the stored job URL either way.
_TRACKING_PARAM_RE = re.compile(
    r"^(gclid|dclid|gclsrc|gad_source|gad_campaignid|gbraid|wbraid|srsltid|"
    r"fbclid|msclkid|igshid|mc_[a-z]+|pk_[a-z]+|utm_[a-z]+|tw_[a-z]+|"
    r"_su_rec.*|pflclid)$",
    re.IGNORECASE,
)


def strip_tracking_params(url: str) -> str:
    """Drop the fragment and any ad-tracking query parameters from a URL."""
    if not url:
        return url
    parts = urlparse(url)
    if parts.query:
        kept = [
            kv for kv in parts.query.split("&")
            if not _TRACKING_PARAM_RE.match(kv.split("=", 1)[0])
        ]
        parts = parts._replace(query="&".join(kept))
    return urlunparse(parts._replace(fragment=""))



_CAREER_PATH_RE = re.compile(
    r"karriere|career|jobs?|stellen|vacanc|bewerb|mit-?uns-?arbeiten", re.IGNORECASE
)

# Hosted ATS boards — a link here is a researched career page even with no
# path keyword (join.com/companies/{slug} carries none).
_ATS_HOST_RE = re.compile(
    r"(personio\.(de|com)|recruitee\.com|workable\.com|join\.com|talent-soft\.com|"
    r"softgarden\.(io|de)|greenhouse\.io|lever\.co|ashbyhq\.com|smartrecruiters\.com|"
    r"myworkdayjobs\.com|successfactors\.(com|eu)|jobs\.sap\.com|rexx-systems\.com|"
    r"dvinci-hr\.com|concludis\.de|jobvector\.de|onlyfy\.jobs|prescreen\.io|"
    r"teamtailor\.com|d-vinci\.de)",
    re.IGNORECASE,
)

# Company-owned career subdomains (jobs.acme.de, career.medios.group, …).
_CAREER_SUBDOMAIN_RE = re.compile(
    r"^(www\.)?(jobs?|careers?|karriere|stellen)\.",
    re.IGNORECASE,
)


def canonical(url: str) -> str:
    """Tracking-free, trailing-slash-insensitive form, for comparing two URLs."""
    return strip_tracking_params(url or "").rstrip("/")


def _host_key(netloc: str) -> str:
    """Lowercased host without a leading www., for identity comparison."""
    host = (netloc or "").lower().split(":", 1)[0]
    return host[4:] if host.startswith("www.") else host


def _looks_like_career(parts) -> bool:
    """True when the URL's host or path signals a career/ATS listing page."""
    host = (parts.netloc or "").lower()
    if _ATS_HOST_RE.search(host) or _CAREER_SUBDOMAIN_RE.match(host):
        return True
    return bool(_CAREER_PATH_RE.search(parts.path or ""))


def is_less_specific(candidate: str, current: str) -> bool:
    """
    True when `candidate` is a weaker career URL than `current`.

    The research spreadsheet often holds a homepage or a section index where a
    person has since found the actual listing page — including an ATS board or
    a jobs./career. subdomain. A sync must not walk that back — but it must
    still accept a genuinely different career URL.
    """
    cand, cur = urlparse(canonical(candidate)), urlparse(canonical(current))
    cand_host, cur_host = _host_key(cand.netloc), _host_key(cur.netloc)

    if cand_host != cur_host:
        # Sheet homepage (or any non-career URL) must not displace a researched
        # ATS / career-subdomain page. A sheet URL that itself looks
        # career-specific is a genuine move and is accepted.
        return (not _looks_like_career(cand)) and _looks_like_career(cur)

    if not cur.path.strip("/"):
        return False
    cand_segs = [seg for seg in cand.path.split("/") if seg]
    cur_segs = [seg for seg in cur.path.split("/") if seg]
    if cur_segs[: len(cand_segs)] == cand_segs and len(cur_segs) > len(cand_segs):
        # "" or ["en"] against ["en", "karriere-bei-uns"] — ours is deeper on
        # the same path. Segment-wise so /car does not read as a prefix of
        # /career.
        return True
    # Neither contains the other: keep ours only if ours looks like a career
    # page and theirs does not (e.g. /de/karriere vs /de/home).
    return bool(_CAREER_PATH_RE.search(cur.path)) and not _CAREER_PATH_RE.search(cand.path)

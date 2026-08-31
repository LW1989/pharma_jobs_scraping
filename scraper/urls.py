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


def is_bare_homepage(url: str) -> bool:
    """True when the URL has no meaningful path (scheme://host, or host/)."""
    return not urlparse(url or "").path.strip("/")


_CAREER_PATH_RE = re.compile(
    r"karriere|career|jobs?|stellen|vacanc|bewerb|mit-?uns-?arbeiten", re.IGNORECASE
)


def canonical(url: str) -> str:
    """Tracking-free, trailing-slash-insensitive form, for comparing two URLs."""
    return strip_tracking_params(url or "").rstrip("/")


def is_less_specific(candidate: str, current: str) -> bool:
    """
    True when `candidate` is a weaker career URL than `current`, same host.

    The research spreadsheet often holds a homepage or a section index where a
    person has since found the actual listing page. A sync must not walk that
    back — but it must still accept a genuinely different URL.
    """
    cand, cur = urlparse(canonical(candidate)), urlparse(canonical(current))
    if not cur.path or cand.netloc != cur.netloc:
        return False
    if cur.path.startswith(cand.path):
        # "" or "/en" against "/en/karriere-bei-uns"
        return True
    # Neither contains the other: keep ours only if ours looks like a career
    # page and theirs does not (e.g. /de/karriere vs /de/home).
    return bool(_CAREER_PATH_RE.search(cur.path)) and not _CAREER_PATH_RE.search(cand.path)

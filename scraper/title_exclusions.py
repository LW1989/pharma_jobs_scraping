"""
Shared title-keyword exclusion logic for prescreener and NRW major insert.

Uses requirements.yaml exclude_title_keywords with special handling for
false positives (e.g. "Head of" vs "Functional head of …").
"""

from __future__ import annotations

import re

# Senior "Head of [function]" — not "Functional head of quality control" (DE lab lead).
_HEAD_OF_RE = re.compile(r"(?<![a-zäöüß])head of(?![a-zäöüß])", re.IGNORECASE)
_FUNCTIONAL_HEAD_RE = re.compile(
    r"(?:functional|funktional(?:er|e)?)\s+head\s+of",
    re.IGNORECASE,
)

# Whole-word intern (trainee), not international / internal / …
_INTERN_TRAINEE_RE = re.compile(
    r"(?<![a-zäöüß])intern(?![a-zäöüß])",
    re.IGNORECASE,
)


def title_matches_exclude_keyword(title: str, keyword: str) -> bool:
    """Case-insensitive match for one exclude_title_keywords entry."""
    if not (title or "").strip() or not (keyword or "").strip():
        return False

    kw_lower = keyword.strip().lower()
    title_cf = title.casefold()

    if kw_lower == "head of":
        if _FUNCTIONAL_HEAD_RE.search(title):
            return False
        return bool(_HEAD_OF_RE.search(title))

    return kw_lower in title_cf


def is_excluded_job_title(
    title: str,
    exclude_keywords: list[str] | None = None,
    *,
    include_intern_rules: bool = False,
) -> tuple[bool, str]:
    """
    Return (excluded, matched_keyword_or_rule).

    When include_intern_rules is True, also rejects internship / praktikum /
    whole-word intern (used by NRW insert where titles come from ATS listings).
    """
    if not (title or "").strip():
        return False, ""

    t = title.casefold()

    if include_intern_rules:
        if "internship" in t:
            return True, "internship"
        if any(x in t for x in ("praktikum", "praktikant", "praktika")):
            return True, "praktikum"
        if _INTERN_TRAINEE_RE.search(title):
            return True, "intern"

    for kw in exclude_keywords or []:
        if title_matches_exclude_keyword(title, kw):
            return True, kw

    return False, ""

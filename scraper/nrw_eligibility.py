"""
Eligibility for NRW major-employer jobs:
  Remote (DE/EU) OR hybrid+NRW OR on-site/in-office with NRW location in listing+detail text.
  listing_nrw_scoped (per employer): URL already filters to NRW/office scope — trust unless US-only.

Config: input_data/nrw_eligibility.yaml
"""

from __future__ import annotations

import json
import logging
import re
from functools import lru_cache
from pathlib import Path

import yaml

from scraper.title_exclusions import is_excluded_job_title

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
YAML_PATH = ROOT / "input_data" / "nrw_eligibility.yaml"


@lru_cache(maxsize=1)
def _cfg() -> dict:
    if not YAML_PATH.exists():
        logger.warning("nrw_eligibility.yaml missing — no jobs pass filter")
        return {}
    with YAML_PATH.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _lower_list(key: str) -> list[str]:
    return [str(x) for x in (_cfg().get(key) or []) if x]


def location_in_nrw(text: str) -> bool:
    if not text:
        return False
    low = text.lower()
    for kw in _cfg().get("nrw_location_keywords") or []:
        if kw and str(kw).lower() in low:
            return True
    return False


def location_in_benelux(text: str) -> bool:
    if not text:
        return False
    low = text.lower()
    for kw in _cfg().get("benelux_location_keywords") or []:
        if kw and str(kw).lower() in low:
            return True
    return False


def _text_has_any(haystack: str, needles: list[str]) -> bool:
    low = haystack.lower()
    return any(n.lower() in low for n in needles if n)


def text_suggests_us_only_remote(text: str) -> bool:
    return _text_has_any(text, _lower_list("us_only_remote_signals"))


def text_suggests_de_eu_emea(text: str) -> bool:
    return _text_has_any(text, _lower_list("remote_region_hints"))


def text_suggests_hybrid(text: str) -> bool:
    return _text_has_any(text, _lower_list("hybrid_keywords"))


def text_suggests_remote(text: str) -> bool:
    low = (text or "").lower()
    if "remote" in low and "not remote" not in low:
        return True
    return _text_has_any(text, _lower_list("remote_keywords"))


def text_suggests_onsite(text: str) -> bool:
    return _text_has_any(text, _lower_list("onsite_keywords"))


def job_text_eligible(location: str, detail_text: str) -> bool:
    """
    Used for HTML job pages (SuccessFactors, Workday text dump).
    """
    blob = f"{location or ''}\n{detail_text or ''}"
    if text_suggests_us_only_remote(blob):
        return False

    in_nrw = location_in_nrw(blob)
    hybrid = text_suggests_hybrid(blob)
    remote = text_suggests_remote(blob)

    if hybrid and in_nrw:
        return True
    if hybrid and not in_nrw:
        # e.g. "hybrid role based in Berlin" — reject unless NRW in text
        return False

    if remote:
        if not text_suggests_de_eu_emea(blob):
            # Germany as work country in location only
            if not _germany_or_neighbour_location(location):
                return False
        return True

    # On-site / in-office: NRW city in location or detail is enough (no hybrid/remote keyword needed)
    if in_nrw:
        return True

    return False


def _location_suggests_remote(loc: str) -> bool:
    """True when the location line/slug is explicitly a remote posting."""
    if not loc:
        return False
    first = loc.split(",")[0].strip().lower()
    if "remote" in first:
        return True
    return text_suggests_remote(loc)


def job_text_eligible_nrw_benelux_remote(location: str, detail_text: str) -> bool:
    """
    NRW or Benelux on-site/hybrid; DE/EU remote/home office. Rejects other DE on-site.
    When location is set (e.g. from Workday URL slug), geo checks use it — not full page body.
    """
    blob = f"{location or ''}\n{detail_text or ''}"
    if text_suggests_us_only_remote(blob):
        return False

    geo = location.strip() if location else blob
    in_nrw = location_in_nrw(geo)
    in_benelux = location_in_benelux(geo)
    concrete_onsite_loc = bool(location.strip()) and not _location_suggests_remote(location)

    if concrete_onsite_loc:
        return in_nrw or in_benelux

    if _location_suggests_remote(location):
        if not text_suggests_de_eu_emea(blob) and not _germany_or_neighbour_location(location):
            return False
        return True

    hybrid = text_suggests_hybrid(blob)
    if hybrid:
        return in_nrw or in_benelux

    remote = text_suggests_remote(blob)
    if remote:
        if not text_suggests_de_eu_emea(blob):
            if not _germany_or_neighbour_location(location):
                return False
        return True

    if in_nrw or in_benelux:
        return True

    return False


def job_eligible_nrw_benelux_remote(
    location: str,
    detail_text: str,
    *,
    listing_nrw_scoped: bool = False,
) -> bool:
    blob = f"{location or ''}\n{detail_text or ''}"
    if text_suggests_us_only_remote(blob):
        return False
    if listing_nrw_scoped:
        return True
    return job_text_eligible_nrw_benelux_remote(location, detail_text)


_SYNEOS_PRIORITY_EU = frozenset({"DEU", "NLD", "BEL", "LUX"})
_SYNEOS_OTHER_EU = frozenset(
    {
        "AUT",
        "ESP",
        "PRT",
        "POL",
        "IRL",
        "ITA",
        "CHE",
        "DNK",
        "SWE",
        "FIN",
        "CZE",
        "HUN",
        "ROU",
        "BGR",
        "GRC",
        "SVK",
        "SVN",
        "HRV",
        "LTU",
        "LVA",
        "EST",
        "CYP",
        "MLT",
    }
)
_SYNEOS_NON_EU = frozenset(
    {
        "USA",
        "AUS",
        "BRA",
        "CHN",
        "MEX",
        "TWN",
        "ARG",
        "IND",
        "JPN",
        "KOR",
        "CAN",
        "COL",
        "SGP",
        "ZAF",
        "EGY",
        "SAU",
        "TUR",
        "THA",
        "MYS",
        "IDN",
        "PHL",
        "VNM",
        "NZL",
        "ISR",
    }
)


def syneos_country_from_location(loc: str) -> str | None:
    """Parse Syneos location code prefix (e.g. DEU-Remote → DEU)."""
    code = (loc or "").strip().upper()
    if not code:
        return None
    prefix = code.split("-", 1)[0]
    if len(prefix) == 3 and prefix.isalpha():
        return prefix
    return None


def syneos_title_clinical_eligible(title: str) -> bool:
    t = (title or "").strip()
    if not t:
        return False
    low = t.lower()
    for kw in _cfg().get("syneos_clinical_title_exclude_keywords") or []:
        if kw and str(kw).lower() in low:
            return False
    for kw in _cfg().get("syneos_clinical_title_keywords") or []:
        if not kw:
            continue
        k = str(kw).lower()
        if k == "cra":
            if re.search(r"\bcra\b", low):
                return True
            continue
        if k == "ctm":
            if re.search(r"\bctm\b", low):
                return True
            continue
        if k in low:
            return True
    return False


def _syneos_qualifications_blob(detail_text: str) -> str:
    """Prefer Qualifications block over full page (footer mentions United States)."""
    blob = detail_text or ""
    m = re.search(
        r"Qualifications:?\s*(.*?)(?:Additional Information|Summary|Apply for this job|Share this job|$)",
        blob,
        re.I | re.S,
    )
    if m and m.group(1).strip():
        return m.group(1).strip()
    m2 = re.search(r"Description\s*(.*?)(?:Qualifications|Additional Information|$)", blob, re.I | re.S)
    if m2 and m2.group(1).strip():
        return m2.group(1).strip()
    return blob[:6000]


def job_language_requirements_acceptable(detail_text: str) -> bool:
    """Reject postings that require non-acceptable working languages."""
    blob = _syneos_qualifications_blob(detail_text).lower()
    for phrase in _cfg().get("blocking_job_language_requirements") or []:
        if phrase and str(phrase).lower() in blob:
            return False
    return True


def _syneos_blob_suggests_emea(blob: str) -> bool:
    low = (blob or "").lower()
    return any(
        x in low
        for x in ("emea", "europe", "european union", " eu ", "e.u.")
    )


def syneos_geo_eligible(location: str, title: str, detail_text: str) -> bool:
    blob = f"{title or ''}\n{location or ''}\n{detail_text or ''}"
    prefix = syneos_country_from_location(location)
    if prefix:
        if prefix in _SYNEOS_NON_EU:
            return False
        if prefix in _SYNEOS_PRIORITY_EU or prefix in _SYNEOS_OTHER_EU:
            return True
        return False
    low = blob.lower()
    for hint in _cfg().get("syneos_geo_title_hints") or []:
        if hint and str(hint).lower() in low:
            return True
    if _syneos_blob_suggests_emea(blob):
        return True
    return False


def job_eligible_syneos_clinical_eu(
    location: str,
    detail_text: str,
    *,
    title: str = "",
    listing_nrw_scoped: bool = False,
) -> bool:
    qual = _syneos_qualifications_blob(detail_text)
    blob = f"{location or ''}\n{qual}"
    if text_suggests_us_only_remote(blob):
        return False
    if listing_nrw_scoped:
        return syneos_title_clinical_eligible(title)
    if not syneos_title_clinical_eligible(title):
        return False
    if not syneos_geo_eligible(location, title, detail_text):
        return False
    return job_language_requirements_acceptable(detail_text)


def job_eligible_for_employer(
    company: dict,
    location: str,
    detail_text: str,
    *,
    title: str = "",
) -> bool:
    """Dispatch eligibility by optional YAML key eligibility_profile (default: nrw)."""
    profile = str(company.get("eligibility_profile") or "nrw").strip().lower()
    scoped = bool(company.get("listing_nrw_scoped"))
    if profile == "nrw_benelux_remote":
        return job_eligible_nrw_benelux_remote(
            location, detail_text, listing_nrw_scoped=scoped
        )
    if profile == "syneos_clinical_eu":
        return job_eligible_syneos_clinical_eu(
            location, detail_text, title=title, listing_nrw_scoped=scoped
        )
    return job_eligible_nrw_major(location, detail_text, listing_nrw_scoped=scoped)


def job_eligible_nrw_major(
    location: str,
    detail_text: str,
    *,
    listing_nrw_scoped: bool = False,
) -> bool:
    """
    Use for HTML/detail pipelines. When listing_nrw_scoped=True, every row from that listing counts
    except clear US-only remote roles.
    """
    blob = f"{location or ''}\n{detail_text or ''}"
    if text_suggests_us_only_remote(blob):
        return False
    if listing_nrw_scoped:
        return True
    return job_text_eligible(location, detail_text)


def ucb_detail_eligible(detail_text: str, site_keywords: list[str] | None) -> bool:
    """UCB: match Monheim/Mettmann (user-verified scope) plus general NRW rules for edge cases."""
    blob = detail_text or ""
    low = blob.lower()
    if text_suggests_us_only_remote(blob):
        return False
    keys = site_keywords or ["monheim", "mettmann"]
    for k in keys:
        if k and k.lower() in low:
            return True
    return job_text_eligible("", detail_text)


def _germany_or_neighbour_location(loc: str) -> bool:
    low = (loc or "").lower()
    for hint in ("germany", "deutschland", ", de", " dach", "europe", "eu "):
        if hint in low:
            return True
    return False


def smartrecruiters_posting_eligible(posting: dict) -> bool:
    """One element from SmartRecruiters API `content` array."""
    loc = posting.get("location") or {}
    city = (loc.get("city") or "").strip()
    country = (loc.get("country") or "").strip()
    full_loc = (loc.get("fullLocation") or f"{city}, {country}").strip()
    loc_blob = f"{city}\n{full_loc}"
    remote = bool(loc.get("remote"))
    hybrid = bool(loc.get("hybrid"))

    parts = [posting.get("name") or "", full_loc]
    job_ad = posting.get("jobAd")
    if job_ad:
        if isinstance(job_ad, str):
            parts.append(job_ad)
        else:
            parts.append(json.dumps(job_ad, default=str))
    blob = "\n".join(parts)

    if text_suggests_us_only_remote(blob):
        return False

    if hybrid:
        return location_in_nrw(full_loc) or location_in_nrw(city) or location_in_nrw(blob)

    if remote:
        if country and country.lower() in ("de", "at", "ch", "nl", "be"):
            return True
        if len(blob) > 100 and not text_suggests_de_eu_emea(blob):
            return False
        return True

    if text_suggests_remote(blob) and text_suggests_de_eu_emea(blob):
        return True

    # On-site / in-office (API flags hybrid/remote false) — e.g. Miltenyi Köln / Bergisch Gladbach
    nrw_office = (
        location_in_nrw(full_loc) or location_in_nrw(city) or location_in_nrw(loc_blob)
    )
    if nrw_office:
        country_l = (country or "").strip().lower()
        germany = country_l in (
            "germany",
            "deutschland",
            "de",
            "deu",
        ) or _germany_in_location_string(full_loc)
        # Trust NRW city when country omitted; all NRW keywords are German cities
        if germany or not country_l:
            return True

    return False


def _germany_in_location_string(s: str) -> bool:
    low = (s or "").lower()
    return "germany" in low or "deutschland" in low


def listing_row_worth_detail_fetch(location_snippet: str) -> bool:
    """Cheap prefilter before fetching full SuccessFactors job page."""
    if not location_snippet:
        return True
    low = location_snippet.lower()
    if location_in_nrw(location_snippet):
        return True
    if any(x in low for x in ("remote", "homeworking", "hybrid", "home office")):
        return True
    if text_suggests_onsite(location_snippet):
        return True
    if "germany" in low or "deutschland" in low or ", de" in low:
        return True
    if any(x in low for x in ("netherlands", "nederland", "belgium", "belgium", "luxembourg")):
        return True
    return False


def is_excluded_nrw_major_entry_level_title(
    title: str,
    exclude_keywords: list[str] | None = None,
) -> bool:
    """
    Title excluded from company_nrw_major insert.

    Delegates to shared title_exclusions (requirements.yaml keywords + intern rules).
    """
    excluded, _ = is_excluded_job_title(
        title,
        exclude_keywords,
        include_intern_rules=True,
    )
    return excluded

"""
Company watchlist scraper.

Fetches open job listings from a curated list of NRW pharma/biotech companies
that post on their own career pages rather than on pharmiweb.jobs.

Supports four source_type modes:
  personio  — Personio XML feed  (https://{slug}.jobs.personio.de/xml)
  workable  — Workable JSON API  (https://apply.workable.com/api/v3/accounts/{slug}/jobs)
  recruitee — Recruitee JSON API (https://{slug}.recruitee.com/api/offers)
  html      — Generic HTML fetch + OpenAI Structured Outputs listing extraction,
              followed by individual job-page fetch for full descriptions

Two-step approach for html companies (mirrors pharmiweb):
  Step 1 — Fetch career listing page → LLM extracts {title, url, location}
  Step 2 — For each job with a distinct URL, fetch that page → BeautifulSoup
            text → stored as job_details (no second LLM call)

Public API:
    fetch_jobs(company: dict) -> list[dict]

Each returned job dict contains the keys needed by scraper.db.insert_job():
    job_id, url, title, employer, location, job_details,
    source, contract_type, hours, experience_level,
    salary, start_date, closing_date, discipline
"""

import hashlib
import logging
import time
import xml.etree.ElementTree as ET
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from openai import OpenAI
from pydantic import BaseModel

from scraper import config

logger = logging.getLogger(__name__)

_SESSION = requests.Session()
_SESSION.headers.update(config.HEADERS)

_IPV4_HTTP_APPLIED = False


def _apply_ipv4_only_http(reason: str) -> None:
    """Prefer IPv4 for urllib3/requests (fixes connect timeouts when AAAA is broken)."""
    global _IPV4_HTTP_APPLIED
    if _IPV4_HTTP_APPLIED:
        return
    import socket

    try:
        import urllib3.util.connection as urllib3_conn
    except ImportError:
        return
    urllib3_conn.allowed_gai_family = lambda: socket.AF_INET
    _IPV4_HTTP_APPLIED = True
    logger.info("IPv4-only HTTP resolution enabled (%s)", reason)


if config.COMPANY_SCRAPER_FORCE_IPV4:
    _apply_ipv4_only_http("COMPANY_SCRAPER_FORCE_IPV4 in .env")

# TYPO3 / some WAFs return 403 on deep links until a session cookie is set on the site root.
_HTML_BROWSER_HEADERS = {
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7",
}


def _origin(url: str) -> str:
    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}" if p.netloc else ""


def _get_html_career_response(career_url: str) -> requests.Response:
    """GET career page after warming session on site root (reduces 403 on some hosts)."""
    origin = _origin(career_url)
    extra = {**config.HEADERS, **_HTML_BROWSER_HEADERS}
    if origin:
        try:
            _SESSION.get(
                f"{origin}/",
                timeout=config.REQUEST_TIMEOUT_SECONDS,
                headers={**extra, "Referer": f"{origin}/"},
            )
        except requests.RequestException as exc:
            logger.debug("Session warm-up %s/ failed: %s", origin, exc)
    referer = f"{origin}/" if origin else career_url
    return _SESSION.get(
        career_url,
        timeout=config.REQUEST_TIMEOUT_SECONDS,
        headers={**extra, "Referer": referer},
    )


_OPENAI_CLIENT: OpenAI | None = None


def _openai() -> OpenAI:
    global _OPENAI_CLIENT
    if _OPENAI_CLIENT is None:
        _OPENAI_CLIENT = OpenAI(api_key=config.OPENAI_API_KEY)
    return _OPENAI_CLIENT


# ---------------------------------------------------------------------------
# Stable job_id: MD5(company_name + job_title + location)[:16]
# ---------------------------------------------------------------------------

def _make_job_id(company_name: str, title: str, location: str = "") -> str:
    raw = f"{company_name.lower().strip()}|{title.lower().strip()}|{location.lower().strip()}"
    return hashlib.md5(raw.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Shared detail-page text fetcher (Step 2 for html companies)
# ---------------------------------------------------------------------------

def _fetch_detail_text(url: str, career_url: str) -> str:
    """
    Fetch a single job detail page and return cleaned plain text.

    Returns empty string if:
    - url is the same as the career listing page (no distinct detail page)
    - the fetch fails for any reason
    """
    if not url or url.rstrip("/") == career_url.rstrip("/"):
        return ""
    try:
        extra = {**config.HEADERS, **_HTML_BROWSER_HEADERS}
        ref = (
            career_url
            if urlparse(url).netloc == urlparse(career_url).netloc
            else f"{_origin(url)}/"
        )
        resp = _SESSION.get(
            url,
            timeout=config.REQUEST_TIMEOUT_SECONDS,
            headers={**extra, "Referer": ref or url},
        )
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")
        for tag in soup(["script", "style", "nav", "footer", "head", "header"]):
            tag.decompose()
        text = soup.get_text(separator="\n", strip=True)
        return text[:6000]
    except Exception as exc:
        logger.debug("Could not fetch detail page %s: %s", url, exc)
        return ""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def fetch_jobs(company: dict) -> list[dict]:
    """
    Fetch all open job listings for a company.

    Returns a list of partial job dicts ready for insert_job().
    Returns an empty list if the page is unreachable or no jobs are found.
    """
    source_type = company.get("source_type", "html")
    name = company.get("name", "Unknown")

    if company.get("force_ipv4"):
        _apply_ipv4_only_http(f"force_ipv4: {name}")

    try:
        if source_type == "personio":
            jobs = _fetch_personio(company)
        elif source_type == "workable":
            jobs = _fetch_workable(company)
        elif source_type == "recruitee":
            jobs = _fetch_recruitee(company)
        elif source_type == "skip":
            logger.debug("Skipping %s (source_type=skip)", name)
            return []
        else:
            jobs = _fetch_html_llm(company)

        logger.info("  %s: %d job(s) found", name, len(jobs))
        return jobs

    except Exception as exc:
        logger.warning("  %s: fetch failed — %s", name, exc)
        return []


# ---------------------------------------------------------------------------
# ATS JSON/XML API fetchers
# ---------------------------------------------------------------------------

def _fetch_personio(company: dict) -> list[dict]:
    """
    Uses the Personio XML feed (/xml) which is a stable public endpoint.
    The /api/v1/jobs path returns HTML for most accounts.
    Full job descriptions are embedded in the XML as <jobDescriptions>.
    """
    slug = company["slug"]
    url = f"https://{slug}.jobs.personio.de/xml"
    resp = _SESSION.get(url, timeout=config.REQUEST_TIMEOUT_SECONDS)
    resp.raise_for_status()

    root = ET.fromstring(resp.content)
    jobs = []
    for position in root.findall("position"):
        title = (position.findtext("name") or "").strip()
        if not title:
            continue

        job_id_val = position.findtext("id") or ""
        job_url = f"https://{slug}.jobs.personio.de/job/{job_id_val}" if job_id_val else url
        office = position.findtext("office") or ""
        location = office or company.get("city", "")

        # Extract full job description from embedded XML sections
        sections = []
        for jd in position.findall(".//jobDescription"):
            section_name = (jd.findtext("name") or "").strip()
            raw_html = jd.findtext("value") or ""
            if raw_html:
                section_text = BeautifulSoup(raw_html, "lxml").get_text(
                    separator=" ", strip=True
                )
                if section_name:
                    sections.append(f"{section_name}:\n{section_text}")
                else:
                    sections.append(section_text)
        job_details = "\n\n".join(sections)

        jobs.append(_build_job(company, title, job_url, location, job_details))
    return jobs


def _fetch_workable(company: dict) -> list[dict]:
    slug = company["slug"]
    url = f"https://apply.workable.com/api/v3/accounts/{slug}/jobs"
    resp = _SESSION.post(
        url,
        json={"query": "", "location": [], "department": [], "worktype": [], "remote": []},
        timeout=config.REQUEST_TIMEOUT_SECONDS,
    )
    resp.raise_for_status()
    data = resp.json()

    jobs = []
    for item in data.get("results", []):
        title = item.get("title") or ""
        if not title:
            continue
        location = item.get("location", {})
        loc_str = ", ".join(filter(None, [
            location.get("city"), location.get("country")
        ]))
        shortcode = item.get("shortcode") or item.get("id") or ""
        job_url = f"https://apply.workable.com/{slug}/j/{shortcode}/"

        # Workable listing API doesn't include description; fetch the detail page
        job_details = _fetch_detail_text(job_url, company["career_url"])

        jobs.append(_build_job(company, title, job_url, loc_str, job_details))
        time.sleep(config.REQUEST_DELAY_SECONDS)
    return jobs


def _fetch_recruitee(company: dict) -> list[dict]:
    slug = company["slug"]
    url = f"https://{slug}.recruitee.com/api/offers"
    resp = _SESSION.get(url, timeout=config.REQUEST_TIMEOUT_SECONDS)
    resp.raise_for_status()
    data = resp.json()

    jobs = []
    for item in data.get("offers", []):
        title = item.get("title") or ""
        if not title:
            continue
        location = item.get("location") or item.get("city") or ""
        job_url = (
            item.get("careers_url")
            or f"https://{slug}.recruitee.com/o/{item.get('slug', '')}"
        )

        # Recruitee API includes description HTML in "description" field
        raw_description = item.get("description") or ""
        if raw_description:
            job_details = BeautifulSoup(raw_description, "lxml").get_text(
                separator="\n", strip=True
            )[:6000]
        else:
            job_details = _fetch_detail_text(job_url, company["career_url"])

        jobs.append(_build_job(company, title, job_url, location, job_details))
    return jobs


# ---------------------------------------------------------------------------
# Generic HTML + LLM listing extractor (Step 1) + detail page fetch (Step 2)
# ---------------------------------------------------------------------------

_EXTRACTION_SYSTEM_PROMPT = (
    "You are a precise data extraction assistant. Your only task is to extract "
    "structured job listing data from the text of a company career page. "
    "Extract only what is explicitly present — do not infer, invent, or paraphrase. "
    "If a field is not present on the page, return an empty string for that field."
)


class _JobListing(BaseModel):
    title: str        # Exact job title as written on the page
    url: str          # Direct URL to this job posting if visible; otherwise empty string
    location: str     # City/country/region as written; 'Remote' if stated; empty if not mentioned


class _JobListings(BaseModel):
    jobs: list[_JobListing]


def _fetch_html_llm(company: dict) -> list[dict]:
    """
    Two-step extraction:
      Step 1 — LLM extracts job listing (title, url, location) from career page text.
      Step 2 — For each job with a distinct URL, fetch that page and store the
               full text as job_details. No second LLM call.
    """
    career_url = company["career_url"]

    resp = _get_html_career_response(career_url)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "lxml")
    for tag in soup(["script", "style", "nav", "footer", "head", "header"]):
        tag.decompose()

    page_text = soup.get_text(separator="\n", strip=True)
    page_text = page_text[:8000]

    if len(page_text) < 50:
        logger.debug("  %s: page text too short — likely JS-rendered, skipping",
                     company.get("name"))
        return []

    prompt = (
        "Extract all open job listings from the career page text below.\n\n"
        "Rules:\n"
        "- title: exact job title as written; skip generic entries like "
        "'Spontaneous Application' or 'No positions available'\n"
        "- url: the direct link to this specific job posting if visible on this page; "
        "otherwise empty string (do NOT use the career page URL as a fallback)\n"
        "- location: city/country/region exactly as written; 'Remote' if explicitly stated; "
        "empty string if not mentioned\n"
        "- If the page shows 'no open positions', 'check back later', or similar, "
        "return an empty jobs array\n"
        "- Do not include section headers, navigation items, or company boilerplate "
        "as job titles\n\n"
        f"Career page URL: {career_url}\n\n"
        f"Page content:\n{page_text}"
    )

    response = _openai().beta.chat.completions.parse(
        model=config.OPENAI_MODEL,
        messages=[
            {"role": "system", "content": _EXTRACTION_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        response_format=_JobListings,
    )

    listings = response.choices[0].message.parsed
    if not listings or not listings.jobs:
        return []

    jobs = []
    for item in listings.jobs:
        if not item.title:
            continue

        # Step 2: fetch the individual job detail page for full description
        job_url = item.url or career_url
        job_details = _fetch_detail_text(job_url, career_url)

        jobs.append(_build_job(
            company,
            title=item.title,
            url=job_url,
            location=item.location,
            job_details=job_details,
        ))
        # Brief delay between detail page fetches to be polite
        if item.url and item.url.rstrip("/") != career_url.rstrip("/"):
            time.sleep(config.REQUEST_DELAY_SECONDS)

    return jobs


# ---------------------------------------------------------------------------
# Shared job dict builder
# ---------------------------------------------------------------------------

def _build_job(
    company: dict,
    title: str,
    url: str,
    location: str = "",
    job_details: str = "",
) -> dict:
    company_name = company["name"]
    city = company.get("city", "")
    country = company.get("country", "")

    # Use company city/country as fallback location
    if not location:
        location = ", ".join(filter(None, [city, country]))

    return {
        "job_id":           _make_job_id(company_name, title, location),
        "title":            title,
        "url":              url,
        "employer":         company_name,
        "location":         location,
        "job_details":      job_details,
        "source":           "company_direct",
        # Fields not available from career pages — set to None
        "salary":           None,
        "start_date":       None,
        "closing_date":     None,
        "discipline":       None,
        "hours":            None,
        "contract_type":    None,
        "experience_level": None,
    }

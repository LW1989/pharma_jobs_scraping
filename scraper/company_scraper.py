"""
Company watchlist scraper.

Fetches open job listings from a curated list of NRW pharma/biotech companies
that post on their own career pages rather than on pharmiweb.jobs.

Supports four source_type modes:
  personio  — Personio JSON API  (https://{slug}.jobs.personio.de/api/v1/jobs)
  workable  — Workable JSON API  (https://apply.workable.com/api/v3/accounts/{slug}/jobs)
  recruitee — Recruitee JSON API (https://{slug}.recruitee.com/api/offers)
  html      — Generic HTML fetch + OpenAI LLM extraction

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
from typing import Any

import requests
from bs4 import BeautifulSoup
from openai import OpenAI
from pydantic import BaseModel

from scraper import config

logger = logging.getLogger(__name__)

_SESSION = requests.Session()
_SESSION.headers.update(config.HEADERS)

_OPENAI_CLIENT: OpenAI | None = None


def _openai() -> OpenAI:
    global _OPENAI_CLIENT
    if _OPENAI_CLIENT is None:
        _OPENAI_CLIENT = OpenAI(api_key=config.OPENAI_API_KEY)
    return _OPENAI_CLIENT


# ---------------------------------------------------------------------------
# Stable job_id: MD5(company_name + job_title)[:16]
# ---------------------------------------------------------------------------

def _make_job_id(company_name: str, title: str, location: str = "") -> str:
    raw = f"{company_name.lower().strip()}|{title.lower().strip()}|{location.lower().strip()}"
    return hashlib.md5(raw.encode()).hexdigest()[:16]


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
# ATS JSON API fetchers
# ---------------------------------------------------------------------------

def _fetch_personio(company: dict) -> list[dict]:
    """
    Uses the Personio XML feed (/xml) which is a public, stable endpoint.
    The /api/v1/jobs path returns an HTML page for most Personio accounts.
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
        department = position.findtext("department") or ""
        location = office or department
        jobs.append(_build_job(company, title, job_url, location))
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
        department = item.get("department") or ""
        jobs.append(_build_job(company, title, job_url, loc_str, department))
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
        job_url = item.get("careers_url") or f"https://{slug}.recruitee.com/o/{item.get('slug', '')}"
        department = item.get("department") or ""
        jobs.append(_build_job(company, title, job_url, location, department))
    return jobs


# ---------------------------------------------------------------------------
# Generic HTML + LLM extractor
# ---------------------------------------------------------------------------

class _JobListing(BaseModel):
    title: str
    url: str
    location: str
    description: str


class _JobListings(BaseModel):
    jobs: list[_JobListing]


def _fetch_html_llm(company: dict) -> list[dict]:
    career_url = company["career_url"]

    resp = _SESSION.get(career_url, timeout=config.REQUEST_TIMEOUT_SECONDS)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "lxml")

    # Remove noise: scripts, styles, nav, footer
    for tag in soup(["script", "style", "nav", "footer", "head"]):
        tag.decompose()

    page_text = soup.get_text(separator="\n", strip=True)

    # Truncate to keep token cost low (~8000 chars covers any career listing page)
    page_text = page_text[:8000]

    if len(page_text) < 50:
        logger.debug("  %s: page text too short — likely JS-rendered, skipping",
                     company.get("name"))
        return []

    prompt = (
        "You are given the text content of a company career page.\n"
        "Extract all open job listings. For each job return:\n"
        "  - title: job title (string)\n"
        "  - url: direct link to the job posting if visible, otherwise use the career page URL\n"
        "  - location: city/country or 'Remote' if specified\n"
        "  - description: one-sentence summary of the role\n\n"
        "If no jobs are listed, return an empty jobs array.\n\n"
        f"Career page URL: {career_url}\n\n"
        f"Page content:\n{page_text}"
    )

    response = _openai().beta.chat.completions.parse(
        model=config.OPENAI_MODEL,
        messages=[{"role": "user", "content": prompt}],
        response_format=_JobListings,
    )

    listings = response.choices[0].message.parsed
    if not listings:
        return []

    jobs = []
    for item in listings.jobs:
        if not item.title:
            continue
        jobs.append(_build_job(
            company,
            title=item.title,
            url=item.url or career_url,
            location=item.location,
            job_details=item.description,
        ))
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
        "job_id":          _make_job_id(company_name, title, location),
        "title":           title,
        "url":             url,
        "employer":        company_name,
        "location":        location,
        "job_details":     job_details,
        "source":          "company_direct",
        # Fields not available from career pages — set to None
        "salary":          None,
        "start_date":      None,
        "closing_date":    None,
        "discipline":      None,
        "hours":           None,
        "contract_type":   None,
        "experience_level": None,
    }

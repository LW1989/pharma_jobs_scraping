"""
Company watchlist scraper.

Fetches open job listings from a curated list of NRW pharma/biotech companies
that post on their own career pages rather than on pharmiweb.jobs.

Supports five source_type modes:
  personio  — Personio XML feed  (https://{slug}.jobs.personio.de/xml)
  workable  — Workable JSON API  (https://apply.workable.com/api/v3/accounts/{slug}/jobs)
  recruitee — Recruitee JSON API (https://{slug}.recruitee.com/api/offers)
  join      — join.com company page: schema.org JSON-LD, then __NEXT_DATA__,
              then the html path as a last resort
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
import json
import logging
import re
import time
from datetime import date
import xml.etree.ElementTree as ET
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from openai import OpenAI
from pydantic import BaseModel

from scraper import config, db

logger = logging.getLogger(__name__)


class CompanyFetchError(RuntimeError):
    """
    The career page could not be read.

    Distinct from a page that loaded fine and lists no openings: callers must
    not treat this as "no jobs", or they will delist the employer's entire job
    set and re-insert it on the next successful run.
    """


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

    Returns a list of partial job dicts ready for insert_job(). An empty list
    means the career page loaded and lists no openings.

    Raises on any fetch or parse failure, so that callers can tell an
    unreachable page apart from an empty one — run_company_checker.py delists
    an employer's jobs on an empty result, which would be wrong for a failure.
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
        elif source_type == "join":
            jobs = _fetch_join(company)
        elif source_type == "skip":
            logger.debug("Skipping %s (source_type=skip)", name)
            return []
        else:
            jobs = _fetch_html_llm(company)

        logger.info("  %s: %d job(s) found", name, len(jobs))
        return jobs

    except Exception:
        # Deliberately not swallowed: run_company_checker.py delists an
        # employer's whole job set when a fetch comes back empty.
        raise


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
# join.com company pages
# ---------------------------------------------------------------------------

_JSONLD_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.IGNORECASE | re.DOTALL,
)
_NEXT_DATA_RE = re.compile(
    r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>',
    re.IGNORECASE | re.DOTALL,
)
_JOB_LIST_KEY_RE = re.compile(r"job|position|vacanc|opening", re.IGNORECASE)


def _html_to_text(raw_html: str, limit: int = 6000) -> str:
    if not raw_html:
        return ""
    return BeautifulSoup(raw_html, "lxml").get_text(separator="\n", strip=True)[:limit]


def _jsonld_blocks(html: str) -> list:
    """Yield every parsed application/ld+json payload, flattening @graph."""
    blocks = []
    for raw in _JSONLD_RE.findall(html):
        try:
            data = json.loads(raw.strip())
        except (ValueError, TypeError):
            continue
        stack = [data]
        while stack:
            item = stack.pop()
            if isinstance(item, list):
                stack.extend(item)
            elif isinstance(item, dict):
                if "@graph" in item:
                    stack.append(item["@graph"])
                blocks.append(item)
    return blocks


def _jsonld_location(posting: dict) -> str:
    loc = posting.get("jobLocation")
    if isinstance(loc, list):
        loc = loc[0] if loc else None
    if not isinstance(loc, dict):
        return ""
    address = loc.get("address")
    if isinstance(address, str):
        return address
    if not isinstance(address, dict):
        return ""
    parts = [address.get("addressLocality"), address.get("addressRegion"),
             address.get("addressCountry")]
    parts = [p.get("name") if isinstance(p, dict) else p for p in parts]
    return ", ".join(str(p) for p in parts if p)


def _jobs_from_jsonld(html: str) -> list[dict]:
    """Extract schema.org JobPosting entries: [{title, url, location, details}]."""
    out = []
    for block in _jsonld_blocks(html):
        types = block.get("@type")
        types = types if isinstance(types, list) else [types]
        if "JobPosting" not in types:
            continue
        title = (block.get("title") or "").strip()
        if not title:
            continue
        out.append({
            "title": title,
            "url": (block.get("url") or block.get("@id") or "").strip(),
            "location": _jsonld_location(block),
            "details": _html_to_text(block.get("description") or ""),
        })
    return out


# "similarJobs"/"recommendedJobs" blocks carry stubs of postings listed
# elsewhere on the page. They match the job-ish key test, so they are read, but
# a primary list always wins over them.
_SECONDARY_JOB_KEY_RE = re.compile(
    r"similar|related|recommend|suggest|other|viewed|nearby", re.IGNORECASE
)


def _next_data_record(item: dict) -> dict | None:
    """One candidate job from a __NEXT_DATA__ node, or None if it has no title."""
    title = str(item.get("title") or item.get("name") or "").strip()
    if not title:
        return None
    return {
        "title": title,
        "url": str(item.get("url") or item.get("link")
                   or item.get("permalink") or "").strip(),
        "location": str(item.get("location") or item.get("city") or "").strip(),
        "details": _html_to_text(
            item.get("description") or item.get("descriptionHtml") or ""
        ),
    }


def _dedupe_next_data_records(
    candidates: list[tuple[bool, dict]],
) -> list[dict]:
    """
    Collapse stubs into the postings they shadow, without losing real jobs.

    Two rules, in order:
      1. If a title appears in a primary list, every copy of it from a
         "similar jobs" block is dropped — a stub can carry MORE fields than
         the posting it shadows (a location the real row omits), so richness
         alone picks the wrong one.
      2. Within what remains for that title, postings are distinct when their
         (location, url) differ. Collapsing on title alone would drop a second
         genuine opening — and System A's job_id is md5(name|title|location),
         so those are two separate DB rows.
    """
    primary_titles = {
        rec["title"].lower() for is_primary, rec in candidates if is_primary
    }

    out: list[dict] = []
    seen: set[tuple[str, str, str]] = set()
    for is_primary, rec in candidates:
        key_title = rec["title"].lower()
        if not is_primary and key_title in primary_titles:
            continue
        identity = (key_title, rec["location"].lower(), rec["url"])
        if identity in seen:
            continue
        seen.add(identity)
        out.append(rec)
    return out


def _jobs_from_next_data(html: str) -> list[dict]:
    """
    Best-effort walk of a Next.js __NEXT_DATA__ payload.

    Looks for any list held under a job-ish key whose items are dicts carrying a
    title. Shapes differ between join.com releases, so every field is optional
    except the title.
    """
    match = _NEXT_DATA_RE.search(html)
    if not match:
        return []
    try:
        data = json.loads(match.group(1).strip())
    except (ValueError, TypeError):
        return []

    candidates: list[tuple[bool, dict]] = []
    stack = [(None, data)]
    while stack:
        key, node = stack.pop()
        if isinstance(node, dict):
            stack.extend(node.items())
            continue
        if not isinstance(node, list):
            continue
        key_text = str(key or "")
        job_ish = bool(key and _JOB_LIST_KEY_RE.search(key_text))
        is_primary = job_ish and not _SECONDARY_JOB_KEY_RE.search(key_text)
        for item in node:
            if isinstance(item, list):
                # Lists nested in lists still have to be walked.
                stack.append((key, item))
                continue
            if not isinstance(item, dict):
                continue
            record = _next_data_record(item) if job_ish else None
            if record is None:
                stack.append((key, item))
                continue
            candidates.append((is_primary, record))

    return _dedupe_next_data_records(candidates)


def _fetch_join(company: dict) -> list[dict]:
    """
    join.com renders its listings client-side, so the plain page text the LLM
    path sees is useless. Both structured payloads it does ship are tried first;
    the html path is the fallback if join.com changes shape again.
    """
    career_url = company["career_url"]
    resp = _get_html_career_response(career_url)
    resp.raise_for_status()
    html = resp.text

    listings = _jobs_from_jsonld(html)
    source = "json-ld"
    if not listings:
        listings = _jobs_from_next_data(html)
        source = "__NEXT_DATA__"

    if not listings:
        logger.info(
            "  %s: no structured join.com payload — falling back to html extraction",
            company.get("name"),
        )
        return _fetch_html_llm(company)

    logger.debug("  %s: join.com listings via %s", company.get("name"), source)

    jobs = []
    for item in listings:
        job_url = item["url"] or career_url
        details = item["details"]
        if not details:
            details = _fetch_detail_text(job_url, career_url)
            if job_url != career_url:
                time.sleep(config.REQUEST_DELAY_SECONDS)
        jobs.append(_build_job(
            company,
            title=item["title"],
            url=job_url,
            location=item["location"],
            job_details=details,
        ))
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


_PAGE_CACHE_WARNED = False


def _warn_page_cache_unavailable(name: str, exc: Exception) -> None:
    """
    Warn once per process, not per company.

    The cache is answered from the DB, so a missing company_page_state table
    (scripts/migrate_db.py never run) disables it for every company. At DEBUG
    that is invisible at the runner's INFO level, and the cache silently
    no-ops forever while still paying for a failed connection per company.
    """
    global _PAGE_CACHE_WARNED
    if not _PAGE_CACHE_WARNED:
        _PAGE_CACHE_WARNED = True
        logger.warning(
            "Career-page cache unavailable (%s: %s) — every company will be "
            "re-extracted this run. If company_page_state is missing, run "
            "scripts/migrate_db.py.",
            name, exc,
        )
    else:
        logger.debug("  %s: page-hash cache unavailable (%s)", name, exc)


def _cached_listing_for_unchanged_page(company: dict, page_hash: str) -> list[dict] | None:
    """
    Career pages change monthly, the checker runs daily. When a page's text is
    byte-identical to the previous run, re-run the extraction from the DB
    instead of paying for another LLM call: returning the employer's currently
    active rows marks them re-seen and delists nothing.

    Returns None to mean "no usable cache — do the real extraction". Every DB
    error is a cache miss on purpose, so the smoke scripts still run without a
    database.
    """
    if not config.COMPANY_PAGE_HASH_CACHE:
        return None

    name = company["name"]
    try:
        state = db.get_company_page_state(name)
        if not state or state.get("page_hash") != page_hash:
            return None

        checked_on = state.get("checked_on")
        age_days = (date.today() - checked_on).days if checked_on else None
        # A negative age means a clock skew wrote a future date; re-extract
        # rather than trusting the row until that date passes.
        if (
            age_days is None
            or age_days < 0
            or age_days >= config.COMPANY_PAGE_CACHE_MAX_DAYS
        ):
            logger.info(
                "  %s: cached listing is %s day(s) old — re-extracting",
                name, age_days,
            )
            return None

        rows = db.get_active_jobs_for_employer("company_direct", name)
    except Exception as exc:
        _warn_page_cache_unavailable(name, exc)
        return None

    if not rows:
        # Nothing to re-confirm; extract again so a page that gained its first
        # listing between runs is not missed.
        return None

    logger.info("  %s: career page unchanged — reusing %d cached listing(s)",
                name, len(rows))

    cached = []
    for row in rows:
        job = _build_job(
            company,
            title=row.get("title") or "",
            url=row.get("url") or company["career_url"],
            location=row.get("location") or "",
        )
        # The stored id is authoritative; re-hashing would drift if the row was
        # written under a different title or location.
        job["job_id"] = row["job_id"]
        cached.append(job)
    return cached


def _extract_listings_with_llm(page_text: str, career_url: str) -> list[_JobListing]:
    """Step 1 — ask the model for the {title, url, location} of every opening."""
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
    return list(listings.jobs) if listings and listings.jobs else []


def _fetch_html_llm(company: dict) -> list[dict]:
    """
    Two-step extraction:
      Step 1 — LLM extracts job listing (title, url, location) from career page text.
      Step 2 — For each job with a distinct URL, fetch that page and store the
               full text as job_details. No second LLM call.

    Step 1 is skipped entirely while the page text is unchanged since the last
    run (see _cached_listing_for_unchanged_page).
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
        raise CompanyFetchError(
            f"career page has {len(page_text)} chars of text — likely JS-rendered"
        )

    page_hash = hashlib.md5(page_text.encode("utf-8")).hexdigest()
    cached = _cached_listing_for_unchanged_page(company, page_hash)
    if cached is not None:
        return cached

    listings = _extract_listings_with_llm(page_text, career_url)

    # Only record the hash once extraction succeeded, so a failed run re-tries.
    try:
        db.set_company_page_hash(company["name"], page_hash)
    except Exception as exc:
        _warn_page_cache_unavailable(company["name"], exc)

    jobs = []
    for item in listings:
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

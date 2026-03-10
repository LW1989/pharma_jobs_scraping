import logging
import re
import time
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from scraper import config

logger = logging.getLogger(__name__)

BASE = "https://www.pharmiweb.jobs"
JOB_URL_RE = re.compile(r"/job/(\d+)/")


def _get(url: str) -> requests.Response:
    resp = requests.get(url, headers=config.HEADERS, timeout=config.REQUEST_TIMEOUT_SECONDS)
    resp.raise_for_status()
    return resp


def _soup(resp: requests.Response) -> BeautifulSoup:
    return BeautifulSoup(resp.text, "lxml")


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------

def get_last_page(soup: BeautifulSoup) -> int:
    """Return the total number of search result pages."""
    last_link = soup.find("a", {"aria-label": "Last page"})
    if last_link:
        href = last_link.get("href", "")
        match = re.search(r"[Pp]age=(\d+)", href)
        if match:
            return int(match.group(1))

    # Fallback: look for numbered page links and take the maximum
    page_nums = []
    for a in soup.find_all("a", href=True):
        m = re.search(r"[Pp]age=(\d+)", a["href"])
        if m:
            page_nums.append(int(m.group(1)))
    return max(page_nums) if page_nums else 1


# ---------------------------------------------------------------------------
# Search listing pages
# ---------------------------------------------------------------------------

def _extract_job_links_from_soup(soup: BeautifulSoup) -> dict[str, str]:
    """Return {job_id: absolute_url} from a single search results page."""
    jobs: dict[str, str] = {}
    for a in soup.find_all("a", href=True):
        href = a["href"]
        match = JOB_URL_RE.search(href)
        if match:
            job_id = match.group(1)
            jobs[job_id] = urljoin(BASE, href)
    return jobs


def scrape_all_job_links() -> dict[str, str]:
    """
    Iterate every search results page and return {job_id: url} for all jobs.
    """
    logger.info("Fetching page 1 to determine total pages …")
    page1_url = config.SEARCH_BASE_URL + "&Page=1"
    resp = _get(page1_url)
    soup = _soup(resp)

    last_page = get_last_page(soup)
    logger.info("Total pages: %d", last_page)

    all_jobs: dict[str, str] = {}
    all_jobs.update(_extract_job_links_from_soup(soup))

    for page in range(2, last_page + 1):
        url = config.SEARCH_BASE_URL + f"&Page={page}"
        logger.info("Scraping listing page %d / %d …", page, last_page)
        try:
            resp = _get(url)
            all_jobs.update(_extract_job_links_from_soup(_soup(resp)))
        except Exception as exc:
            logger.warning("Failed to fetch page %d: %s", page, exc)
        time.sleep(config.REQUEST_DELAY_SECONDS)

    logger.info("Found %d unique job links across all pages.", len(all_jobs))
    return all_jobs


# ---------------------------------------------------------------------------
# Individual job detail pages
# ---------------------------------------------------------------------------

def _clean(text: str | None) -> str | None:
    if text is None:
        return None
    return " ".join(text.split()) or None


def _extract_meta_value(soup: BeautifulSoup, label: str) -> str | None:
    """
    The job detail page renders metadata as adjacent dt/dd pairs inside a dl,
    or as labelled spans. Try both patterns.
    """
    # Pattern 1: <dt> label followed by <dd> value
    for dt in soup.find_all("dt"):
        if label.lower() in dt.get_text(strip=True).lower():
            dd = dt.find_next_sibling("dd")
            if dd:
                return _clean(dd.get_text(separator=" "))

    # Pattern 2: labelled <li> items (some boards use this layout)
    for li in soup.find_all("li"):
        text = li.get_text(separator=" ")
        if text.lower().strip().startswith(label.lower()):
            value = text[len(label):].strip().lstrip(":").strip()
            if value:
                return _clean(value)

    return None


def scrape_job_detail(job_id: str, url: str) -> dict:
    """Fetch and parse a single job detail page. Returns a dict ready for DB."""
    logger.debug("Scraping job %s …", job_id)
    try:
        resp = _get(url)
    except Exception as exc:
        logger.warning("Could not fetch job %s (%s): %s", job_id, url, exc)
        return {"job_id": job_id, "url": url}

    soup = _soup(resp)

    # Title
    h1 = soup.find("h1")
    title = _clean(h1.get_text()) if h1 else None

    # Structured metadata labels as they appear on the page
    field_map = {
        "employer":        ["Employer", "Company"],
        "location":        ["Location"],
        "salary":          ["Salary"],
        "start_date":      ["Start date"],
        "closing_date":    ["Closing date"],
        "discipline":      ["Discipline"],
        "hours":           ["Hours"],
        "contract_type":   ["Contract Type", "Contract type"],
        "experience_level": ["Experience Level", "Experience level"],
    }

    data: dict = {"job_id": job_id, "url": url, "title": title}

    for field, labels in field_map.items():
        for label in labels:
            value = _extract_meta_value(soup, label)
            if value:
                data[field] = value
                break

    # Job details body text
    # Look for the article / section that contains the job description
    details_text = None
    for selector in [
        {"name": "div", "attrs": {"class": re.compile(r"job-detail", re.I)}},
        {"name": "section", "attrs": {"class": re.compile(r"detail", re.I)}},
        {"name": "article"},
    ]:
        tag = soup.find(selector["name"], selector.get("attrs"))
        if tag:
            details_text = _clean(tag.get_text(separator="\n"))
            if details_text and len(details_text) > 100:
                break

    # Fallback: largest <div> by text length (heuristic)
    if not details_text or len(details_text) < 100:
        candidates = soup.find_all("div")
        if candidates:
            biggest = max(candidates, key=lambda d: len(d.get_text()))
            details_text = _clean(biggest.get_text(separator="\n"))

    data["job_details"] = details_text
    return data

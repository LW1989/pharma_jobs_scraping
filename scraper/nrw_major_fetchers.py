"""
Fetchers for NRW major employers (remote DE/EU, hybrid NRW, on-site/in-office NRW).
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
from datetime import date
from typing import Any
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup

from scraper import config
from scraper.nrw_eligibility import (
    job_eligible_nrw_major,
    listing_row_worth_detail_fetch,
    smartrecruiters_posting_eligible,
    text_suggests_us_only_remote,
    ucb_detail_eligible,
)

logger = logging.getLogger(__name__)

_SESSION = requests.Session()
_SESSION.headers.update(config.HEADERS)

JOB_PATH_RE = re.compile(r"/job/[^?\s\"']+/\d+/?")


def _job_id_nrw(employer: str, url: str) -> str:
    raw = f"nrw|{employer}|{url}"
    return hashlib.md5(raw.encode()).hexdigest()[:16]


def _build_row(
    employer: str,
    title: str,
    url: str,
    location: str,
    job_details: str,
) -> dict[str, Any]:
    today = date.today()
    return {
        "job_id": _job_id_nrw(employer, url),
        "title": title,
        "url": url,
        "employer": employer,
        "location": location or "",
        "job_details": (job_details or "")[:8000],
        "source": "company_nrw_major",
        "salary": None,
        "start_date": None,
        "closing_date": None,
        "discipline": None,
        "hours": None,
        "contract_type": None,
        "experience_level": None,
        "first_seen": today,
        "last_seen": today,
    }


def _smartrecruiters_public_url(slug: str, posting_id: str, title: str) -> str:
    slug_part = re.sub(r"[^a-zA-Z0-9]+", "-", (title or "job").strip()).strip("-")[:70]
    return f"https://jobs.smartrecruiters.com/{slug}/{posting_id}-{slug_part}"


def fetch_smartrecruiters(company: dict) -> list[dict]:
    slug = company["slug"]
    employer = company["name"]
    jobs: list[dict] = []
    offset = 0
    limit = 100
    while True:
        url = (
            f"https://api.smartrecruiters.com/v1/companies/{slug}/postings"
            f"?limit={limit}&offset={offset}"
        )
        resp = _SESSION.get(url, timeout=config.REQUEST_TIMEOUT_SECONDS)
        resp.raise_for_status()
        data = resp.json()
        content = data.get("content") or []
        if not content:
            break
        for posting in content:
            if not smartrecruiters_posting_eligible(posting):
                continue
            title = posting.get("name") or ""
            pid = posting.get("id") or ""
            pub_url = _smartrecruiters_public_url(slug, pid, title)
            loc = posting.get("location") or {}
            loc_str = loc.get("fullLocation") or ", ".join(
                filter(None, [loc.get("city"), loc.get("country")])
            )
            job_ad = posting.get("jobAd") or {}
            desc = ""
            if isinstance(job_ad, dict):
                desc = str(job_ad.get("jobDescription") or job_ad.get("description") or "")
            if desc:
                desc = BeautifulSoup(desc, "lxml").get_text(separator="\n", strip=True)[:6000]
            jobs.append(_build_row(employer, title, pub_url, loc_str, desc))
        offset += limit
        if offset >= data.get("totalFound", 0):
            break
    return jobs


def _sf_extract_jobs_from_page(html: str, base_url: str) -> list[tuple[str, str, str]]:
    """Return list of (url, title, location_snippet)."""
    soup = BeautifulSoup(html, "lxml")
    out: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "/job/" not in href:
            continue
        m = JOB_PATH_RE.search(href)
        if not m:
            continue
        path = m.group(0).split("?")[0]
        full = urljoin(base_url, path)
        if full in seen:
            continue
        seen.add(full)
        title = a.get_text(separator=" ", strip=True) or ""
        row = a.find_parent("tr") or a.find_parent("li") or a.find_parent("div")
        loc_snip = ""
        if row:
            loc_snip = row.get_text(separator=" ", strip=True)[:500]
        out.append((full, title, loc_snip))
    return out


def fetch_bayer_eightfold(company: dict) -> list[dict]:
    """
    Bayer NRW (and other) slices from Eightfold PCS JSON API — same inventory as
    https://bayer.eightfold.ai/careers?location=NW%2C%20Germany (not jobs.bayer.com SF).
    """
    employer = company["name"]
    api = company.get(
        "eightfold_api_url",
        "https://bayer.eightfold.ai/api/apply/v2/jobs",
    )
    domain = company.get("eightfold_domain", "bayer.com")
    location = company.get("eightfold_location", "NW, Germany")
    jobs: list[dict] = []
    seen_urls: set[str] = set()
    start = 0
    reported_total = 0

    while True:
        try:
            resp = _SESSION.get(
                api,
                params={
                    "domain": domain,
                    "location": location,
                    "query": company.get("eightfold_query", "") or "",
                    "hl": company.get("eightfold_hl", "de"),
                    "start": start,
                },
                timeout=config.REQUEST_TIMEOUT_SECONDS,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            logger.warning("Bayer Eightfold API %s: %s", start, exc)
            break

        positions = data.get("positions") or []
        reported_total = int(data.get("count") or 0)
        if not positions:
            break

        for p in positions:
            url = (p.get("canonicalPositionUrl") or "").strip()
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            title = (p.get("name") or p.get("posting_name") or "").strip()
            loc = (p.get("location") or "").strip()
            raw_desc = p.get("job_description") or ""
            if raw_desc:
                detail_text = BeautifulSoup(raw_desc, "lxml").get_text(
                    separator="\n", strip=True
                )[:8000]
            else:
                detail_text = ""
            blob = f"{title}\n{loc}\n{detail_text}"
            if text_suggests_us_only_remote(blob):
                continue
            jobs.append(_build_row(employer, title, url, loc[:200], detail_text))

        start += len(positions)
        if start >= reported_total or len(positions) < 10:
            break
        time.sleep(config.REQUEST_DELAY_SECONDS)

    logger.info(
        "Bayer Eightfold (%s): %d job(s) fetched (API count=%s)",
        location,
        len(jobs),
        reported_total,
    )
    return jobs


def fetch_successfactors_listing(company: dict) -> list[dict]:
    """
    Paginated listing (SAP SuccessFactors RMK style: /job/slug/id/).
    """
    employer = company["name"]
    base_list = company["listing_base_url"].rstrip("/")
    parsed = urlparse(
        base_list if "://" in base_list else f"https://{base_list}"
    )
    origin = f"{parsed.scheme}://{parsed.netloc}"
    max_pages = int(company.get("max_pages", 20))
    page_param = company.get("page_param", "Page")
    scoped = bool(company.get("listing_nrw_scoped"))
    max_detail_attempts = company.get("max_detail_attempts")
    if max_detail_attempts is not None:
        max_detail_attempts = int(max_detail_attempts)
    jobs: list[dict] = []
    seen_job_urls: set[str] = set()
    detail_attempts = 0

    for page in range(1, max_pages + 1):
        sep = "&" if "?" in base_list else "?"
        list_url = f"{base_list}{sep}{page_param}={page}"
        try:
            resp = _SESSION.get(list_url, timeout=config.REQUEST_TIMEOUT_SECONDS)
            resp.raise_for_status()
        except Exception as exc:
            logger.debug("%s page %d: %s", employer, page, exc)
            break
        entries = _sf_extract_jobs_from_page(resp.text, origin)
        if not entries:
            if page == 1:
                logger.warning("%s: no job links on first listing page", employer)
            break
        new_on_page = 0
        for job_url, title, loc_snip in entries:
            if job_url in seen_job_urls:
                continue
            if not scoped and not listing_row_worth_detail_fetch(loc_snip):
                continue
            if max_detail_attempts is not None and detail_attempts >= max_detail_attempts:
                return jobs
            detail_attempts += 1
            try:
                dresp = _SESSION.get(job_url, timeout=config.REQUEST_TIMEOUT_SECONDS)
                dresp.raise_for_status()
            except Exception as exc:
                logger.debug("detail %s: %s", job_url, exc)
                continue
            dsoup = BeautifulSoup(dresp.text, "lxml")
            for tag in dsoup(["script", "style", "nav", "footer"]):
                tag.decompose()
            detail_text = dsoup.get_text(separator="\n", strip=True)[:12000]
            loc_meta = ""
            h1 = dsoup.find("h1")
            page_title = h1.get_text(strip=True) if h1 else title
            if not job_eligible_nrw_major(
                loc_snip + " " + loc_meta,
                detail_text,
                listing_nrw_scoped=scoped,
            ):
                continue
            seen_job_urls.add(job_url)
            jobs.append(
                _build_row(employer, page_title or title, job_url, loc_snip[:200], detail_text)
            )
            new_on_page += 1
            time.sleep(config.REQUEST_DELAY_SECONDS)
        if new_on_page == 0 and page > 3:
            break
        time.sleep(config.REQUEST_DELAY_SECONDS)
    return jobs


def _workday_extract_job_links(html: str, base_url: str) -> list[str]:
    """Unique job detail URLs from one Workday listing page."""
    links: list[str] = []
    for a in BeautifulSoup(html, "lxml").find_all("a", href=True):
        h = a["href"]
        if "/job/" not in h:
            continue
        if "myworkdayjobs.com" not in h and not h.startswith("/"):
            continue
        full = urljoin(base_url, h.split("?")[0])
        if full not in links:
            links.append(full)
    return links


_WORKDAY_NEXT_SELECTORS = (
    'button[aria-label*="next" i]',
    'button[aria-label*="weiter" i]',
    '[data-uxi-widget-type="paginationNextButton"]',
)


def _workday_next_clickable(page) -> bool:
    for sel in _WORKDAY_NEXT_SELECTORS:
        try:
            loc = page.locator(sel).first
            if loc.count() == 0:
                continue
            if loc.is_disabled():
                return False
            if (loc.get_attribute("aria-disabled") or "").lower() == "true":
                return False
            return True
        except Exception:
            continue
    return False


def _workday_click_next(page) -> bool:
    if not _workday_next_clickable(page):
        return False
    for sel in _WORKDAY_NEXT_SELECTORS:
        try:
            loc = page.locator(sel).first
            if loc.count() == 0:
                continue
            loc.click(timeout=5000)
            page.wait_for_timeout(2500)
            return True
        except Exception:
            continue
    return False


def _workday_collect_listing_links(
    page,
    start_url: str,
    *,
    max_list: int,
    max_pages: int,
    employer: str,
) -> list[str]:
    """Paginate Workday listing (20 jobs/page) until no next or caps hit."""
    seen: set[str] = set()
    ordered: list[str] = []
    for pg in range(1, max_pages + 1):
        batch = _workday_extract_job_links(page.content(), start_url)
        new = [u for u in batch if u not in seen]
        for u in new:
            seen.add(u)
            ordered.append(u)
        logger.info(
            "%s workday page %d: %d link(s) on page, +%d new (total %d)",
            employer,
            pg,
            len(batch),
            len(new),
            len(ordered),
        )
        if len(ordered) >= max_list:
            return ordered[:max_list]
        if pg >= max_pages:
            break
        if not new and pg > 1:
            break
        if not _workday_click_next(page):
            break
    return ordered[:max_list]


def fetch_workday_playwright(company: dict) -> list[dict]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.warning("playwright not installed — skip %s", company.get("name"))
        return []

    employer = company["name"]
    start_url = company["workday_url"]
    max_list = int(company.get("workday_max_list_jobs", 60))
    max_pages = int(company.get("workday_max_listing_pages", 10))
    scoped = bool(company.get("listing_nrw_scoped"))
    jobs: list[dict] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page(user_agent=config.HEADERS["User-Agent"])
            page.set_default_timeout(45000)
            page.goto(start_url, wait_until="domcontentloaded")
            page.wait_for_timeout(4000)
            try:
                for btn in page.locator("button:has-text('Accept')").all()[:1]:
                    btn.click(timeout=3000)
                    page.wait_for_timeout(1000)
            except Exception:
                pass
            links = _workday_collect_listing_links(
                page,
                start_url,
                max_list=max_list,
                max_pages=max_pages,
                employer=employer,
            )
            logger.info(
                "%s workday: %d job link(s) across listing (max_list=%d, max_pages=%d)",
                employer,
                len(links),
                max_list,
                max_pages,
            )
            for job_url in links:
                try:
                    page.goto(job_url, wait_until="domcontentloaded")
                    page.wait_for_timeout(2000)
                    body = page.inner_text("body")[:12000]
                except Exception as exc:
                    logger.debug("workday job %s: %s", job_url, exc)
                    continue
                title = ""
                try:
                    title = page.title() or ""
                except Exception:
                    pass
                if not job_eligible_nrw_major(
                    "", body, listing_nrw_scoped=scoped
                ):
                    continue
                jobs.append(_build_row(employer, title, job_url, "", body))
            logger.info("%s workday: %d eligible job(s) after filter", employer, len(jobs))
            browser.close()
        except Exception as exc:
            logger.warning("Workday %s: %s", employer, exc)
            try:
                browser.close()
            except Exception:
                pass
    return jobs


def fetch_ucb_playwright(company: dict) -> list[dict]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return []

    employer = company["name"]
    url = company["careers_url"]
    max_jobs = int(company.get("max_jobs", 80))
    kw = company.get("ucb_site_keywords") or [
        "Monheim am Rhein",
        "Monheim",
        "Mettmann",
    ]
    jobs: list[dict] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page(user_agent=config.HEADERS["User-Agent"])
            page.goto(url, wait_until="networkidle", timeout=90000)
            page.wait_for_timeout(5000)
            for _ in range(20):
                try:
                    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                except Exception:
                    break
                page.wait_for_timeout(600)
            html = page.content()
            soup = BeautifulSoup(html, "lxml")
            links: list[str] = []
            for a in soup.find_all("a", href=True):
                h = a["href"]
                if "/job/" in h or "/en/job/" in h:
                    full = urljoin(url, h.split("?")[0])
                    if "ucb.com" in full and full not in links:
                        links.append(full)
            for job_url in links[:max_jobs]:
                try:
                    page.goto(job_url, wait_until="domcontentloaded", timeout=30000)
                    page.wait_for_timeout(2000)
                    text = page.inner_text("body")[:12000]
                except Exception:
                    continue
                if not ucb_detail_eligible(text, kw):
                    continue
                jobs.append(_build_row(employer, page.title() or "", job_url, "", text))
            browser.close()
        except Exception as exc:
            logger.warning("UCB fetch: %s", exc)
            try:
                browser.close()
            except Exception:
                pass
    return jobs


def _jnj_listing_page_url(listing_url: str, page: int) -> str:
    """Add/update ?page=N on careers.jnj.com jobs search URL (preserve #fragment)."""
    frag = ""
    base = listing_url
    if "#" in listing_url:
        base, frag = listing_url.split("#", 1)
        frag = "#" + frag
    parsed = urlparse(base)
    q = dict(parse_qsl(parsed.query, keep_blank_values=True))
    q["page"] = str(page)
    new_q = urlencode(q)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", new_q, "")) + frag


def _jnj_collect_job_links(html: str) -> list[str]:
    """Detail URLs: careers.jnj.com/{locale}/jobs/r-{id}/{slug}/"""
    seen: set[str] = set()
    out: list[str] = []
    for a in BeautifulSoup(html, "lxml").find_all("a", href=True):
        h = (a["href"] or "").strip()
        if not h or "saved-jobs" in h:
            continue
        full = urljoin("https://www.careers.jnj.com/", h.split("#")[0])
        if "careers.jnj.com" not in full.lower():
            continue
        if not re.search(r"/jobs/r-\d+/", full, re.I):
            continue
        full = full.split("?")[0].rstrip("/")
        if full not in seen:
            seen.add(full)
            out.append(full)
    return out


def _jnj_dismiss_cookies(page) -> None:
    for sel in (
        "#onetrust-reject-all-handler",
        "button:has-text('Reject All')",
        "button#onetrust-accept-btn-handler",
        "button:has-text('Accept All Cookies')",
    ):
        try:
            page.locator(sel).first.click(timeout=4000)
            page.wait_for_timeout(1200)
            return
        except Exception:
            continue


def probe_jnj_careers_listing_link_count(listing_url: str) -> tuple[str, int]:
    """
    Smoke test: number of job detail links on page 1 (no detail fetches).
    Returns (status, count); count -1 on failure.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return "playwright not installed", -1
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(user_agent=config.HEADERS["User-Agent"])
            page.set_default_timeout(90000)
            url = _jnj_listing_page_url(listing_url, 1)
            page.goto(url, wait_until="domcontentloaded")
            page.wait_for_timeout(10000)
            _jnj_dismiss_cookies(page)
            page.wait_for_timeout(2500)
            n = len(_jnj_collect_job_links(page.content()))
            browser.close()
        return "ok", n
    except Exception as exc:
        return str(exc)[:120], -1


def fetch_jnj_careers_playwright(company: dict) -> list[dict]:
    """
    Johnson & Johnson listings on careers.jnj.com (filtered search URL, e.g. Germany + NRW).
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.warning("playwright not installed — skip J&J careers")
        return []

    employer = company["name"]
    listing_url = company.get("listing_url") or company.get("jnj_listing_url")
    if not listing_url:
        logger.warning("J&J careers: missing listing_url")
        return []

    max_pages = int(company.get("jnj_max_listing_pages", 30))
    max_jobs = int(company.get("max_jobs", 100))
    job_urls: list[str] = []
    seen: set[str] = set()
    jobs: list[dict] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page(user_agent=config.HEADERS["User-Agent"])
            page.set_default_timeout(90000)

            for pg in range(1, max_pages + 1):
                url = _jnj_listing_page_url(listing_url, pg)
                # careers.jnj.com: networkidle often never settles (analytics, long-poll)
                page.goto(url, wait_until="domcontentloaded")
                page.wait_for_timeout(9000)
                _jnj_dismiss_cookies(page)
                page.wait_for_timeout(3000)
                links = _jnj_collect_job_links(page.content())
                new = [u for u in links if u not in seen]
                if not new:
                    break
                for u in new:
                    seen.add(u)
                    job_urls.append(u)

            for job_url in job_urls[:max_jobs]:
                try:
                    page.goto(job_url, wait_until="domcontentloaded", timeout=35000)
                    page.wait_for_timeout(1800)
                    _jnj_dismiss_cookies(page)
                    body = page.inner_text("body")[:12000]
                except Exception as exc:
                    logger.debug("J&J job %s: %s", job_url, exc)
                    continue
                if not job_eligible_nrw_major(
                    "",
                    body,
                    listing_nrw_scoped=bool(
                        company.get("listing_nrw_scoped", True)
                    ),
                ):
                    continue
                try:
                    title = page.title() or ""
                except Exception:
                    title = ""
                jobs.append(_build_row(employer, title, job_url, "", body))
            browser.close()
        except Exception as exc:
            logger.warning("J&J careers: %s", exc)
            try:
                browser.close()
            except Exception:
                pass
    return jobs


def _henkel_job_url(href: str) -> bool:
    if not href or "javascript:" in href.lower():
        return False
    if href.startswith("#"):
        low = href.lower()
        return any(
            x in low for x in ("job", "req", "stelle", "posting", "career", "apply")
        ) and len(href) > 8
    low = href.lower()
    if "jobs-und-bewerbung" in low and "?" not in low and "jobreq" not in low:
        return False
    # SAP / SuccessFactors job deep links (~121+ postings on Henkel DE portal)
    if any(
        x in low
        for x in (
            "career_job_req_id",
            "jobreqid",
            "job_req_id",
            "requisitionid",
            "rcm/",
            "jobdetails",
            "jobdetail",
        )
    ):
        return True
    if "successfactors" in low and any(
        x in low for x in ("job", "career", "requisition", "posting", "apply", "rcm")
    ):
        return True
    if "jobs.sap.com" in low:
        return True
    if "sapsf.com" in low or "sapsf.eu" in low:
        if any(x in low for x in ("career", "job", "req", "posting", "apply")):
            return True
    if "cloud.sap" in low and "job" in low:
        return True
    if any(x in low for x in ("/job/", "jobdetail", "requisition", "posting")):
        return True
    if low.startswith("/") and any(
        x in low for x in ("stelle", "job", "bewerbung", "requisition")
    ):
        return len(href) > 15
    return False


HENKEL_NRW_HASH = (
    "selectFilterByParameter=Locations_279384=Europe"
    "&Europe_877522=Germany"
    "&Germany_279422=North%20Rhine%20Westphalia"
)


def _henkel_listing_url(
    base: str, start_index: int, load_count: int, *, nrw: bool
) -> str:
    stem = base.split("#")[0].rstrip("/")
    if nrw:
        frag = f"{HENKEL_NRW_HASH}&startIndex={start_index}&loadCount={load_count}&"
    else:
        frag = f"startIndex={start_index}&loadCount={load_count}&"
    return f"{stem}#{frag}"


def _henkel_normalize_href(href: str, base_url: str) -> str | None:
    if not _henkel_job_url(href):
        return None
    if href.startswith("#"):
        return (base_url.split("#")[0].rstrip("/") + href).split("?")[0]
    if href.startswith("/"):
        return urljoin("https://www.henkel.de", href.split("#")[0])
    if href.startswith("http"):
        return href.split("#")[0]
    return urljoin(base_url.split("#")[0], href.split("#")[0])


def _henkel_extract_links_from_frames(page, base_url: str) -> list[str]:
    seen: set[str] = set()
    found: list[str] = []
    for frame in page.frames:
        try:
            hrefs = frame.evaluate(
                """() => [...document.querySelectorAll('a[href]')]
                    .map(a => a.getAttribute('href'))
                    .filter(Boolean)"""
            )
        except Exception:
            continue
        for h in hrefs:
            full = _henkel_normalize_href(h, base_url)
            if full and full.startswith("http") and full not in seen:
                seen.add(full)
                found.append(full)
    return found


def _henkel_dismiss_cookies(page) -> None:
    for sel in (
        "button:has-text('Alle akzeptieren')",
        "button:has-text('Accept all')",
        "button:has-text('Zustimmen')",
    ):
        try:
            page.locator(sel).first.click(timeout=3000)
            page.wait_for_timeout(1500)
            return
        except Exception:
            continue


def _henkel_click_load_more_in_frames(page) -> bool:
    clicked = False
    for frame in page.frames:
        for pattern in (
            r"Mehr Jobs laden",
            r"Mehr laden",
            r"Load more",
        ):
            try:
                btn = frame.get_by_role("button", name=re.compile(pattern, re.I))
                if btn.count() and btn.first.is_enabled():
                    btn.first.click(timeout=3000)
                    page.wait_for_timeout(2000)
                    clicked = True
            except Exception:
                continue
    if not clicked:
        for loc in (
            page.get_by_role("button", name=re.compile(r"Mehr Jobs laden", re.I)),
            page.locator("text=Mehr Jobs laden"),
        ):
            try:
                loc.first.click(timeout=3000)
                page.wait_for_timeout(2000)
                return True
            except Exception:
                continue
    return clicked


def _henkel_collect_listing_links(page, company: dict) -> list[str]:
    """
    Henkel SAP portal: NRW filter uses hash startIndex/loadCount pagination (~91 jobs).
    Unfiltered portal uses 'Mehr Jobs laden' clicks (~121 jobs).
    """
    base = company.get(
        "careers_url",
        "https://www.henkel.de/karriere/jobs-und-bewerbung",
    )
    employer = company.get("name", "Henkel")
    use_nrw = bool(company.get("henkel_nrw_listing", True))
    load_count = int(company.get("henkel_load_count", 10))
    max_rounds = int(company.get("henkel_max_load_rounds", 15))

    if use_nrw:
        seen: set[str] = set()
        ordered: list[str] = []
        start = 0
        for _ in range(max_rounds):
            listing_url = _henkel_listing_url(base, start, load_count, nrw=True)
            page.goto(listing_url, wait_until="domcontentloaded")
            page.wait_for_timeout(3500)
            _henkel_click_load_more_in_frames(page)
            batch = _henkel_extract_links_from_frames(page, base)
            new = [u for u in batch if u not in seen]
            for u in new:
                seen.add(u)
                ordered.append(u)
            logger.info(
                "%s startIndex=%d: %d on page, +%d new (total %d)",
                employer,
                start,
                len(batch),
                len(new),
                len(ordered),
            )
            if not new or len(new) < load_count:
                break
            start += load_count
        return ordered

    # Legacy: unfiltered portal + Mehr Jobs laden
    page.goto(base.split("#")[0], wait_until="domcontentloaded")
    page.wait_for_timeout(4000)
    for _ in range(max_rounds):
        try:
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        except Exception:
            pass
        page.wait_for_timeout(600)
        if not _henkel_click_load_more_in_frames(page):
            break
    return _henkel_extract_links_from_frames(page, base)


def probe_henkel_portal_link_count(company: dict) -> tuple[str, int]:
    """Smoke test: job URLs visible after NRW hash pagination or load-more."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return "playwright not installed", -1
    careers_url = company.get(
        "careers_url",
        "https://www.henkel.de/karriere/jobs-und-bewerbung",
    )
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(user_agent=config.HEADERS["User-Agent"])
            page.set_default_timeout(90000)
            page.goto(careers_url.split("#")[0], wait_until="domcontentloaded")
            page.wait_for_timeout(4000)
            _henkel_dismiss_cookies(page)
            n = len(_henkel_collect_listing_links(page, company))
            browser.close()
        return "ok", n
    except Exception as exc:
        return str(exc)[:120], -1


def fetch_henkel_playwright(company: dict) -> list[dict]:
    """
    Henkel DE job portal (JS + often embedded SAP iframe).
    https://www.henkel.de/karriere/jobs-und-bewerbung
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.warning("playwright not installed — skip Henkel")
        return []

    employer = company["name"]
    url = company.get(
        "careers_url",
        "https://www.henkel.de/karriere/jobs-und-bewerbung",
    )
    # Portal lists ~121 jobs; fetch all links then filter by NRW/remote eligibility
    max_jobs = int(company.get("max_jobs", 200))
    scoped = bool(company.get("listing_nrw_scoped", False))
    jobs: list[dict] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page(user_agent=config.HEADERS["User-Agent"])
            page.set_default_timeout(90000)
            page.goto(url.split("#")[0], wait_until="domcontentloaded")
            page.wait_for_timeout(4000)
            _henkel_dismiss_cookies(page)
            links = _henkel_collect_listing_links(page, company)
            logger.info(
                "Henkel portal: %d job URL(s) found (NRW listing ~91; global ~121)",
                len(links),
            )
            eligible = 0
            for job_url in links[:max_jobs]:
                try:
                    page.goto(job_url, wait_until="domcontentloaded", timeout=45000)
                    page.wait_for_timeout(2000)
                    body = page.inner_text("body")[:12000]
                except Exception as exc:
                    logger.debug("Henkel job %s: %s", job_url, exc)
                    continue
                if not job_eligible_nrw_major(
                    "", body, listing_nrw_scoped=scoped
                ):
                    continue
                eligible += 1
                try:
                    title = page.title() or ""
                except Exception:
                    title = ""
                jobs.append(_build_row(employer, title, job_url, "", body))
            logger.info(
                "Henkel portal: %d eligible job(s) after NRW/remote filter",
                eligible,
            )
            browser.close()
        except Exception as exc:
            logger.warning("Henkel portal: %s", exc)
            try:
                browser.close()
            except Exception:
                pass
    return jobs


def fetch_jobs_for_employer(company: dict) -> list[dict]:
    st = company.get("source_type", "")
    if st == "smartrecruiters":
        return fetch_smartrecruiters(company)
    if st == "bayer_eightfold":
        return fetch_bayer_eightfold(company)
    if st == "successfactors":
        return fetch_successfactors_listing(company)
    if st == "workday":
        return fetch_workday_playwright(company)
    if st == "ucb":
        return fetch_ucb_playwright(company)
    if st == "henkel_portal":
        return fetch_henkel_playwright(company)
    if st == "jnj_careers":
        return fetch_jnj_careers_playwright(company)
    logger.warning("Unknown NRW employer source_type: %s", st)
    return []

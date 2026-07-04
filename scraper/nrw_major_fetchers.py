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
    location_in_nrw,
    smartrecruiters_posting_eligible,
    text_suggests_remote,
    text_suggests_us_only_remote,
    ucb_detail_eligible,
)

logger = logging.getLogger(__name__)

_SESSION = requests.Session()
_SESSION.headers.update(config.HEADERS)

JOB_PATH_RE = re.compile(r"/job/[^?\s\"']+/\d+/?")
HENKEL_JOB_PATH_RE = re.compile(r"/karriere/jobs-und-bewerbung/\d+-\d+/?", re.I)
HENKEL_BABIEL_API = (
    "https://www.henkel.de/ajax/collection/de/1341296-1341296/queryresults/asJson"
)
LANXESS_JOB_RE = re.compile(
    r"https://career\.lanxess\.com/us/en/job/\d+/[^\"'\s<>]+", re.I
)
UCB_JOB_RE = re.compile(r"https://careers\.ucb\.com/global/en/job/\d+/[^\"'\s<>]+", re.I)


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


def _workday_path_matches(url: str, patterns: list[str] | None) -> bool:
    if not patterns:
        return True
    return any(p in url for p in patterns)


def _workday_dismiss_cookies(page) -> None:
    try:
        for btn in page.locator("button:has-text('Accept')").all()[:1]:
            btn.click(timeout=3000)
            page.wait_for_timeout(1000)
            return
    except Exception:
        pass
    for label in ("Cookies akzeptieren", "Accept All", "Accept"):
        try:
            page.get_by_role("button", name=label).first.click(timeout=3000)
            page.wait_for_timeout(1000)
            return
        except Exception:
            continue


def _workday_gather_listing_links(page, company: dict, employer: str) -> list[str]:
    """Collect job URLs from workday_url (+ optional extra URLs), merge, path-filter."""
    max_list = int(company.get("workday_max_list_jobs", 60))
    max_pages = int(company.get("workday_max_listing_pages", 10))
    path_include: list[str] | None = company.get("workday_path_include") or None
    path_only_extra = bool(company.get("workday_extra_paths_only", False))

    listing_urls: list[str] = [company["workday_url"]]
    for extra in company.get("workday_extra_urls") or []:
        if extra and extra not in listing_urls:
            listing_urls.append(extra)

    seen: set[str] = set()
    ordered: list[str] = []
    for list_url in listing_urls:
        page.goto(list_url, wait_until="domcontentloaded")
        page.wait_for_timeout(4000)
        _workday_dismiss_cookies(page)
        apply_path_filter = path_include
        if path_only_extra and list_url == company["workday_url"]:
            apply_path_filter = None
        batch = _workday_collect_listing_links(
            page,
            list_url,
            max_list=max_list,
            max_pages=max_pages,
            employer=employer,
        )
        for u in batch:
            if apply_path_filter and not _workday_path_matches(u, apply_path_filter):
                continue
            if u not in seen:
                seen.add(u)
                ordered.append(u)
        logger.info(
            "%s workday listing %s: batch=%d running_total=%d",
            employer,
            list_url[:70],
            len(batch),
            len(ordered),
        )
    return ordered


def fetch_workday_playwright(company: dict) -> list[dict]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.warning("playwright not installed — skip %s", company.get("name"))
        return []

    employer = company["name"]
    scoped = bool(company.get("listing_nrw_scoped"))
    jobs: list[dict] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page(user_agent=config.HEADERS["User-Agent"])
            page.set_default_timeout(45000)
            links = _workday_gather_listing_links(page, company, employer)
            logger.info(
                "%s workday: %d job link(s) after merge/path filter",
                employer,
                len(links),
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


def _ucb_apply_listing_filters(page, company: dict) -> None:
    country = company.get("ucb_country_filter", "Germany")
    city = company.get("ucb_city_filter", "Monheim")
    if not country and not city:
        return
    try:
        if country:
            page.locator("text=Country").first.click(timeout=8000)
            page.wait_for_timeout(800)
            page.locator(f"text={country}").first.click(timeout=5000)
            page.wait_for_timeout(2500)
        if city:
            page.locator("text=City").first.click(timeout=8000)
            page.wait_for_timeout(800)
            page.locator(f"text={city}").first.click(timeout=5000)
            page.wait_for_timeout(2500)
    except Exception as exc:
        logger.warning("UCB facet filters: %s", exc)


def _ucb_collect_job_links(page, max_pages: int = 10) -> list[str]:
    """Paginate UCB search-results and collect job detail URLs."""
    seen: set[str] = set()
    ordered: list[str] = []
    for pg in range(1, max_pages + 1):
        batch = UCB_JOB_RE.findall(page.content())
        new = [u for u in batch if u not in seen]
        for u in new:
            seen.add(u)
            ordered.append(u.split("?")[0])
        showing = re.search(
            r"Showing\s+(\d+)\s*-\s*(\d+)\s+of\s+(\d+)",
            page.inner_text("body"),
            re.I,
        )
        if showing and int(showing.group(2)) >= int(showing.group(3)):
            break
        if not new and pg > 1:
            break
        try:
            nxt = page.locator('a[aria-label*="Next" i], button[aria-label*="Next" i]').first
            if nxt.is_disabled():
                break
            nxt.click(timeout=5000)
            page.wait_for_timeout(2500)
        except Exception:
            break
    return ordered


def fetch_ucb_playwright(company: dict) -> list[dict]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return []

    employer = company["name"]
    url = company["careers_url"]
    max_jobs = int(company.get("max_jobs", 80))
    max_pages = int(company.get("ucb_max_listing_pages", 10))
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
            page.goto(url, wait_until="domcontentloaded", timeout=90000)
            page.wait_for_timeout(4000)
            _ucb_apply_listing_filters(page, company)
            links = _ucb_collect_job_links(page, max_pages=max_pages)
            logger.info("UCB: %d job link(s) after facet filters", len(links))
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
            logger.info("UCB: %d eligible job(s)", len(jobs))
            browser.close()
        except Exception as exc:
            logger.warning("UCB fetch: %s", exc)
            try:
                browser.close()
            except Exception:
                pass
    return jobs


def _lanxess_extract_job_links(html: str) -> list[str]:
    return list(dict.fromkeys(LANXESS_JOB_RE.findall(html)))


def _lanxess_click_next(page) -> bool:
    for pattern in (r"next", r"weiter", r"Next"):
        try:
            btn = page.get_by_role("button", name=re.compile(pattern, re.I))
            if btn.count() and btn.first.is_enabled():
                btn.first.click(timeout=4000)
                page.wait_for_timeout(2500)
                return True
        except Exception:
            continue
    try:
        page.locator('a[aria-label*="Next" i]').first.click(timeout=4000)
        page.wait_for_timeout(2500)
        return True
    except Exception:
        return False


def _lanxess_collect_listing_links(page, company: dict) -> list[str]:
    listing_url = company.get(
        "listing_url", "https://career.lanxess.com/us/en/search-results"
    )
    location_search = (company.get("location_search") or "Leverkusen").strip()
    max_pages = int(company.get("lanxess_max_listing_pages", 5))

    page.goto(listing_url, wait_until="domcontentloaded", timeout=90000)
    page.wait_for_timeout(4000)
    if location_search:
        for sel in (
            'input[placeholder*="Location" i]',
            'input[aria-label*="Location" i]',
            'input[name*="location" i]',
        ):
            try:
                loc = page.locator(sel).first
                loc.fill(location_search, timeout=5000)
                page.keyboard.press("Enter")
                page.wait_for_timeout(4000)
                break
            except Exception:
                continue
        try:
            page.get_by_role("button", name=re.compile(r"^Search$", re.I)).first.click(
                timeout=3000
            )
            page.wait_for_timeout(3000)
        except Exception:
            pass

    seen: set[str] = set()
    ordered: list[str] = []
    for pg in range(1, max_pages + 1):
        batch = _lanxess_extract_job_links(page.content())
        new = [u for u in batch if u not in seen]
        for u in new:
            seen.add(u)
            ordered.append(u)
        logger.info(
            "LANXESS page %d: %d link(s), +%d new (total %d)",
            pg,
            len(batch),
            len(new),
            len(ordered),
        )
        showing = re.search(
            r"Showing\s+(\d+)\s*-\s*(\d+)\s+of\s+(\d+)",
            page.inner_text("body"),
            re.I,
        )
        if showing and int(showing.group(2)) >= int(showing.group(3)):
            break
        if not new and pg > 1:
            break
        if not _lanxess_click_next(page):
            break
    return ordered


def probe_lanxess_portal_link_count(company: dict) -> tuple[str, int]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return "playwright not installed", -1
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(user_agent=config.HEADERS["User-Agent"])
            links = _lanxess_collect_listing_links(page, company)
            browser.close()
        return "ok", len(links)
    except Exception as exc:
        return str(exc)[:120], -1


def fetch_lanxess_portal(company: dict) -> list[dict]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.warning("playwright not installed — skip LANXESS")
        return []

    employer = company["name"]
    max_jobs = int(company.get("max_jobs", 30))
    scoped = bool(company.get("listing_nrw_scoped", True))
    jobs: list[dict] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page(user_agent=config.HEADERS["User-Agent"])
            page.set_default_timeout(60000)
            links = _lanxess_collect_listing_links(page, company)
            logger.info("LANXESS portal: %d job link(s) on listing", len(links))
            for job_url in links[:max_jobs]:
                try:
                    r = _SESSION.get(job_url, timeout=config.REQUEST_TIMEOUT_SECONDS)
                    r.raise_for_status()
                    soup = BeautifulSoup(r.text, "lxml")
                    for tag in soup(["script", "style", "nav", "footer"]):
                        tag.decompose()
                    body = soup.get_text(separator="\n", strip=True)[:12000]
                    h1 = soup.find("h1")
                    title = h1.get_text(strip=True) if h1 else ""
                    if not title:
                        t = soup.find("title")
                        title = t.get_text(strip=True) if t else ""
                except Exception as exc:
                    logger.debug("LANXESS job %s: %s", job_url, exc)
                    continue
                if not job_eligible_nrw_major("", body, listing_nrw_scoped=scoped):
                    continue
                jobs.append(_build_row(employer, title, job_url, "", body))
                time.sleep(config.REQUEST_DELAY_SECONDS)
            logger.info("LANXESS portal: %d eligible job(s)", len(jobs))
            browser.close()
        except Exception as exc:
            logger.warning("LANXESS portal: %s", exc)
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
    if HENKEL_JOB_PATH_RE.search(href):
        return True
    # Legacy SAP / SuccessFactors deep links (older Henkel integrations)
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
        return len(href) > 15 and "jobs-und-bewerbung" not in low
    return False


def _henkel_row_to_url(link: str) -> str:
    if link.startswith("http"):
        return link.split("#")[0]
    return urljoin("https://www.henkel.de", link.split("#")[0])


def _henkel_api_listings(company: dict) -> list[dict]:
    """Paginate Henkel Babiel CMS JSON listing (10 jobs/page, startIndex offset)."""
    api = company.get("henkel_ajax_url", HENKEL_BABIEL_API)
    max_total = int(company.get("max_jobs", 200))
    params_base: dict[str, str] = dict(company.get("henkel_api_params") or {})
    offset = 0
    all_rows: list[dict] = []
    seen_ids: set[str] = set()

    while len(all_rows) < max_total:
        params = dict(params_base)
        if offset:
            params["startIndex"] = str(offset)
        r = _SESSION.get(api, params=params, timeout=45)
        r.raise_for_status()
        data = r.json()
        batch = data.get("results") or []
        if not batch:
            break
        for row in batch:
            jid = str(row.get("id") or "")
            if jid and jid in seen_ids:
                continue
            if jid:
                seen_ids.add(jid)
            all_rows.append(row)
        remaining = int(data.get("resultsRemaining") or 0)
        if remaining <= 0:
            break
        offset += len(batch)
    return all_rows


def probe_henkel_portal_link_count(company: dict) -> tuple[str, int]:
    """Smoke test: job URLs from Babiel JSON listing API."""
    try:
        probe_cfg = {**company, "max_jobs": int(company.get("henkel_api_max_probe", 500))}
        rows = _henkel_api_listings(probe_cfg)
        urls = [_henkel_row_to_url(r["link"]) for r in rows if r.get("link")]
        return "ok", len(urls)
    except Exception as exc:
        return str(exc)[:120], -1


def fetch_henkel_playwright(company: dict) -> list[dict]:
    """
    Henkel DE Babiel job portal — JSON listing API + HTTP detail pages.
    https://www.henkel.de/karriere/jobs-und-bewerbung
    """
    employer = company["name"]
    max_jobs = int(company.get("max_jobs", 200))
    scoped = bool(company.get("listing_nrw_scoped", False))
    jobs: list[dict] = []

    try:
        rows = _henkel_api_listings(company)
    except Exception as exc:
        logger.warning("Henkel Babiel API listing: %s", exc)
        return []

    logger.info("Henkel Babiel API: %d listing row(s)", len(rows))
    eligible = 0
    for row in rows[:max_jobs]:
        link = row.get("link")
        if not link:
            continue
        job_url = _henkel_row_to_url(link)
        loc = (row.get("location") or "").strip()
        title_api = (row.get("title") or "").strip()
        loc_blob = f"{loc} {title_api}"
        if not scoped and not listing_row_worth_detail_fetch(loc_blob):
            continue
        # Babiel location field is reliable; avoid onsite roles outside NRW (Hamburg, etc.)
        if not scoped and loc and not location_in_nrw(loc) and not text_suggests_remote(
            loc_blob
        ):
            continue
        try:
            r = _SESSION.get(job_url, timeout=45)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "html.parser")
            body = soup.get_text(" ", strip=True)[:12000]
            title = title_api
            if not title:
                t = soup.find("title")
                title = t.get_text(strip=True) if t else ""
        except Exception as exc:
            logger.debug("Henkel job %s: %s", job_url, exc)
            continue
        if not job_eligible_nrw_major(loc, body, listing_nrw_scoped=scoped):
            continue
        eligible += 1
        jobs.append(_build_row(employer, title, job_url, loc, body))

    logger.info(
        "Henkel portal: %d eligible job(s) after NRW/remote filter",
        eligible,
    )
    return jobs


def fetch_jobs_for_employer(company: dict) -> list[dict]:
    st = company.get("source_type", "")
    if st == "smartrecruiters":
        return fetch_smartrecruiters(company)
    if st == "bayer_eightfold":
        return fetch_bayer_eightfold(company)
    if st == "successfactors":
        return fetch_successfactors_listing(company)
    if st == "lanxess_portal":
        return fetch_lanxess_portal(company)
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

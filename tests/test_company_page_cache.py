"""
The watchlist's career-page hash cache.

Career pages change monthly while the checker runs daily, so an unchanged page
must not cost another listing-extraction call. The cache is answered from the
DB, and every DB failure has to degrade to a normal extraction — the smoke
scripts run without a database.
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

for _var in ("DB_HOST", "DB_NAME", "DB_USER", "DB_PASSWORD"):
    os.environ.setdefault(_var, "test")

from scraper import company_scraper as cs  # noqa: E402
from scraper import config  # noqa: E402
from datetime import date, timedelta  # noqa: E402

COMPANY = {
    "name": "Testco",
    "city": "Köln",
    "country": "Germany",
    "career_url": "https://testco.example/karriere/",
    "source_type": "html",
}

PAGE = (
    "<html><body><h1>Karriere</h1>"
    "<p>Wir suchen ab sofort einen Qualified Person (m/w/d) fuer unseren "
    "Standort in Koeln. Bewerbungen an jobs@testco.example.</p>"
    "</body></html>"
)

# Same page with one opening added — the case the cache must NOT swallow.
CHANGED_PAGE = PAGE.replace(
    "</body></html>",
    "<p>Neu: Regulatory Affairs Manager (m/w/d), Standort Koeln.</p></body></html>",
)

DB_ROW = {
    "job_id": "abc123",
    "title": "Qualified Person (m/w/d)",
    "url": "https://testco.example/karriere/qp",
    "location": "Köln",
}


class _FakeResponse:
    def __init__(self, text=PAGE):
        self.text = text
        self.url = COMPANY["career_url"]

    def raise_for_status(self):
        return None


class _FakeListing:
    def __init__(self, title, url="", location=""):
        self.title = title
        self.url = url
        self.location = location


def _install(monkeypatch, *, stored_hash, rows, llm_calls, stored,
             page=PAGE, checked_on=None):
    monkeypatch.setattr(
        cs, "_get_html_career_response", lambda url: _FakeResponse(page)
    )
    monkeypatch.setattr(cs, "_fetch_detail_text", lambda url, career_url: "")
    state = (
        None if stored_hash is None
        else {"page_hash": stored_hash,
              "checked_on": checked_on or date.today()}
    )
    monkeypatch.setattr(cs.db, "get_company_page_state", lambda name: state)
    monkeypatch.setattr(cs.db, "get_active_jobs_for_employer", lambda src, name: rows)
    monkeypatch.setattr(
        cs.db, "set_company_page_hash",
        lambda name, page_hash: stored.append((name, page_hash)),
    )

    def fake_llm(page_text, career_url):
        llm_calls.append(page_text)
        return [_FakeListing("Regulatory Affairs Manager")]

    monkeypatch.setattr(cs, "_extract_listings_with_llm", fake_llm)


def _hash_of(monkeypatch, page):
    """Hash the implementation records for a given page, via a cold run."""
    calls, stored = [], []
    _install(monkeypatch, stored_hash=None, rows=[], llm_calls=calls,
             stored=stored, page=page)
    cs.fetch_jobs(COMPANY)
    return stored[0][1]


def test_different_pages_hash_differently(monkeypatch):
    # Pins the hash to the page CONTENT. Without this, a constant hash — under
    # which the LLM would never run again — satisfies every other test here.
    assert _hash_of(monkeypatch, PAGE) != _hash_of(monkeypatch, CHANGED_PAGE)


def test_edited_page_re_extracts_rather_than_reusing_the_cache(monkeypatch):
    stale = _hash_of(monkeypatch, PAGE)

    calls, stored = [], []
    _install(monkeypatch, stored_hash=stale, rows=[DB_ROW], llm_calls=calls,
             stored=stored, page=CHANGED_PAGE)

    jobs = cs.fetch_jobs(COMPANY)

    assert len(calls) == 1, "a changed page must not be served from the cache"
    assert "Regulatory Affairs Manager" in calls[0]
    assert [j["title"] for j in jobs] == ["Regulatory Affairs Manager"]


def test_cache_expires_so_a_bad_extraction_cannot_persist(monkeypatch):
    page_hash = _hash_of(monkeypatch, PAGE)
    stale_date = date.today() - timedelta(
        days=config.COMPANY_PAGE_CACHE_MAX_DAYS + 1
    )

    calls, stored = [], []
    _install(monkeypatch, stored_hash=page_hash, rows=[DB_ROW],
             llm_calls=calls, stored=stored, checked_on=stale_date)

    cs.fetch_jobs(COMPANY)

    assert len(calls) == 1, "an unchanged page must still be re-extracted eventually"


def test_unchanged_page_skips_the_llm_and_reuses_db_rows(monkeypatch):
    page_hash = _hash_of(monkeypatch, PAGE)

    calls, stored = [], []
    _install(monkeypatch, stored_hash=page_hash, rows=[DB_ROW],
             llm_calls=calls, stored=stored)

    jobs = cs.fetch_jobs(COMPANY)

    assert calls == []
    assert [j["job_id"] for j in jobs] == ["abc123"]
    assert jobs[0]["title"] == "Qualified Person (m/w/d)"
    # Cached rows must be shaped like any other job dict, so insert_job would
    # accept them if they ever reached it.
    assert jobs[0]["source"] == "company_direct"
    assert set(jobs[0]) == set(
        cs._build_job(COMPANY, title="x", url="u", location="l")
    )


def test_changed_page_runs_the_llm_and_records_the_new_hash(monkeypatch):
    calls, stored = [], []
    _install(monkeypatch, stored_hash="stale-hash", rows=[DB_ROW],
             llm_calls=calls, stored=stored)

    jobs = cs.fetch_jobs(COMPANY)

    assert len(calls) == 1
    assert [j["title"] for j in jobs] == ["Regulatory Affairs Manager"]
    assert stored and stored[0][0] == "Testco"


def test_matching_hash_with_no_active_rows_still_extracts(monkeypatch):
    page_hash = _hash_of(monkeypatch, PAGE)

    calls, stored = [], []
    _install(monkeypatch, stored_hash=page_hash, rows=[], llm_calls=calls, stored=stored)

    cs.fetch_jobs(COMPANY)

    # A page that gained its first listing between runs must not be missed.
    assert len(calls) == 1


def test_db_failure_is_a_cache_miss_not_an_error(monkeypatch):
    calls, stored = [], []
    _install(monkeypatch, stored_hash=None, rows=[], llm_calls=calls, stored=stored)

    def boom(*args, **kwargs):
        raise OSError("could not connect to server")

    monkeypatch.setattr(cs.db, "get_company_page_state", boom)
    monkeypatch.setattr(cs.db, "set_company_page_hash", boom)

    jobs = cs.fetch_jobs(COMPANY)

    assert len(calls) == 1
    assert [j["title"] for j in jobs] == ["Regulatory Affairs Manager"]


def test_cache_can_be_disabled(monkeypatch):
    page_hash = _hash_of(monkeypatch, PAGE)

    calls, stored = [], []
    _install(monkeypatch, stored_hash=page_hash, rows=[DB_ROW],
             llm_calls=calls, stored=stored)
    monkeypatch.setattr(config, "COMPANY_PAGE_HASH_CACHE", False)

    cs.fetch_jobs(COMPANY)

    assert len(calls) == 1


def test_a_future_checked_on_does_not_pin_the_cache(monkeypatch):
    # Clock skew writing a future date must not freeze the listing until then.
    page_hash = _hash_of(monkeypatch, PAGE)
    calls, stored = [], []
    _install(monkeypatch, stored_hash=page_hash, rows=[DB_ROW], llm_calls=calls,
             stored=stored, checked_on=date.today() + timedelta(days=365))

    cs.fetch_jobs(COMPANY)

    assert len(calls) == 1


def test_cache_unavailability_is_reported_once_at_warning(monkeypatch, caplog):
    # A missing company_page_state table disables the cache for every company;
    # at DEBUG that is invisible at the runner's INFO level.
    import logging

    monkeypatch.setattr(cs, "_PAGE_CACHE_WARNED", False)
    calls, stored = [], []
    _install(monkeypatch, stored_hash=None, rows=[], llm_calls=calls, stored=stored)

    def boom(*a, **kw):
        raise OSError('relation "company_page_state" does not exist')

    monkeypatch.setattr(cs.db, "get_company_page_state", boom)
    monkeypatch.setattr(cs.db, "set_company_page_hash", boom)

    with caplog.at_level(logging.WARNING, logger=cs.logger.name):
        cs.fetch_jobs(COMPANY)
        cs.fetch_jobs(COMPANY)

    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(warnings) == 1, [r.message for r in warnings]
    assert "migrate_db" in warnings[0].getMessage()


def test_job_id_distinguishes_two_postings_that_differ_only_by_location():
    # The property the join.com dedup rests on, and what makes a blank city
    # churn a company's rows.
    a = cs._make_job_id("Testco", "QA Manager", "Köln")
    b = cs._make_job_id("Testco", "QA Manager", "Bonn")
    assert a != b


def test_cache_is_served_right_up_to_the_expiry_boundary(monkeypatch):
    page_hash = _hash_of(monkeypatch, PAGE)
    boundary = date.today() - timedelta(days=config.COMPANY_PAGE_CACHE_MAX_DAYS - 1)
    calls, stored = [], []
    _install(monkeypatch, stored_hash=page_hash, rows=[DB_ROW], llm_calls=calls,
             stored=stored, checked_on=boundary)

    cs.fetch_jobs(COMPANY)
    assert calls == [], "re-extracted a day early"

    calls2, stored2 = [], []
    _install(monkeypatch, stored_hash=page_hash, rows=[DB_ROW], llm_calls=calls2,
             stored=stored2,
             checked_on=date.today() - timedelta(days=config.COMPANY_PAGE_CACHE_MAX_DAYS))
    cs.fetch_jobs(COMPANY)
    assert len(calls2) == 1, "did not re-extract on the expiry day"


def test_build_job_falls_back_to_the_company_city_and_country():
    # The reason a blank city re-keys job_id, and what the Singleron entry rests on.
    job = cs._build_job(COMPANY, title="QA Manager", url="https://t.example/1", location="")
    assert job["location"] == "Köln, Germany"
    assert job["job_id"] == cs._make_job_id("Testco", "QA Manager", "Köln, Germany")

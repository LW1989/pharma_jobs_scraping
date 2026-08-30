"""
fetch_jobs must distinguish "career page unreadable" from "no openings".

run_company_checker.py delists an employer's whole job set whenever a fetch
returns no jobs, so a swallowed error would deactivate every listing and
re-insert it on the next successful run.
"""

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

for _var in ("DB_HOST", "DB_NAME", "DB_USER", "DB_PASSWORD"):
    os.environ.setdefault(_var, "test")

from scraper import company_scraper as cs  # noqa: E402

COMPANY = {
    "name": "Testco",
    "city": "Köln",
    "country": "Germany",
    "career_url": "https://testco.example/karriere/",
    "source_type": "html",
}


class _FakeResponse:
    def __init__(self, text: str):
        self.text = text
        self.url = COMPANY["career_url"]

    def raise_for_status(self):
        return None


def test_transport_failure_propagates(monkeypatch):
    def boom(url):
        raise ConnectionError("connect timeout")

    monkeypatch.setattr(cs, "_get_html_career_response", boom)
    with pytest.raises(ConnectionError):
        cs.fetch_jobs(COMPANY)


def test_js_rendered_page_raises_rather_than_reporting_zero_jobs(monkeypatch):
    monkeypatch.setattr(
        cs,
        "_get_html_career_response",
        lambda url: _FakeResponse("<html><body><div id='root'></div></body></html>"),
    )
    with pytest.raises(cs.CompanyFetchError):
        cs.fetch_jobs(COMPANY)


def test_readable_page_with_no_openings_returns_empty_list(monkeypatch):
    page = (
        "<html><body><h1>Karriere bei Testco</h1>"
        "<p>Zurzeit haben wir keine offenen Stellen. Schauen Sie bald wieder "
        "vorbei oder senden Sie uns eine Initiativbewerbung an jobs@testco.de.</p>"
        "</body></html>"
    )
    monkeypatch.setattr(cs, "_get_html_career_response", lambda url: _FakeResponse(page))
    monkeypatch.setattr(cs, "_extract_listings_with_llm", lambda *a, **kw: [])
    monkeypatch.setattr(cs.db, "get_company_page_state", lambda name: None)
    monkeypatch.setattr(cs.db, "set_company_page_hash", lambda name, page_hash: None)

    assert cs.fetch_jobs(COMPANY) == []


def test_skip_source_type_returns_empty_without_fetching(monkeypatch):
    def boom(url):
        raise AssertionError("skip companies must not be fetched")

    monkeypatch.setattr(cs, "_get_html_career_response", boom)
    assert cs.fetch_jobs({**COMPANY, "source_type": "skip"}) == []

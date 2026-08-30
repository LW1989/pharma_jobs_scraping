"""
Generic Workday CXS fetcher — the browserless JSON path for *.myworkdayjobs.com.

Only the tenant-resolution and URL-building helpers plus the fetch loop are
exercised here; the CXS endpoints themselves are stubbed.
"""

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

for _var in ("DB_HOST", "DB_NAME", "DB_USER", "DB_PASSWORD"):
    os.environ.setdefault(_var, "test")

from scraper import nrw_major_fetchers as nmf  # noqa: E402

VIATRIS = {
    "name": "Viatris",
    "source_type": "workday_cxs",
    "listing_nrw_scoped": False,
    "workday_cxs_host": "viatris.wd5.myworkdayjobs.com",
    "workday_cxs_tenant": "viatris",
    "workday_cxs_site": "External",
    "workday_cxs_locale": "de-DE",
}


def test_host_from_explicit_key_url_or_tenant():
    assert nmf._workday_cxs_host(VIATRIS) == "viatris.wd5.myworkdayjobs.com"
    assert nmf._workday_cxs_host({
        "workday_url": "https://acme.wd3.myworkdayjobs.com/de-DE/External?x=1",
        "workday_cxs_tenant": "acme", "workday_cxs_site": "External",
    }) == "acme.wd3.myworkdayjobs.com"
    assert nmf._workday_cxs_host({
        "workday_cxs_tenant": "acme", "workday_cxs_wd": "wd1",
    }) == "acme.wd1.myworkdayjobs.com"


def test_host_and_base_reject_incomplete_config():
    with pytest.raises(ValueError):
        nmf._workday_cxs_host({"workday_cxs_tenant": "acme"})
    with pytest.raises(ValueError):
        nmf._workday_cxs_base({"workday_cxs_host": "acme.wd1.myworkdayjobs.com"})


def test_base_is_the_cxs_endpoint_prefix():
    assert nmf._workday_cxs_base(VIATRIS) == (
        "https://viatris.wd5.myworkdayjobs.com/wday/cxs/viatris/External"
    )


def test_public_url_prefers_the_advertised_external_url():
    url = nmf._workday_cxs_public_url(
        VIATRIS, "/job/Troisdorf/QA-Manager_R123",
        {"externalUrl": "https://careers.viatris.com/job/R123"},
    )
    assert url == "https://careers.viatris.com/job/R123"


def test_public_url_falls_back_to_the_workday_site_url():
    url = nmf._workday_cxs_public_url(VIATRIS, "/job/Troisdorf/QA-Manager_R123", {})
    assert url == (
        "https://viatris.wd5.myworkdayjobs.com/de-DE/External"
        "/job/Troisdorf/QA-Manager_R123"
    )


def _stub_cxs(monkeypatch, postings, details):
    def fake_search(company, search_text, *, offset=0, limit=20):
        return (postings, len(postings)) if offset == 0 else ([], len(postings))

    monkeypatch.setattr(nmf, "_workday_cxs_search", fake_search)
    monkeypatch.setattr(
        nmf, "_workday_cxs_detail", lambda company, path: details[path]
    )
    monkeypatch.setattr(nmf.time, "sleep", lambda _s: None)


def test_fetch_keeps_nrw_roles_and_drops_the_rest(monkeypatch):
    postings = [
        {"externalPath": "/job/Troisdorf/QA-Manager_R1", "locationsText": "Troisdorf"},
        {"externalPath": "/job/Muenchen/Sales_R2", "locationsText": "München"},
    ]
    details = {
        "/job/Troisdorf/QA-Manager_R1": {
            "title": "QA Manager (m/w/d)",
            "location": "Troisdorf",
            "country": {"descriptor": "Germany"},
            "jobDescription": "<p>Standort Troisdorf, Nordrhein-Westfalen.</p>",
        },
        "/job/Muenchen/Sales_R2": {
            "title": "Sales Manager",
            "location": "München",
            "country": {"descriptor": "Germany"},
            "jobDescription": "<p>Standort München, Bayern.</p>",
        },
    }
    _stub_cxs(monkeypatch, postings, details)

    jobs = nmf.fetch_workday_cxs(VIATRIS)

    assert [j["title"] for j in jobs] == ["QA Manager (m/w/d)"]
    job = jobs[0]
    assert job["employer"] == "Viatris"
    assert job["source"] == "company_nrw_major"
    assert job["location"].startswith("Troisdorf")
    assert "Nordrhein-Westfalen" in job["job_details"]


def test_listing_prefilter_skips_the_detail_fetch(monkeypatch):
    postings = [{"externalPath": "/job/Tokyo/Analyst_R9", "locationsText": "Tokyo, Japan"}]

    def explode(company, path):
        raise AssertionError("detail must not be fetched for a filtered-out row")

    monkeypatch.setattr(
        nmf, "_workday_cxs_search",
        lambda company, q, *, offset=0, limit=20: ((postings, 1) if offset == 0 else ([], 1)),
    )
    monkeypatch.setattr(nmf, "_workday_cxs_detail", explode)
    monkeypatch.setattr(nmf.time, "sleep", lambda _s: None)

    assert nmf.fetch_workday_cxs(VIATRIS) == []


def test_max_jobs_caps_the_result(monkeypatch):
    postings = [
        {"externalPath": f"/job/Koeln/Role_R{i}", "locationsText": "Köln"}
        for i in range(5)
    ]
    details = {
        p["externalPath"]: {
            "title": f"Role {i}",
            "location": "Köln",
            "country": {"descriptor": "Germany"},
            "jobDescription": "<p>Standort Köln, Nordrhein-Westfalen.</p>",
        }
        for i, p in enumerate(postings)
    }
    _stub_cxs(monkeypatch, postings, details)

    jobs = nmf.fetch_workday_cxs({**VIATRIS, "max_jobs": 2})
    assert len(jobs) == 2


def test_dispatcher_routes_workday_cxs(monkeypatch):
    monkeypatch.setattr(nmf, "fetch_workday_cxs", lambda company: ["sentinel"])
    assert nmf.fetch_jobs_for_employer({"source_type": "workday_cxs"}) == ["sentinel"]

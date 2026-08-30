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


def test_public_url_is_derived_deterministically():
    url = nmf._workday_cxs_public_url(VIATRIS, "/job/Troisdorf/QA-Manager_R123")
    assert url == (
        "https://viatris.wd5.myworkdayjobs.com/de-DE/External"
        "/job/Troisdorf/QA-Manager_R123"
    )


def test_job_id_does_not_depend_on_the_servers_optional_external_url(monkeypatch):
    # _build_row hashes the URL into job_id, so a field Workday may omit or vary
    # between runs would re-key the same posting and churn it in the digest.
    postings = [{"externalPath": "/job/Troisdorf/QA_R1", "locationsText": "Troisdorf"}]
    detail = {
        "title": "QA Manager",
        "location": "Troisdorf",
        "country": {"descriptor": "Germany"},
        "jobDescription": "<p>Standort Troisdorf, Nordrhein-Westfalen.</p>",
    }
    ids = []
    for external in ("https://careers.viatris.com/job/R1", None, ""):
        payload = dict(detail)
        if external is not None:
            payload["externalUrl"] = external
        _stub_cxs(monkeypatch, postings, {"/job/Troisdorf/QA_R1": payload})
        ids.append(nmf.fetch_workday_cxs(VIATRIS)[0]["job_id"])
    assert len(set(ids)) == 1, f"job_id varied with externalUrl: {ids}"


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


def test_multi_location_listing_row_is_not_dropped_before_the_detail_fetch(monkeypatch):
    # Workday reports an aggregate locationsText for multi-site postings. The
    # NRW location helpers are a whitelist that drops whatever they do not
    # recognise, so gating the detail GET on the listing snippet would discard
    # a Troisdorf role the eligibility pass — which reads the body — accepts.
    postings = [{"externalPath": "/job/DE/QA_R1", "locationsText": "2 Locations"}]
    details = {
        "/job/DE/QA_R1": {
            "title": "QA Manager (m/w/d)",
            "location": "Troisdorf",
            "country": {"descriptor": "Germany"},
            "jobDescription": "<p>Standort Troisdorf, Nordrhein-Westfalen.</p>",
        }
    }
    _stub_cxs(monkeypatch, postings, details)

    assert [j["title"] for j in nmf.fetch_workday_cxs(VIATRIS)] == ["QA Manager (m/w/d)"]


def test_listing_failure_raises_instead_of_delisting_the_employer(monkeypatch):
    # run_nrw_major_checker.py delists every active row an employer's fetch does
    # not return, so a 5xx must not look like "the board lists nothing".
    def boom(company, search_text, *, offset=0, limit=20):
        raise ConnectionError("503 from the CXS endpoint")

    monkeypatch.setattr(nmf, "_workday_cxs_search", boom)
    monkeypatch.setattr(nmf.time, "sleep", lambda _s: None)

    with pytest.raises(ConnectionError):
        nmf.fetch_workday_cxs(VIATRIS)


def test_total_detail_failure_raises(monkeypatch):
    postings = [{"externalPath": "/job/DE/QA_R1", "locationsText": "Troisdorf"}]
    monkeypatch.setattr(
        nmf, "_workday_cxs_search",
        lambda c, q, *, offset=0, limit=20: ((postings, 1) if offset == 0 else ([], 1)),
    )

    def boom(company, path):
        raise ConnectionError("detail 503")

    monkeypatch.setattr(nmf, "_workday_cxs_detail", boom)
    monkeypatch.setattr(nmf.time, "sleep", lambda _s: None)

    with pytest.raises(RuntimeError):
        nmf.fetch_workday_cxs(VIATRIS)


def test_a_broad_query_cannot_starve_a_targeted_one(monkeypatch):
    # With one shared budget, the unbounded query consumed it all and the
    # targeted query never ran — the exact case Viatris was configured for.
    company = {
        **VIATRIS,
        "workday_cxs_queries": ["", "Troisdorf"],
        "workday_cxs_max_list_jobs": 20,
        "workday_cxs_page_size": 10,
    }
    broad = [
        {"externalPath": f"/job/DE/Role_{i}", "locationsText": "München"}
        for i in range(200)
    ]
    targeted = [
        {"externalPath": "/job/Troisdorf/QA_T1", "locationsText": "Troisdorf"}
    ]
    issued = []

    def fake_search(c, search_text, *, offset=0, limit=20):
        issued.append(search_text)
        pool = targeted if search_text == "Troisdorf" else broad
        return pool[offset:offset + limit], len(pool)

    monkeypatch.setattr(nmf, "_workday_cxs_search", fake_search)
    monkeypatch.setattr(nmf.time, "sleep", lambda _s: None)

    refs = nmf._workday_cxs_collect_refs(company, "Viatris")
    paths = {r["externalPath"] for r in refs}

    assert "Troisdorf" in issued, f"targeted query never ran; issued={set(issued)}"
    assert "/job/Troisdorf/QA_T1" in paths
    assert len(refs) <= 20


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


def _paged(pool, page_size=20):
    def search(company, q, *, offset=0, limit=page_size):
        return pool[offset:offset + limit], len(pool)
    return search


def test_paging_is_bounded_by_pages_not_by_rows_collected(monkeypatch):
    # collected_here only advances for NEW paths, so a query returning rows the
    # previous one already gave us would otherwise page to the remote total.
    board = [{"externalPath": f"/job/DE/R{i}", "locationsText": "M"} for i in range(2000)]
    company = {
        **VIATRIS,
        "workday_cxs_queries": ["", "A"],
        "workday_cxs_max_list_jobs": 120,
        "workday_cxs_page_size": 20,
    }
    pages = []

    def search(c, q, *, offset=0, limit=20):
        pages.append((q, offset))
        pool = board if q == "" else board[:40] * 50   # all duplicates
        return pool[offset:offset + limit], 2000

    monkeypatch.setattr(nmf, "_workday_cxs_search", search)
    monkeypatch.setattr(nmf.time, "sleep", lambda _s: None)

    nmf._workday_cxs_collect_refs(company, "T")

    dup_pages = [p for p in pages if p[0] == "A"]
    assert len(dup_pages) <= company.get("workday_cxs_max_listing_pages", 10), (
        f"duplicate-only query issued {len(dup_pages)} requests"
    )


def test_payload_drift_raises_rather_than_reporting_an_empty_board(monkeypatch):
    # Rows come back, but externalPath is gone: an empty list here would delist
    # the employer's entire job set.
    renamed = [{"jobPath": f"/job/DE/R{i}", "locationsText": "Troisdorf"} for i in range(50)]
    monkeypatch.setattr(nmf, "_workday_cxs_search", _paged(renamed))
    monkeypatch.setattr(nmf.time, "sleep", lambda _s: None)

    with pytest.raises(nmf.WorkdayCxsFetchError):
        nmf.fetch_workday_cxs(VIATRIS)


def test_mostly_failing_detail_fetches_raise_instead_of_returning_a_short_list(monkeypatch):
    postings = [{"externalPath": f"/job/DE/R{i}", "locationsText": "Troisdorf"}
                for i in range(20)]
    good = {
        "title": "QA Manager", "location": "Troisdorf",
        "country": {"descriptor": "Germany"},
        "jobDescription": "<p>Standort Troisdorf, Nordrhein-Westfalen.</p>",
    }
    monkeypatch.setattr(nmf, "_workday_cxs_search", _paged(postings))
    monkeypatch.setattr(nmf.time, "sleep", lambda _s: None)

    def mostly_broken(company, path):
        if path == "/job/DE/R0":
            return good
        raise ConnectionError("503")

    monkeypatch.setattr(nmf, "_workday_cxs_detail", mostly_broken)

    with pytest.raises(nmf.WorkdayCxsFetchError):
        nmf.fetch_workday_cxs(VIATRIS)


def test_every_detail_request_is_throttled_not_just_the_ones_that_yield_a_job(monkeypatch):
    postings = [{"externalPath": f"/job/DE/R{i}", "locationsText": "München"}
                for i in range(10)]
    monkeypatch.setattr(nmf, "_workday_cxs_search", _paged(postings))
    monkeypatch.setattr(
        nmf, "_workday_cxs_detail",
        lambda c, p: {"title": "Sales", "location": "München",
                      "country": {"descriptor": "Germany"},
                      "jobDescription": "<p>Standort München, Bayern.</p>"},
    )
    sleeps = []
    monkeypatch.setattr(nmf.time, "sleep", lambda s: sleeps.append(s))

    assert nmf.fetch_workday_cxs(VIATRIS) == []
    assert len(sleeps) >= 10, f"only {len(sleeps)} sleeps for 10 detail GETs"


def test_probe_reports_eligible_jobs_not_raw_listing_rows(monkeypatch):
    # MIN_EXPECTED is an NRW number; probing the country-wide listing would
    # report ~120 and "ok" while the fetcher yields nothing.
    postings = [{"externalPath": f"/job/DE/R{i}", "locationsText": "München"}
                for i in range(30)]
    monkeypatch.setattr(nmf, "_workday_cxs_search", _paged(postings))
    monkeypatch.setattr(
        nmf, "_workday_cxs_detail",
        lambda c, p: {"title": "Sales", "location": "München",
                      "country": {"descriptor": "Germany"},
                      "jobDescription": "<p>Standort München, Bayern.</p>"},
    )
    monkeypatch.setattr(nmf.time, "sleep", lambda _s: None)

    status, n = nmf.probe_workday_cxs_job_count(VIATRIS)
    assert (status, n) == ("ok", 0), f"probe counted listing rows: {n}"


def test_probe_survives_a_row_without_a_name(monkeypatch):
    monkeypatch.setattr(nmf, "_workday_cxs_search", _paged([]))
    monkeypatch.setattr(nmf.time, "sleep", lambda _s: None)
    company = {k: v for k, v in VIATRIS.items() if k != "name"}

    status, n = nmf.probe_workday_cxs_job_count(company)
    assert (status, n) == ("ok", 0)


def test_total_is_capped_even_when_a_whole_page_overshoots_the_budget(monkeypatch):
    # A query takes whole pages, so the last one can push the union past
    # max_list; the caller's max_jobs is a separate, later cap.
    pool_a = [{"externalPath": f"/a/{i}", "locationsText": "M"} for i in range(100)]
    pool_b = [{"externalPath": f"/b/{i}", "locationsText": "M"} for i in range(100)]
    company = {
        **VIATRIS,
        "workday_cxs_queries": ["a", "b"],
        "workday_cxs_max_list_jobs": 25,
        "workday_cxs_page_size": 20,
    }

    def search(c, q, *, offset=0, limit=20):
        pool = pool_a if q == "a" else pool_b
        return pool[offset:offset + limit], len(pool)

    monkeypatch.setattr(nmf, "_workday_cxs_search", search)
    monkeypatch.setattr(nmf.time, "sleep", lambda _s: None)

    refs = nmf._workday_cxs_collect_refs(company, "T")
    assert len(refs) == 25, f"max_list not enforced: {len(refs)}"


def test_more_queries_than_the_budget_still_collects(monkeypatch):
    # max_list // len(queries) floors to 0 with many queries; without the
    # page_size floor every query gets a zero budget and nothing is fetched.
    pool = [{"externalPath": f"/x/{i}", "locationsText": "Troisdorf"} for i in range(50)]
    company = {
        **VIATRIS,
        "workday_cxs_queries": [f"q{i}" for i in range(30)],
        "workday_cxs_max_list_jobs": 20,
        "workday_cxs_page_size": 20,
    }
    monkeypatch.setattr(nmf, "_workday_cxs_search", _paged(pool))
    monkeypatch.setattr(nmf.time, "sleep", lambda _s: None)

    refs = nmf._workday_cxs_collect_refs(company, "T")
    assert len(refs) == 20, f"per-query budget starved every query: {len(refs)}"

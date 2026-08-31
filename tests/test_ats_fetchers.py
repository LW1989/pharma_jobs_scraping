"""
The ATS fetchers in company_scraper: personio, workable, recruitee, join.

Every one of these builds the location and URL that feed job_id
(md5(name|title|location)), so a dropped fallback silently re-keys a company's
whole job set and churns it through the digest.
"""

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

for _var in ("DB_HOST", "DB_NAME", "DB_USER", "DB_PASSWORD"):
    os.environ.setdefault(_var, "test")

from scraper import company_scraper as cs  # noqa: E402


class _Resp:
    def __init__(self, *, text="", payload=None, content=b"", url=""):
        self.text = text
        self._payload = payload
        self.content = content
        self.url = url

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def _no_detail(monkeypatch):
    monkeypatch.setattr(cs, "_fetch_detail_text", lambda url, career_url: "")
    monkeypatch.setattr(cs.time, "sleep", lambda _s: None)


# ── personio ───────────────────────────────────────────────────────────────

PERSONIO_XML = b"""<?xml version="1.0"?>
<workzag-jobs>
  <position>
    <id>42</id><name>QA Manager</name><office></office>
    <jobDescriptions><jobDescription><name>Aufgaben</name>
      <value>&lt;p&gt;Freigabe von Chargen&lt;/p&gt;</value>
    </jobDescription></jobDescriptions>
  </position>
</workzag-jobs>"""


def test_personio_falls_back_to_the_company_city_when_office_is_blank(monkeypatch):
    monkeypatch.setattr(cs._SESSION, "get",
                        lambda url, **kw: _Resp(content=PERSONIO_XML))
    _no_detail(monkeypatch)
    company = {"name": "Prosion", "city": "Köln", "country": "Germany",
               "career_url": "https://prosion-gmbh.jobs.personio.de",
               "source_type": "personio", "slug": "prosion-gmbh"}

    jobs = cs.fetch_jobs(company)

    assert len(jobs) == 1
    assert jobs[0]["location"] == "Köln", "blank office must not blank the location"
    assert "Freigabe von Chargen" in jobs[0]["job_details"]


# ── recruitee ──────────────────────────────────────────────────────────────

def test_recruitee_prefers_the_boards_own_careers_url(monkeypatch):
    payload = {"offers": [{
        "title": "Lab Technician", "location": "Köln", "slug": "lab-technician",
        "careers_url": "https://careers.cellex.me/vacancy/lab-technician",
        "description": "<p>Zellkultur</p>",
    }]}
    monkeypatch.setattr(cs._SESSION, "get", lambda url, **kw: _Resp(payload=payload))
    _no_detail(monkeypatch)
    company = {"name": "Cellex", "city": "Köln", "country": "Germany",
               "career_url": "https://cellexgmbh.recruitee.com",
               "source_type": "recruitee", "slug": "cellexgmbh"}

    jobs = cs.fetch_jobs(company)

    assert jobs[0]["url"] == "https://careers.cellex.me/vacancy/lab-technician"
    assert jobs[0]["job_details"] == "Zellkultur"


# ── workable ───────────────────────────────────────────────────────────────

def test_workable_builds_the_location_from_city_and_country(monkeypatch):
    payload = {"results": [{
        "title": "CRA", "shortcode": "ABC123",
        "location": {"city": "Köln", "country": "Germany"},
    }]}
    monkeypatch.setattr(cs._SESSION, "post", lambda url, **kw: _Resp(payload=payload))
    _no_detail(monkeypatch)
    company = {"name": "Allucent", "city": "Köln", "country": "Germany",
               "career_url": "https://apply.workable.com/allucent/",
               "source_type": "workable", "slug": "allucent"}

    jobs = cs.fetch_jobs(company)

    assert jobs[0]["location"] == "Köln, Germany"
    assert jobs[0]["url"] == "https://apply.workable.com/allucent/j/ABC123/"


# ── join ───────────────────────────────────────────────────────────────────

JOIN_COMPANY = {"name": "enua", "city": "Köln", "country": "Germany",
                "career_url": "https://join.com/companies/enua",
                "source_type": "join", "slug": "enua"}


def test_join_prefers_jsonld_over_next_data(monkeypatch):
    # Both payloads are present and disagree; JSON-LD is the schema.org contract
    # and __NEXT_DATA__ is a shape that changes between releases.
    page = """
    <script type="application/ld+json">
    {"@type":"JobPosting","title":"From JSON-LD","url":"https://join.com/ld"}</script>
    <script id="__NEXT_DATA__">{"props":{"jobs":[{"title":"From NEXT_DATA"}]}}</script>
    """
    monkeypatch.setattr(cs, "_get_html_career_response", lambda url: _Resp(text=page))
    _no_detail(monkeypatch)

    assert [j["title"] for j in cs.fetch_jobs(JOIN_COMPANY)] == ["From JSON-LD"]


def test_join_falls_back_to_the_career_url_when_a_listing_has_none(monkeypatch):
    page = ('<script id="__NEXT_DATA__">{"props":{"jobs":'
            '[{"title":"Werkstudent"}]}}</script>')
    monkeypatch.setattr(cs, "_get_html_career_response", lambda url: _Resp(text=page))
    _no_detail(monkeypatch)

    jobs = cs.fetch_jobs(JOIN_COMPANY)

    assert jobs[0]["url"] == "https://join.com/companies/enua"
    assert jobs[0]["location"] == "Köln, Germany"


def test_join_falls_back_to_html_extraction_when_no_payload_is_present(monkeypatch):
    monkeypatch.setattr(cs, "_get_html_career_response",
                        lambda url: _Resp(text="<html><body>nothing structured</body></html>"))
    called = []
    monkeypatch.setattr(cs, "_fetch_html_llm", lambda company: called.append(company) or [])

    assert cs.fetch_jobs(JOIN_COMPANY) == []
    assert called, "join must degrade to the html path, not report an empty board"

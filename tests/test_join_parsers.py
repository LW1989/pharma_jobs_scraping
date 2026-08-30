"""Unit tests for the join.com structured-payload parsers in company_scraper."""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# scraper.config reads DB settings at import time; these parsers touch neither
# the DB nor OpenAI, so placeholders are enough to import the module.
for _var in ("DB_HOST", "DB_NAME", "DB_USER", "DB_PASSWORD"):
    os.environ.setdefault(_var, "test")

from scraper.company_scraper import (  # noqa: E402
    _jobs_from_jsonld,
    _jobs_from_next_data,
)

JSONLD_PAGE = """
<html><head>
<script type="application/ld+json">
{"@context":"https://schema.org","@graph":[
 {"@type":"Organization","name":"enua"},
 {"@type":"JobPosting","title":"Pharmazeut (m/w/d)",
  "url":"https://join.com/companies/enua/123-pharmazeut",
  "description":"<p>Wir suchen</p><ul><li>Approbation</li></ul>",
  "jobLocation":{"@type":"Place",
    "address":{"addressLocality":"Köln","addressCountry":"DE"}}}
]}
</script>
<script type="application/ld+json">
{"@type":"JobPosting","title":"QA Manager",
 "url":"https://join.com/companies/enua/456-qa","description":"QA text",
 "jobLocation":[{"address":{"addressLocality":"Remote"}}]}
</script>
</head><body></body></html>
"""

NEXT_DATA_PAGE = """
<html><body><script id="__NEXT_DATA__" type="application/json">
{"props":{"pageProps":{"company":{"name":"enua","jobs":[
  {"id":9,"title":"Sales Rep","url":"https://join.com/companies/enua/9-sales",
   "location":"Köln","description":"<b>Sell</b> things"},
  {"id":10,"title":"Intern","city":"Düsseldorf"},
  {"noTitle":true}
]},"unrelatedList":[1,2,3]}}}
</script></body></html>
"""


def _by_title(jobs):
    return {j["title"]: j for j in jobs}


def test_jsonld_reads_graph_and_bare_postings():
    jobs = _by_title(_jobs_from_jsonld(JSONLD_PAGE))
    assert set(jobs) == {"Pharmazeut (m/w/d)", "QA Manager"}
    assert jobs["Pharmazeut (m/w/d)"]["url"].endswith("/123-pharmazeut")


def test_jsonld_flattens_address_and_strips_description_html():
    jobs = _by_title(_jobs_from_jsonld(JSONLD_PAGE))
    assert jobs["Pharmazeut (m/w/d)"]["location"] == "Köln, DE"
    assert "Approbation" in jobs["Pharmazeut (m/w/d)"]["details"]
    assert "<li>" not in jobs["Pharmazeut (m/w/d)"]["details"]


def test_jsonld_accepts_joblocation_as_a_list():
    jobs = _by_title(_jobs_from_jsonld(JSONLD_PAGE))
    assert jobs["QA Manager"]["location"] == "Remote"


def test_next_data_finds_jobs_under_a_job_ish_key():
    jobs = _by_title(_jobs_from_next_data(NEXT_DATA_PAGE))
    assert set(jobs) == {"Sales Rep", "Intern"}
    assert jobs["Sales Rep"]["location"] == "Köln"
    assert jobs["Sales Rep"]["details"] == "Sell\nthings"


def test_next_data_tolerates_missing_url_and_falls_back_to_city():
    jobs = _by_title(_jobs_from_next_data(NEXT_DATA_PAGE))
    assert jobs["Intern"]["url"] == ""
    assert jobs["Intern"]["location"] == "Düsseldorf"


def test_parsers_return_empty_on_missing_or_malformed_payloads():
    assert _jobs_from_jsonld("<html></html>") == []
    assert _jobs_from_next_data("<html></html>") == []
    assert _jobs_from_jsonld('<script type="application/ld+json">{oops</script>') == []
    assert _jobs_from_next_data('<script id="__NEXT_DATA__">{oops</script>') == []

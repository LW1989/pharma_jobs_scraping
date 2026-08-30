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
],
"articles":[{"title":"Blog: our Series A","url":"https://enua.de/blog/series-a"}],
"teamMembers":[{"name":"Dr. Berger","title":"Head of Lab"}]},
"unrelatedList":[1,2,3]}}}
</script></body></html>
"""

# The standard Next.js shape: the real posting under "jobs", a bare stub with
# the same title under "similarJobs". Traversal order must not decide which wins.
SHADOWED_PAGE = """
<html><body><script id="__NEXT_DATA__" type="application/json">
{"props":{"pageProps":{
  "similarJobs":[{"id":99,"title":"QA Manager"}],
  "jobs":[{"id":1,"title":"QA Manager","url":"https://join.com/companies/enua/1-qa",
           "location":"Köln","description":"the real posting"}]}}}
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


def test_non_job_lists_are_not_harvested():
    # Pins the job-ish key selection: "articles" and "teamMembers" both hold
    # dicts with a "title", so a regex that matched every key would sweep a blog
    # post and a staff bio into the watchlist.
    titles = set(_by_title(_jobs_from_next_data(NEXT_DATA_PAGE)))
    assert titles == {"Sales Rep", "Intern"}
    assert "Blog: our Series A" not in titles
    assert "Head of Lab" not in titles


def test_a_stub_never_displaces_the_real_posting_it_shadows():
    jobs = _jobs_from_next_data(SHADOWED_PAGE)

    assert len(jobs) == 1, f"the duplicate title should collapse to one: {jobs}"
    job = jobs[0]
    assert job["url"] == "https://join.com/companies/enua/1-qa"
    assert job["location"] == "Köln"
    assert job["details"] == "the real posting"


def test_the_richest_record_wins_regardless_of_payload_order():
    reordered = SHADOWED_PAGE.replace(
        '"similarJobs":[{"id":99,"title":"QA Manager"}],\n  ', ""
    ).replace(
        '"location":"Köln","description":"the real posting"}]}}}',
        '"location":"Köln","description":"the real posting"}],'
        '"similarJobs":[{"id":99,"title":"QA Manager"}]}}}',
    )
    jobs = _jobs_from_next_data(reordered)

    assert len(jobs) == 1
    assert jobs[0]["url"].endswith("/1-qa")


STUB_RICHER_PAGE = """
<html><body><script id="__NEXT_DATA__" type="application/json">
{"props":{
  "similarJobs":[{"title":"QA Manager","url":"https://join.com/x/99",
                  "location":"Berlin","description":"stub"}],
  "jobs":[{"title":"QA Manager","url":"https://join.com/companies/enua/1-qa",
           "description":"the real posting"}]}}
</script></body></html>
"""

TWO_CITIES_PAGE = """
<html><body><script id="__NEXT_DATA__" type="application/json">
{"props":{"jobs":[
  {"title":"QA Manager","url":"https://join.com/1","location":"Köln"},
  {"title":"QA Manager","url":"https://join.com/2","location":"Bonn"}]}}
</script></body></html>
"""

NESTED_PAGE = """
<html><body><script id="__NEXT_DATA__" type="application/json">
{"blocks":[[{"jobs":[{"title":"Buried Job","url":"https://join.com/deep"}]}]]}
</script></body></html>
"""


def test_a_primary_list_beats_a_stub_that_carries_more_fields():
    # The stub has a location the real posting omits, so "most filled fields"
    # picks it — and location feeds job_id.
    jobs = _jobs_from_next_data(STUB_RICHER_PAGE)

    assert len(jobs) == 1, jobs
    assert jobs[0]["url"] == "https://join.com/companies/enua/1-qa"
    assert jobs[0]["details"] == "the real posting"
    assert jobs[0]["location"] == ""


def test_two_real_postings_sharing_a_title_both_survive():
    # job_id is md5(name|title|location), so these are two distinct DB rows.
    jobs = _jobs_from_next_data(TWO_CITIES_PAGE)

    assert {j["location"] for j in jobs} == {"Köln", "Bonn"}
    assert {j["url"] for j in jobs} == {"https://join.com/1", "https://join.com/2"}


def test_lists_nested_inside_lists_are_still_walked():
    assert [j["title"] for j in _jobs_from_next_data(NESTED_PAGE)] == ["Buried Job"]

"""
Link ranking in scripts/discover_career_urls.py.

The script's whole value is picking the right link out of a homepage's nav and
footer, so the ranking is tested against realistic markup rather than trusted.
"""

import importlib.util
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

for _var in ("DB_HOST", "DB_NAME", "DB_USER", "DB_PASSWORD"):
    os.environ.setdefault(_var, "test")

_spec = importlib.util.spec_from_file_location(
    "discover_career_urls", ROOT / "scripts" / "discover_career_urls.py"
)
disc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(disc)


class _FakeResponse:
    def __init__(self, text, url):
        self.text = text
        self.url = url

    def raise_for_status(self):
        return None


def _rank(monkeypatch, html, base="https://acme.de/"):
    monkeypatch.setattr(
        disc, "_get_html_career_response", lambda url: _FakeResponse(html, base)
    )
    return disc.discover("Acme", base)


GERMAN_HOMEPAGE = """
<html><body>
<nav>
  <a href="/de/produkte">Produkte</a>
  <a href="/de/ueber-uns">Über uns</a>
  <a href="/de/ueber-uns/karriere/">Karriere</a>
  <a href="/de/kontakt">Kontakt</a>
</nav>
<main>
  <a href="/de/team">Unser Team</a>
  <a href="https://www.linkedin.com/company/acme/jobs">Jobs auf LinkedIn</a>
  <a href="/de/newsletter/jobalert">Job-Alert abonnieren</a>
  <a href="/downloads/stellenanzeige-qp.pdf">Stellenanzeige QP (PDF)</a>
</main>
<footer>
  <a href="/de/impressum">Impressum</a>
  <a href="/de/offene-stellen?gclid=XYZ&amp;utm_source=g">Offene Stellen</a>
</footer>
</body></html>
"""


def test_karriere_link_wins_over_weaker_matches(monkeypatch):
    ranked = _rank(monkeypatch, GERMAN_HOMEPAGE)
    assert ranked[0][1] == "https://acme.de/de/ueber-uns/karriere/"


def test_tracking_params_are_stripped(monkeypatch):
    urls = [url for _score, url, _text in _rank(monkeypatch, GERMAN_HOMEPAGE)]
    assert "https://acme.de/de/offene-stellen" in urls
    assert not any("gclid" in u or "utm_source" in u for u in urls)


def test_social_job_alerts_and_pdfs_are_rejected(monkeypatch):
    urls = [url for _score, url, _text in _rank(monkeypatch, GERMAN_HOMEPAGE)]
    assert not any("linkedin.com" in u for u in urls)
    assert not any("jobalert" in u for u in urls)
    assert not any(u.endswith(".pdf") for u in urls)


def test_job_subdomain_beats_a_generic_team_link(monkeypatch):
    html = """<html><body>
      <a href="/team">Team</a>
      <a href="https://jobs.acme.de/">Zu unseren Stellenangeboten</a>
    </body></html>"""
    ranked = _rank(monkeypatch, html)
    assert ranked[0][1] == "https://jobs.acme.de/"


def test_ats_link_wins_even_with_no_keyword_anywhere(monkeypatch):
    # join.com/companies/{slug} has no career keyword in URL or anchor text.
    html = """<html><body>
      <a href="/ueber-uns">Über uns</a>
      <a href="/karriere">Karriere</a>
      <a href="https://join.com/companies/acme">Wir stellen ein</a>
    </body></html>"""
    ranked = _rank(monkeypatch, html)
    assert ranked[0][1] == "https://join.com/companies/acme"


def test_offsite_non_ats_link_is_penalised(monkeypatch):
    html = """<html><body>
      <a href="/unternehmen/karriere">Karriere</a>
      <a href="https://some-jobboard.example/karriere">Karriere bei Partnern</a>
    </body></html>"""
    ranked = _rank(monkeypatch, html)
    assert ranked[0][1] == "https://acme.de/unternehmen/karriere"


def test_www_and_bare_host_count_as_the_same_site(monkeypatch):
    html = """<html><body>
      <a href="https://www.acme.de/karriere">Karriere</a>
    </body></html>"""
    score, url, _text = _rank(monkeypatch, html)[0]
    assert url == "https://www.acme.de/karriere"
    assert score == 13  # 10 + 3 for the keyword being in the URL, no penalty


def test_page_with_nothing_career_like_ranks_empty(monkeypatch):
    html = """<html><body>
      <a href="/produkte">Produkte</a><a href="/impressum">Impressum</a>
    </body></html>"""
    assert _rank(monkeypatch, html) == []


def test_yaml_rows_are_parseable_even_for_a_name_starting_with_a_tag_char():
    # "!mmunetrue" is a YAML tag unless quoted; a hand-built row for it broke
    # the whole file.
    import yaml

    block = (
        disc._yaml_row("!mmunetrue", "", "https://www.immunetrue.eu", "skip")
        + disc._yaml_row("Acme & Co", "Köln", "https://acme.de/karriere")
    )
    parsed = yaml.safe_load("companies:\n" + block)["companies"]

    assert [c["name"] for c in parsed] == ["!mmunetrue", "Acme & Co"]
    assert parsed[0]["source_type"] == "skip"
    assert parsed[1]["career_url"] == "https://acme.de/karriere"


def test_team_pages_no_longer_score_at_all(monkeypatch):
    # A management-team page is never a job listing; scoring it produced
    # ready-to-paste rows pointing at /team and /about.
    html = """<html><body>
      <a href="/company/management-team/advisory-board.html">Advisory Board</a>
      <a href="/team">Unser Team</a>
      <a href="/homepage/about/">Our team</a>
    </body></html>"""
    assert _rank(monkeypatch, html) == []


def test_confidence_threshold_sits_above_about_us_noise():
    # Real-run evidence: genuine career pages scored 13+, noise scored 2-5.
    assert disc._score_link(
        "https://acme.de/de/ueber-uns/karriere/", "Karriere", "acme.de"
    ) >= disc.CONFIDENT_SCORE
    assert disc._score_link(
        "https://acme.de/zielgruppen/sonstige-bewerber", "Sonstige Bewerber", "acme.de"
    ) < disc.CONFIDENT_SCORE

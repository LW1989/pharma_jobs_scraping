"""
The sheet sync, which runs nightly before the company checker.

It rewrites companies.yaml from a Google Sheet, so it is the one place that can
silently undo a curation decision: re-adding an excluded association, adding a
company a second time under its sheet spelling, or walking a researched career
URL back to the homepage the sheet holds.
"""

import importlib.util
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

for _var in ("DB_HOST", "DB_NAME", "DB_USER", "DB_PASSWORD"):
    os.environ.setdefault(_var, "test")

from scraper.urls import canonical, is_less_specific, strip_tracking_params  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "sync_companies_from_sheet", ROOT / "scripts" / "sync_companies_from_sheet.py"
)
sync = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sync)


# ── URL helpers ────────────────────────────────────────────────────────────

def test_tracking_params_are_stripped_but_real_ones_kept():
    assert strip_tracking_params(
        "https://cellavent.de/pages/karriere?_su_rec=abc&_su_rec_id=1"
    ) == "https://cellavent.de/pages/karriere"
    assert strip_tracking_params("https://abclonal.com/?srsltid=XYZ") == "https://abclonal.com/"
    # A functional parameter must survive.
    assert strip_tracking_params(
        "https://hal-allergy.talent-soft.com/startpagina.aspx?LCID=1043"
    ) == "https://hal-allergy.talent-soft.com/startpagina.aspx?LCID=1043"


def test_canonical_ignores_trailing_slash_and_tracking():
    assert canonical("https://abclonal.com/?srsltid=X") == canonical("https://abclonal.com")


def test_is_less_specific_recognises_a_weaker_url():
    # Homepage or section index against a researched career page.
    assert is_less_specific("https://www.pbpharma.de", "https://www.pbpharma.de/karriere/")
    assert is_less_specific("https://www.medperion.de/en",
                            "https://www.medperion.de/en/karriere-bei-uns")
    # Neither contains the other, but ours names a career page and theirs does not.
    assert is_less_specific("https://www.synbiotic.com/de/home",
                            "https://www.synbiotic.com/de/karriere")


def test_is_less_specific_matches_hosts_case_insensitively():
    # A homepage downgrade with a differently-cased host must still be caught.
    assert is_less_specific("https://ACME.de", "https://acme.de/karriere")


def test_is_less_specific_compares_whole_path_segments():
    # A coincidental character prefix where neither path is a career page must
    # not read as a downgrade: /cart is not a deeper form of /car.
    assert not is_less_specific("https://acme.de/car", "https://acme.de/cart")
    # But a real deeper segment still is.
    assert is_less_specific("https://acme.de/car", "https://acme.de/car/details")


def test_is_less_specific_accepts_a_genuine_change():
    # A different host is a real move, not a downgrade.
    assert not is_less_specific("https://jobs.acme.de/", "https://acme.de/karriere")
    # A more specific path is an improvement.
    assert not is_less_specific("https://acme.de/karriere/stellen",
                                "https://acme.de/karriere")


# ── name matching ──────────────────────────────────────────────────────────

def test_legal_forms_and_decorations_do_not_create_duplicates():
    same = [
        ("BOLDER Arzneimittel GmbH & Co. KG", "BOLDER Arzneimittel"),
        ("Medical CNBS® Pharma GmbH", "Medical CNBS Pharma"),
        ("Singleron Biotechnologies HR", "Singleron Biotechnologies"),
        ("Orthogen AG", "Orthogen"),
        ("CellSystems®", "CellSystems"),
    ]
    for sheet_name, yaml_name in same:
        assert sync._normalise_name(sheet_name) == sync._normalise_name(yaml_name), sheet_name


def test_distinct_companies_do_not_normalise_together():
    assert sync._normalise_name("PS Pharma Service") != sync._normalise_name("Schantl Pharma Service")
    assert sync._normalise_name("Active Pharma") != sync._normalise_name("ToRa Pharma")


def test_excluded_companies_and_nrw_majors_are_blocked():
    blocked = sync._blocked_names()
    for name in ("Pharma Deutschland", "PLCD Pharma-Lizenz-Club Deutschland e.V.",
                 "Forschungsvereinigung der Arzneimittel-Hersteller e.V. (FAH)",
                 "Cellex Cell Professionals", "Klosterfrau Group", "Lonza Cologne",
                 "Viatris", "Klosterfrau"):
        assert sync._normalise_name(name) in blocked, name


def test_a_watchlist_company_is_not_blocked():
    blocked = sync._blocked_names()
    for name in ("Numaferm", "Cannamedical", "Togontech"):
        assert sync._normalise_name(name) not in blocked, name


def test_aliases_map_sheet_spellings_to_the_consolidated_entry():
    existing = sync._load_existing()
    aliases = sync._alias_map(existing)
    assert aliases[sync._normalise_name("Cannamedical Pharma")] == "Cannamedical"
    assert aliases[sync._normalise_name("Cannamedical Biotech")] == "Cannamedical"
    assert aliases[sync._normalise_name("Medios Solutions Bonn")] == "Medios Solutions"
    assert aliases[sync._normalise_name("MEDPERION Sales & Communication")] == "MEDPERION"


# ── the diff, end to end ───────────────────────────────────────────────────

def _run(monkeypatch, sheet_rows):
    """Run main() against a stubbed sheet, capturing what it would write."""
    monkeypatch.setattr(sync, "_fetch_sheet_rows", lambda: sheet_rows)
    written = {}
    monkeypatch.setattr(type(sync.COMPANIES_PATH), "write_text",
                        lambda self, text, **kw: written.setdefault("text", text))
    monkeypatch.setattr(sync, "_sheet_id", lambda: "sheet-id")
    sync.main()
    return written.get("text")


def _row(name, url, city=""):
    st, slug = sync._detect_source_type(url)
    row = {"name": name, "city": city, "country": "Germany",
           "career_url": url, "source_type": st}
    if slug:
        row["slug"] = slug
    return row


def test_an_excluded_association_is_never_added(monkeypatch):
    import yaml

    text = _run(monkeypatch, [
        _row("Pharma Deutschland",
             "https://www.pharmadeutschland.de/der-verband/stellenangebote/", "Bonn"),
    ])
    names = [] if text is None else [c["name"] for c in yaml.safe_load(text)["companies"]]
    assert "Pharma Deutschland" not in names


def test_an_nrw_major_is_never_added_to_the_watchlist(monkeypatch):
    import yaml

    text = _run(monkeypatch, [
        _row("Viatris", "https://viatris.wd5.myworkdayjobs.com/de-DE/External", "Troisdorf"),
    ])
    names = [] if text is None else [c["name"] for c in yaml.safe_load(text)["companies"]]
    assert "Viatris" not in names


def test_a_renamed_company_is_matched_not_duplicated(monkeypatch):
    import yaml

    text = _run(monkeypatch, [
        _row("Togontech GmbH", "https://www.togontech.de/careers", "Köln"),
    ])
    if text is None:
        return   # no change at all is also correct
    names = [c["name"] for c in yaml.safe_load(text)["companies"]]
    assert "Togontech GmbH" not in names
    assert names.count("Togontech") == 1


def test_the_sheet_homepage_does_not_overwrite_a_researched_career_url(monkeypatch):
    import yaml

    text = _run(monkeypatch, [_row("PB Pharma", "https://www.pbpharma.de", "Meerbusch")])
    if text is None:
        return
    entry = [c for c in yaml.safe_load(text)["companies"] if c["name"] == "PB Pharma"][0]
    assert entry["career_url"] == "https://www.pbpharma.de/karriere/"


def test_notes_and_aliases_survive_a_sync(monkeypatch):
    import yaml

    text = _run(monkeypatch, [_row("Brand New GmbH", "https://brandnew.de/karriere")])
    assert text is not None
    back = yaml.safe_load(text)["companies"]
    assert sum(1 for c in back if c.get("notes")) == 15
    assert sum(1 for c in back if c.get("aliases")) == 5


def test_is_less_specific_uses_the_path_prefix_not_only_career_keywords():
    # Neither path contains a career keyword, so only the prefix rule can tell
    # that the sheet's URL is the weaker of the two.
    assert is_less_specific("https://acme.de/en", "https://acme.de/en/opportunities")
    assert not is_less_specific("https://acme.de/en/opportunities", "https://acme.de/en")


def test_an_alias_only_spelling_does_not_add_a_second_entry(monkeypatch):
    import yaml

    # "Cannamedical Pharma" does not normalise to "Cannamedical" — it is matched
    # only through the aliases: list on that entry.
    text = _run(monkeypatch, [
        _row("Cannamedical Pharma", "https://cannamedical.com/karriere/", "Meerbusch"),
    ])
    if text is None:
        return
    names = [c["name"] for c in yaml.safe_load(text)["companies"]]
    assert "Cannamedical Pharma" not in names
    assert names.count("Cannamedical") == 1


def test_a_tracking_laden_sheet_url_is_not_treated_as_a_change(monkeypatch):
    import yaml

    text = _run(monkeypatch, [
        _row("Cellavent Healthcare",
             "https://cellavent.de/pages/karriere?_su_rec=abc&_su_rec_id=xyz",
             "Düsseldorf"),
    ])
    if text is None:
        return   # no change detected at all is the desired outcome
    entry = [c for c in yaml.safe_load(text)["companies"]
             if c["name"] == "Cellavent Healthcare"][0]
    assert "_su_rec" not in entry["career_url"]


def test_a_new_company_is_stored_without_tracking_params(monkeypatch):
    import yaml

    text = _run(monkeypatch, [
        _row("Brand New GmbH",
             "https://brandnew.de/karriere?gclid=XYZ&utm_source=google"),
    ])
    assert text is not None
    entry = [c for c in yaml.safe_load(text)["companies"]
             if c["name"] == "Brand New GmbH"][0]
    assert entry["career_url"] == "https://brandnew.de/karriere"


def test_normalise_name_is_case_insensitive():
    # A CELLEX-vs-Cellex sheet/YAML mismatch must not add a duplicate.
    assert sync._normalise_name("CELLEX GmbH") == sync._normalise_name("Cellex")
    assert sync._normalise_name("KLOSTERFRAU GROUP") == sync._normalise_name("Klosterfrau Group")


def test_normalise_name_strips_the_group_and_hr_tokens():
    assert sync._normalise_name("Klosterfrau Group") == sync._normalise_name("Klosterfrau")
    assert sync._normalise_name("Singleron Biotechnologies HR") == \
        sync._normalise_name("Singleron Biotechnologies")


def test_geographic_words_are_kept_so_block_keys_stay_specific():
    # "Pharma Deutschland" must block as itself, not as the bare token "pharma".
    assert sync._normalise_name("Pharma Deutschland") == "pharma deutschland"
    blocked = sync._blocked_names()
    assert "pharma" not in blocked
    assert sync._normalise_name("Pharma Deutschland") in blocked


def test_detect_source_type_routes_each_known_ats():
    assert sync._detect_source_type("https://apply.workable.com/allucent/") == ("workable", "allucent")
    assert sync._detect_source_type("https://cellexgmbh.recruitee.com") == ("recruitee", "cellexgmbh")
    assert sync._detect_source_type("https://prosion-gmbh.jobs.personio.de") == ("personio", "prosion-gmbh")
    assert sync._detect_source_type("https://join.com/companies/enua") == ("join", "enua")
    assert sync._detect_source_type("https://acme.de/karriere") == ("html", None)
    assert sync._detect_source_type("") == ("skip", None)


def test_names_that_normalise_to_nothing_do_not_collide(monkeypatch):
    import yaml

    # Two malformed sheet rows made only of legal forms both normalise to "".
    # They must not be treated as the same company (which would drop one) or
    # block anything.
    text = _run(monkeypatch, [
        _row("GmbH", "https://one.example/karriere"),
        _row("AG", "https://two.example/karriere"),
    ])
    assert text is not None
    names = [c["name"] for c in yaml.safe_load(text)["companies"]]
    assert "GmbH" in names and "AG" in names


def test_an_empty_normal_is_never_a_block_key():
    assert "" not in sync._blocked_names()

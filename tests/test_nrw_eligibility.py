"""Unit tests for NRW major-employer eligibility rules."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scraper.nrw_eligibility import (  # noqa: E402
    is_excluded_nrw_major_entry_level_title,
    job_eligible_nrw_major,
    job_text_eligible,
    location_in_nrw,
    smartrecruiters_posting_eligible,
    text_suggests_us_only_remote,
    ucb_detail_eligible,
)


def test_excluded_intern_praktikum_titles():
    assert is_excluded_nrw_major_entry_level_title("Summer Internship (m/f/d)")
    assert is_excluded_nrw_major_entry_level_title("Pharmaziepraktikant in Berlin")
    assert is_excluded_nrw_major_entry_level_title("Praktikum im Bereich QA")
    assert is_excluded_nrw_major_entry_level_title("Software Engineer Intern")
    assert is_excluded_nrw_major_entry_level_title("Intern (m/w/d) Clinical Development")
    assert not is_excluded_nrw_major_entry_level_title("Internal Audit Manager")
    assert not is_excluded_nrw_major_entry_level_title("International Key Account Manager")
    assert not is_excluded_nrw_major_entry_level_title("VP International Markets")
    assert not is_excluded_nrw_major_entry_level_title("Internationale Projekte — Lead")
    assert not is_excluded_nrw_major_entry_level_title("Senior Scientist Oncology")


def test_location_in_nrw():
    assert location_in_nrw("Köln, Germany")
    assert location_in_nrw("Aachen")
    assert location_in_nrw("Ruhrgebiet")
    assert not location_in_nrw("Berlin, Germany")


def test_us_only_remote():
    assert text_suggests_us_only_remote("United States only remote role")
    assert not text_suggests_us_only_remote("Remote Germany")


def test_job_text_hybrid_nrw():
    assert job_text_eligible(
        "Leverkusen, NRW",
        "We offer a hybrid model with 2 days home office.",
    )
    assert not job_text_eligible(
        "Berlin",
        "Hybrid role based in our Berlin office.",
    )


def test_job_text_remote_eu():
    assert job_text_eligible(
        "Remote, Germany",
        "Fully remote position open to candidates in the EU.",
    )
    assert not job_text_eligible(
        "Remote",
        "United States only. Must reside in one of the 50 states.",
    )


def test_smartrecruiters_hybrid_koeln():
    posting = {
        "name": "Scientist",
        "location": {
            "city": "Köln",
            "country": "de",
            "fullLocation": "Köln, Germany",
            "remote": False,
            "hybrid": True,
        },
        "jobAd": {},
    }
    assert smartrecruiters_posting_eligible(posting)


def test_smartrecruiters_remote_de():
    posting = {
        "name": "Engineer",
        "location": {
            "city": "",
            "country": "de",
            "fullLocation": "Germany",
            "remote": True,
            "hybrid": False,
        },
        "jobAd": {"jobDescription": "Work from anywhere in Germany."},
    }
    assert smartrecruiters_posting_eligible(posting)


def test_smartrecruiters_onsite_bergisch_gladbach_not_hybrid():
    """Miltenyi-style: on-site lab role, API does not set hybrid/remote."""
    posting = {
        "name": "Production Associate",
        "location": {
            "city": "Bergisch Gladbach",
            "country": "de",
            "fullLocation": "Bergisch Gladbach, , Germany",
            "remote": False,
            "hybrid": False,
        },
        "jobAd": {},
    }
    assert smartrecruiters_posting_eligible(posting)


def test_smartrecruiters_teterow_germany_not_nrw_excluded():
    posting = {
        "name": "Role",
        "location": {
            "city": "Teterow",
            "country": "de",
            "fullLocation": "Teterow, , Germany",
            "remote": False,
            "hybrid": False,
        },
        "jobAd": {},
    }
    assert not smartrecruiters_posting_eligible(posting)


def test_job_text_onsite_leverkusen_no_hybrid_keyword():
    assert job_text_eligible(
        "Leverkusen, Germany",
        "Full-time on-site role at our chemical site. No home office.",
    )


def test_listing_nrw_scoped_trusts_listing():
    assert job_eligible_nrw_major("", "On-site role, minimal text.", listing_nrw_scoped=True)
    assert not job_eligible_nrw_major(
        "",
        "United States only. Remote.",
        listing_nrw_scoped=True,
    )


def test_ucb_monheim_keyword():
    assert ucb_detail_eligible(
        "Join our team in Monheim am Rhein.",
        ["Monheim", "Mettmann"],
    )
    assert not ucb_detail_eligible(
        "Based in Brussels, Belgium.",
        ["Monheim", "Mettmann"],
    )

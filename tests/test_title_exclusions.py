"""Unit tests for shared title exclusion helpers."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scraper.title_exclusions import (  # noqa: E402
    is_excluded_job_title,
    title_matches_exclude_keyword,
)


def test_head_of_senior_roles_excluded():
    assert title_matches_exclude_keyword("Head of Regulatory Affairs", "Head of")
    assert title_matches_exclude_keyword("Associate Head of Clinical Ops", "Head of")


def test_functional_head_of_not_excluded():
    assert not title_matches_exclude_keyword(
        "Functional head of quality control [§12 AMWHV] (m|f|d)",
        "Head of",
    )


def test_intern_rules():
    assert is_excluded_job_title("Software Engineer Intern", include_intern_rules=True)[0]
    assert not is_excluded_job_title("International Key Account Manager", include_intern_rules=True)[0]
    assert is_excluded_job_title("Ausbildung zur BTA", ["Ausbildung"])[0]
    assert is_excluded_job_title("Werkstudentin Labor", ["Werkstudent"])[0]

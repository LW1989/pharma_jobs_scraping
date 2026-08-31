"""
The delisting contract, which most of the other fixes exist to protect.

Both runners mark every active job for an employer inactive when it is absent
from the set the fetch returned. So an empty or short result set deletes real
listings, and a failed fetch must never look like an empty board.

These tests drive the runners' main() with the DB and the fetchers stubbed.
"""

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

for _var in ("DB_HOST", "DB_NAME", "DB_USER", "DB_PASSWORD"):
    os.environ.setdefault(_var, "test")

from scraper import db  # noqa: E402


# ── the helper itself ──────────────────────────────────────────────────────

def test_delists_exactly_what_was_not_seen(monkeypatch):
    monkeypatch.setattr(db, "get_active_job_ids_for_employer",
                        lambda source, employer: {"a", "b", "c"})
    gone = []
    monkeypatch.setattr(db, "mark_jobs_inactive", lambda ids: gone.append(set(ids)))

    n = db.deactivate_delisted_for_employer("company_direct", "Testco", {"a", "c"})

    assert n == 1 and gone == [{"b"}]


def test_seeing_everything_delists_nothing(monkeypatch):
    monkeypatch.setattr(db, "get_active_job_ids_for_employer",
                        lambda source, employer: {"a", "b"})
    called = []
    monkeypatch.setattr(db, "mark_jobs_inactive", lambda ids: called.append(ids))

    assert db.deactivate_delisted_for_employer("company_direct", "Testco", {"a", "b"}) == 0
    assert called == []


def test_seeing_nothing_delists_everything(monkeypatch):
    # The behaviour that makes a failed fetch dangerous — pinned so the runners'
    # exception paths below are known to matter.
    monkeypatch.setattr(db, "get_active_job_ids_for_employer",
                        lambda source, employer: {"a", "b"})
    gone = []
    monkeypatch.setattr(db, "mark_jobs_inactive", lambda ids: gone.append(set(ids)))

    assert db.deactivate_delisted_for_employer("company_direct", "Testco", set()) == 2
    assert gone == [{"a", "b"}]


# ── the runners ────────────────────────────────────────────────────────────

class _Recorder:
    """Captures what a runner would do to the DB."""

    def __init__(self):
        self.delisted_with = []
        self.marked_active = []
        self.inserted = []

    def install(self, monkeypatch, module, active_ids):
        monkeypatch.setattr(module, "insert_job", lambda job: self.inserted.append(job))
        monkeypatch.setattr(module, "mark_jobs_active",
                            lambda ids: self.marked_active.append(set(ids)))
        monkeypatch.setattr(
            module, "deactivate_delisted_for_employer",
            lambda source, name, seen: (
                self.delisted_with.append((name, set(seen))) or 0
            ),
        )
        monkeypatch.setattr(module.time, "sleep", lambda _s: None)
        monkeypatch.setattr(module, "_deactivate_stale_company_jobs", lambda: 0,
                            raising=False)
        monkeypatch.setattr(module, "_deactivate_stale", lambda: 0, raising=False)
        monkeypatch.setattr(module, "get_cursor", None, raising=False)


def _one_company(monkeypatch, module, loader, entries):
    monkeypatch.setattr(module, loader, lambda: entries, raising=False)


def test_company_checker_does_not_delist_when_the_fetch_raises(monkeypatch):
    import run_company_checker as rcc

    rec = _Recorder()
    rec.install(monkeypatch, rcc, set())
    monkeypatch.setattr(rcc, "_load_companies", lambda: [
        {"name": "Testco", "source_type": "html", "career_url": "https://t.example/k"}
    ])
    monkeypatch.setattr(rcc, "_get_all_company_job_ids", lambda: {"a", "b"})

    def boom(company):
        raise ConnectionError("connect timeout")

    monkeypatch.setattr(rcc, "fetch_jobs", boom)

    rcc.main()

    assert rec.delisted_with == [], "a failed fetch must not reach the delister"
    assert rec.inserted == []


def test_company_checker_delists_only_on_a_successful_empty_fetch(monkeypatch):
    import run_company_checker as rcc

    rec = _Recorder()
    rec.install(monkeypatch, rcc, set())
    monkeypatch.setattr(rcc, "_load_companies", lambda: [
        {"name": "Testco", "source_type": "html", "career_url": "https://t.example/k"}
    ])
    monkeypatch.setattr(rcc, "_get_all_company_job_ids", lambda: {"a", "b"})
    monkeypatch.setattr(rcc, "fetch_jobs", lambda company: [])

    rcc.main()

    assert rec.delisted_with == [("Testco", set())]


def test_company_checker_passes_through_the_ids_it_saw(monkeypatch):
    import run_company_checker as rcc

    rec = _Recorder()
    rec.install(monkeypatch, rcc, set())
    monkeypatch.setattr(rcc, "_load_companies", lambda: [
        {"name": "Testco", "source_type": "html", "career_url": "https://t.example/k"}
    ])
    monkeypatch.setattr(rcc, "_get_all_company_job_ids", lambda: {"a"})
    monkeypatch.setattr(rcc, "fetch_jobs", lambda company: [
        {"job_id": "a", "title": "Kept"}, {"job_id": "z", "title": "New"},
    ])

    rcc.main()

    assert rec.delisted_with == [("Testco", {"a", "z"})]
    assert [j["job_id"] for j in rec.inserted] == ["z"]
    assert rec.marked_active == [{"a", "z"}]


def test_nrw_checker_does_not_delist_when_the_fetch_raises(monkeypatch):
    import run_nrw_major_checker as rnm

    rec = _Recorder()
    rec.install(monkeypatch, rnm, set())
    monkeypatch.setattr(rnm, "_load_employers", lambda: [
        {"name": "Viatris", "source_type": "workday_cxs"}
    ], raising=False)
    monkeypatch.setattr(rnm, "_db_ids_nrw_major", lambda: {"a", "b"})

    def boom(row):
        raise ConnectionError("503")

    monkeypatch.setattr(rnm, "fetch_jobs_for_employer", boom)

    rnm.main()

    assert rec.delisted_with == [], "a failed NRW fetch must not reach the delister"


def test_nrw_checker_passes_through_the_ids_it_saw(monkeypatch):
    import run_nrw_major_checker as rnm

    rec = _Recorder()
    rec.install(monkeypatch, rnm, set())
    monkeypatch.setattr(rnm, "_load_employers", lambda: [
        {"name": "Viatris", "source_type": "workday_cxs"}
    ], raising=False)
    monkeypatch.setattr(rnm, "_db_ids_nrw_major", lambda: {"a"})
    monkeypatch.setattr(rnm, "fetch_jobs_for_employer", lambda row: [
        {"job_id": "a", "title": "QA Manager"},
        {"job_id": "z", "title": "Regulatory Affairs Manager"},
    ])

    rnm.main()

    assert rec.delisted_with == [("Viatris", {"a", "z"})]
    assert [j["job_id"] for j in rec.inserted] == ["z"]

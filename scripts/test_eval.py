"""
Smoke test for the evaluation module.

Tests:
  1. Module imports
  2. requirements.yaml + CV loading (cv_matching.txt or fallback)
  3. Prescreener: verify tier_3_exclude rejects QA/Reg/MSL/Sales titles
  4. Prescreener: find 5 jobs that pass all filters
  5. LLM evaluation with new signature (tier_1_threshold + preferences)
  6. Summary

Usage:
    python scripts/test_eval.py
"""
import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent

sys.path.insert(0, str(ROOT))

import yaml

# ── 1. Imports ────────────────────────────────────────────────────────────────
print("[1/6] Importing modules...", flush=True)
from scraper.db import get_cursor
from scraper import config
from evaluator.prescreener import prescreen
from evaluator.llm_client import evaluate
print("      OK", flush=True)

# ── 2. Load requirements + CV ─────────────────────────────────────────────────
print("[2/6] Loading requirements.yaml and CV...", flush=True)
with open(ROOT / "requirements.yaml") as f:
    req = yaml.safe_load(f)

filters = req.get("filters", {})
filters["job_preferences"] = req.get("job_preferences", {})   # merge for prescreener

scoring          = req.get("scoring", {})
threshold        = int(scoring.get("should_apply_min_score", 65))
tier_1_threshold = int(scoring.get("tier_1_min_score", 55))
preferences      = req.get("job_preferences", {})
model            = scoring.get("model", "gpt-5-mini")

# Auto-synthesize cv_matching.txt if missing or out of date
import synthesize_cv as _syn
_synthesis_cfg = req.get("cv_synthesis", {})
_source_dir    = ROOT / _synthesis_cfg.get("source_directory", "input_data/cv")
_output_file   = ROOT / _synthesis_cfg.get("output_file", "cv_matching.txt")
if _source_dir.exists() and list(_source_dir.glob("*.txt")):
    _cv_list = _syn._load_source_cvs(_source_dir)
    if not _syn._is_up_to_date(_syn._combined_hash(_cv_list)) or not _output_file.exists():
        print("      cv_matching.txt missing/outdated — running synthesis...", flush=True)
        _syn.main()
    else:
        print("      cv_matching.txt is up to date", flush=True)

# Resolve CV file (same priority order as run_evaluator.py)
configured_cv = scoring.get("cv_file", "")
cv_text = None
cv_file_used = None
for candidate in [ROOT / configured_cv, ROOT / "cv_matching.txt", ROOT / "cv.txt"]:
    if candidate.exists():
        text = candidate.read_text(encoding="utf-8").strip()
        if text and not text.startswith("CV files have been moved"):
            cv_text = text
            cv_file_used = candidate.name
            break

if not cv_text:
    print("ERROR: No usable CV file found. Add .txt files to input_data/cv/ and retry.")
    sys.exit(1)

cv_version = hashlib.md5(cv_text.encode()).hexdigest()
print(f"      CV file:   {cv_file_used}  (hash: {cv_version[:8]}...)", flush=True)
print(f"      Model:     {model}", flush=True)
print(f"      Threshold: score >= {threshold} (tier-1: >= {tier_1_threshold})", flush=True)

tier_1_roles = preferences.get("tier_1_definitely", [])
tier_2_roles = preferences.get("tier_2_would_work", [])
tier_3_entries = preferences.get("tier_3_exclude", [])
print(f"      Tier-1 roles:   {len(tier_1_roles)} defined", flush=True)
print(f"      Tier-2 roles:   {len(tier_2_roles)} defined", flush=True)
print(f"      Tier-3 entries: {len(tier_3_entries)} defined", flush=True)

# ── 3. Tier_3 pre-screen verification ─────────────────────────────────────────
print("\n[3/6] Verifying tier-3 exclusions work...", flush=True)
tier_3_test_cases = [
    {"job_id": "T1", "title": "Quality Assurance Specialist", "contract_type": "Permanent",
     "hours": "Full Time", "location": "UK, Homeworking", "experience_level": "Experienced (non-manager)"},
    {"job_id": "T2", "title": "Regulatory Affairs Manager", "contract_type": "Permanent",
     "hours": "Full Time", "location": "Germany, Homeworking", "experience_level": "Management"},
    {"job_id": "T3", "title": "Medical Science Liaison - CNS", "contract_type": "Permanent",
     "hours": "Full Time", "location": "Homeworking", "experience_level": "Experienced (non-manager)"},
    {"job_id": "T4", "title": "Key Account Manager - Oncology", "contract_type": "Permanent",
     "hours": "Full Time", "location": "Homeworking", "experience_level": "Experienced (non-manager)"},
    {"job_id": "T5", "title": "Clinical Trial Manager", "contract_type": "Permanent",
     "hours": "Full Time", "location": "UK, Homeworking", "experience_level": "Experienced (non-manager)"},
]
all_tier3_ok = True
for job in tier_3_test_cases:
    passed, reason = prescreen(job, filters)
    expected_pass = job["job_id"] == "T5"  # Only CTM should pass
    status = "OK" if passed == expected_pass else "FAIL"
    if status == "FAIL":
        all_tier3_ok = False
    verdict = "PASS" if passed else "SKIP"
    print(f"      [{status}] {verdict}: {job['title']}  | {reason.replace('Pre-screened out: ', '')}", flush=True)

if not all_tier3_ok:
    print("\nERROR: Tier-3 exclusion test failed — check requirements.yaml and prescreener.py")
    sys.exit(1)
print("      Tier-3 exclusions: all checks passed", flush=True)

# ── 4. Find 5 real jobs that pass pre-screening ───────────────────────────────
print("\n[4/6] Fetching 500 random active jobs, pre-screening for 5 passing...", flush=True)
with get_cursor() as cur:
    cur.execute("""
        SELECT job_id, title, employer, location, salary,
               start_date, closing_date, discipline, hours,
               contract_type, experience_level, job_details, url
        FROM jobs WHERE job_active = TRUE
        ORDER BY RANDOM() LIMIT 500
    """)
    candidates = [dict(r) for r in cur.fetchall()]
print(f"      Fetched {len(candidates)} candidates from DB", flush=True)

passing = []
tier3_blocked = 0
other_blocked = 0
screened = 0
for job in candidates:
    screened += 1
    ok, reason = prescreen(job, filters)
    if ok:
        passing.append(job)
        print(f"      PASS [{len(passing)}/5]: {job['job_id']} — {job['title'][:55]} ({job['location'][:30]})", flush=True)
    else:
        if "tier-3" in reason:
            tier3_blocked += 1
        else:
            other_blocked += 1
    if len(passing) == 5:
        break

print(f"\n      Screened {screened} jobs:", flush=True)
print(f"        Passed:          {len(passing)}", flush=True)
print(f"        Tier-3 blocked:  {tier3_blocked}", flush=True)
print(f"        Other blocked:   {other_blocked}", flush=True)

if not passing:
    print("ERROR: No jobs passed pre-screening. Adjust requirements.yaml filters.")
    sys.exit(1)

# ── 5. LLM evaluation with new signature ─────────────────────────────────────
print(f"\n[5/6] Sending {len(passing)} jobs to {model} for evaluation...", flush=True)
print(f"      (using tier_1_threshold={tier_1_threshold}, tier_2_threshold={threshold})", flush=True)
total_in, total_out = 0, 0
results = []
for i, job in enumerate(passing, 1):
    print(f"\n  [{i}/{len(passing)}] Evaluating job {job['job_id']} — {job['title'][:60]}...", flush=True)
    result = evaluate(
        job, cv_text, model, threshold,
        tier_1_threshold=tier_1_threshold,
        preferences=preferences,
    )
    total_in  += result.tokens_input
    total_out += result.tokens_output
    results.append((job, result))
    flag = ">>> APPLY <<<" if result.should_apply else "skip"
    print(f"      Score:     {result.score}/100  [{flag}]", flush=True)
    print(f"      Reasoning: {result.score_reasoning}", flush=True)
    print(f"      Tokens:    {result.tokens_input} in / {result.tokens_output} out", flush=True)

# ── 6. Summary ────────────────────────────────────────────────────────────────
print(f"\n[6/6] Summary", flush=True)
pricing = config.OPENAI_PRICING.get(model, {"input": 0.25, "output": 2.00})
cost = (total_in * pricing["input"] + total_out * pricing["output"]) / 1_000_000
print(f"      Total tokens:   {total_in} in / {total_out} out")
print(f"      Estimated cost: ${cost:.5f}")
print(f"\n      Scores (sorted):")
for job, result in sorted(results, key=lambda x: x[1].score, reverse=True):
    flag = "APPLY" if result.should_apply else "skip"
    print(f"        {result.score:3d}/100  [{flag}]  {job['title'][:50]} @ {job['employer']}")
print("\nSmoke test complete — all checks passed.")

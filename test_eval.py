"""Quick test: evaluate 5 random jobs that pass pre-screening."""
import hashlib
import sys
import yaml

print("[1/6] Importing modules...", flush=True)
from scraper.db import get_cursor
from scraper import config
from evaluator.prescreener import prescreen
from evaluator.llm_client import evaluate
print("      OK", flush=True)

print("[2/6] Loading cv.txt and requirements.yaml...", flush=True)
cv_text = open("cv.txt").read().strip()
cv_version = hashlib.md5(cv_text.encode()).hexdigest()
with open("requirements.yaml") as f:
    req = yaml.safe_load(f)
filters = req["filters"]
scoring = req["scoring"]
threshold = int(scoring.get("should_apply_min_score", 70))
model = scoring.get("model", "gpt-5-mini")
print(f"      Model: {model} | threshold: {threshold} | cv_version: {cv_version[:8]}...", flush=True)

print("[3/6] Fetching 200 random active jobs from DB...", flush=True)
with get_cursor() as cur:
    cur.execute("""
        SELECT job_id, title, employer, location, salary,
               start_date, closing_date, discipline, hours,
               contract_type, experience_level, job_details, url
        FROM jobs WHERE job_active = TRUE
        ORDER BY RANDOM() LIMIT 200
    """)
    candidates = [dict(r) for r in cur.fetchall()]
print(f"      Got {len(candidates)} candidates", flush=True)

print("[4/6] Pre-screening to find 5 passing jobs...", flush=True)
passing = []
screened = 0
for job in candidates:
    screened += 1
    ok, reason = prescreen(job, filters)
    if ok:
        passing.append(job)
        print(f"      PASS [{len(passing)}/5]: {job['job_id']} — {job['title']} ({job['location']})", flush=True)
    if len(passing) == 5:
        break
print(f"      Screened {screened} jobs, {len(passing)} passed", flush=True)

if not passing:
    print("ERROR: No jobs passed pre-screening. Adjust requirements.yaml filters.")
    sys.exit(1)

print(f"\n[5/6] Sending {len(passing)} jobs to {model} for evaluation...", flush=True)
total_in, total_out = 0, 0
results = []
for i, job in enumerate(passing, 1):
    print(f"\n  [{i}/{len(passing)}] Evaluating job {job['job_id']} — {job['title']}...", flush=True)
    result = evaluate(job, cv_text, model, threshold)
    total_in += result.tokens_input
    total_out += result.tokens_output
    results.append((job, result))
    flag = ">>> APPLY <<<" if result.should_apply else "skip"
    print(f"      Score:     {result.score}/100  [{flag}]", flush=True)
    print(f"      Reasoning: {result.score_reasoning}", flush=True)
    print(f"      Tokens:    {result.tokens_input} in / {result.tokens_output} out", flush=True)

print(f"\n[6/6] Summary", flush=True)
pricing = config.OPENAI_PRICING.get(model, {"input": 0.25, "output": 2.00})
cost = (total_in * pricing["input"] + total_out * pricing["output"]) / 1_000_000
print(f"      Total tokens : {total_in} in / {total_out} out")
print(f"      Estimated cost : ${cost:.5f}")
print(f"\n      Scores overview:")
for job, result in sorted(results, key=lambda x: x[1].score, reverse=True):
    flag = "APPLY" if result.should_apply else "skip"
    print(f"        {result.score:3d}/100  [{flag}]  {job['title']} @ {job['employer']}")
print("\nTest complete.")

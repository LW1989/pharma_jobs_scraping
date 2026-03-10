# requirements.yaml — Field Value Reference

Queried from the live database (966 jobs, scraped March 2026).
Use this as a cheat sheet when editing `requirements.yaml`.

---

## experience_levels

All 6 distinct values in the DB, with job counts:

| Value (exact, copy-paste) | Count | What it means |
|---|---|---|
| `Experienced (non-manager)` | 882 | Individual contributor with experience — the bulk of all jobs |
| `Management` | 61 | People manager / team lead roles |
| `Senior Management` | 10 | Head of function, senior programme lead |
| `Director/Executive` | 1 | C-suite or VP level |
| `Graduate` | 6 | Entry-level graduate schemes |
| `Entry level` | 6 | Junior / first-job roles |

**Tip:** The default config includes `Experienced (non-manager)`, `Management`, and `Senior Management`. Remove `Senior Management` if you find those roles too senior. Add `Graduate` / `Entry level` only if you want to see trainee-level roles.

---

## contract_types

| Value | Count |
|---|---|
| `Permanent` | 869 |
| `Contract` | 97 |

---

## hours

| Value | Count |
|---|---|
| `Full Time` | 964 |
| `Part Time` | 2 |

Essentially everything is Full Time — this filter rarely matters but is harmless to keep.

---

## location_keywords

406 of 966 jobs (42%) are remote/hybrid/home. The rest are on-site only.

The keywords that cover the remote/hybrid jobs:

| Keyword | Notes |
|---|---|
| `Homeworking` | The most common remote tag on this site |
| `Remote` | Used occasionally |
| `Hybrid` | Used occasionally |
| `Home` | Catches "Homeworking" and bare "Home" entries |

**Leave this list empty (`[]`) if you're open to relocating / on-site roles** — that gives you 966 jobs instead of 406.

---

## exclude_title_keywords

### ⚠️ Watch out: "intern" matches "International"

The word `intern` appears in 7 titles — but ALL of them are false positives:

```
Associate Director, Insights & Analytics International (Rare Diseases)
Associate Director, International Patient Safety - Poland Hub
International Account Manager - ...
Internist - Mlawa                  ← Medical doctor, not an intern
Senior Clinical Trial Manager - Internal Medicine
Senior Manager, International Patient Safety
Senior Specialist, International Patient Safety
```

**Do not use `Intern` as an exclude keyword.** Use `Internship` instead (0 matches currently) or nothing at all.

### Seniority keyword counts in titles

| Keyword | Matches | Safe to exclude? |
|---|---|---|
| `Graduate` | 11 | Yes — only catches "Graduate Pharmacovigilance Associate" (entry-level) |
| `Executive Director` | 2 | Yes — clearly too senior |
| `Senior Director` | 7 | Yes — too senior in most cases |
| `Medical Director` | 4 | Depends — medical/clinical leadership, not a functional role |
| `Head of` | 10 | Yes — all are "Head of [function]" senior leadership |
| `Chief` | 2 | Yes |
| `Vice President` | 1 | Yes |
| `VP` | 1 | Yes |
| `Trainee` | 1 | Yes — entry level |
| `Junior` | 3 | Probably yes |
| `Director` | 45 | **No — this also removes Associate Director roles** |

### Director roles breakdown — include or exclude?

45 jobs contain "director" in the title. They fall into:

| Pattern | Count | Recommendation |
|---|---|---|
| `Associate Director` | 19 | Often relevant — Senior IC or first-line manager level |
| `Senior Director` | 7 | Likely too senior |
| `Executive Director` | 2 | Too senior |
| `Medical Director` | 4 | Functional leadership (skip unless you have MD) |
| `Country/Research/Site Director` | 5 | Senior, assess case-by-case |
| `Director,` (standalone) | 8 | Senior, often too senior |

**Recommendation:** Exclude `Senior Director`, `Executive Director`, `Head of`, `Medical Director`, `Chief`, `Vice President`, `VP`, `Graduate`, `Trainee`, `Junior` — but **keep** `Associate Director` and bare `Director`.

---

## Top disciplines (for context)

The most common job categories in the DB:

| Count | Discipline |
|---|---|
| 171 | Clinical Research — CRA |
| 59 | Clinical Research (general) |
| 52 | Sales / Commercial |
| 39 | Account Management |
| 33 | Regulatory Affairs |
| 29 | Clinical Trials Manager / Administrator |
| 26 | Pharmacovigilance |
| 21 | Manufacturing / Engineering |
| 19 | Contracts / Proposals |
| 19 | Project Management |
| 18 | Clinical Operations |
| 17 | QA / QC |
| 17 | Clinical Development |
| 16 | Drug Safety |
| 15 | Statistical Programming |
| 13 | Study Start Up |
| 13 | Clinical Data Management |
| 9 | Medical Affairs |
| 9 | Biostatistics |

The `discipline` column is **not currently used as a filter** in the pre-screener. If you want to restrict to specific disciplines, that would need a new filter added to `prescreener.py`.

---

## Suggested requirements.yaml based on the above

```yaml
filters:
  contract_types:
    - Permanent

  hours:
    - Full Time

  location_keywords:
    - Homeworking
    - Remote
    - Hybrid
    - Home

  experience_levels:
    - Experienced (non-manager)
    - Management
    - Senior Management          # remove if Senior Mgmt roles are too senior

  exclude_title_keywords:
    - Senior Director
    - Executive Director
    - Medical Director
    - Head of
    - Chief
    - Vice President
    - VP
    - Graduate
    - Junior
    - Trainee
    # Do NOT add "Director" — it removes Associate Director too
    # Do NOT add "Intern" — it matches "International"

scoring:
  should_apply_min_score: 70
  model: gpt-5-mini
```

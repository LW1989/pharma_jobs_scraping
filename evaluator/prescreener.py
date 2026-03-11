"""
Rule-based pre-screener.

Checks structured job columns against requirements.yaml filters.
No HTTP requests, no LLM calls — pure in-memory logic.

Public API:
    prescreen(job, filters) -> tuple[bool, str]
        Returns (passed, reason_string).
"""


def prescreen(job: dict, filters: dict) -> tuple[bool, str]:
    """
    Apply all configured filters to a job dict (as returned from the DB).

    Returns:
        (True,  "Passed pre-screening")          if all filters pass
        (False, "Pre-screened out: <reason>")    if any filter fails
    """
    # --- Contract type ---
    allowed_contract_types = filters.get("contract_types", [])
    if allowed_contract_types:
        value = (job.get("contract_type") or "").strip()
        if value not in allowed_contract_types:
            return False, (
                f"Pre-screened out: contract type '{value}' "
                f"not in {allowed_contract_types}"
            )

    # --- Hours ---
    allowed_hours = filters.get("hours", [])
    if allowed_hours:
        value = (job.get("hours") or "").strip()
        if value not in allowed_hours:
            return False, (
                f"Pre-screened out: hours '{value}' not in {allowed_hours}"
            )

    # --- Location (two-path: remote needs country, on-site/hybrid needs city) ---
    location_cfg = filters.get("location", {})
    if location_cfg:
        remote_kws = location_cfg.get("remote_keywords", [])
        countries  = location_cfg.get("allowed_countries", [])
        cities     = location_cfg.get("allowed_cities", [])
        loc        = (job.get("location") or "").lower()
        is_remote  = any(kw.lower() in loc for kw in remote_kws)
        if is_remote:
            # Strip remote keywords from the location; if nothing meaningful
            # remains (bare "Homeworking"), pass without a country check.
            loc_remainder = loc
            for kw in remote_kws:
                loc_remainder = loc_remainder.replace(kw.lower(), "")
            loc_remainder = loc_remainder.strip(" ,;/")
            if loc_remainder and not any(c.lower() in loc for c in countries):
                return False, (
                    f"Pre-screened out: remote job in non-allowed country "
                    f"(location: '{job.get('location')}')"
                )
        else:
            if not any(c.lower() in loc for c in cities):
                return False, (
                    f"Pre-screened out: on-site/hybrid job not in allowed city "
                    f"(location: '{job.get('location')}')"
                )

    # --- Experience level ---
    allowed_experience = filters.get("experience_levels", [])
    if allowed_experience:
        value = (job.get("experience_level") or "").strip()
        if value not in allowed_experience:
            return False, (
                f"Pre-screened out: experience level '{value}' "
                f"not in {allowed_experience}"
            )

    # --- Excluded title keywords ---
    exclude_keywords = filters.get("exclude_title_keywords", [])
    if exclude_keywords:
        title = (job.get("title") or "").lower()
        for kw in exclude_keywords:
            if kw.lower() in title:
                return False, (
                    f"Pre-screened out: title contains excluded keyword '{kw}'"
                )

    # --- Tier-3 preference exclusions ---
    # Reads tier_3_exclude from the job_preferences section of requirements.yaml.
    # Each entry has a name (for logging) and a list of title_keywords to match.
    # This runs BEFORE any LLM call — zero tokens spent on unwanted role types.
    tier_3_entries = (filters.get("job_preferences") or {}).get("tier_3_exclude", [])
    if tier_3_entries:
        title = (job.get("title") or "").lower()
        for entry in tier_3_entries:
            for kw in entry.get("title_keywords", []):
                if kw.lower() in title:
                    return False, (
                        f"Pre-screened out: tier-3 role '{entry['name']}' "
                        f"(keyword: '{kw}')"
                    )

    return True, "Passed pre-screening"

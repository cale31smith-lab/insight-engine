"""
column_mapping.py

Lets a user map arbitrary CSV column headers onto the Insight Engine's
fixed internal schema, without ever changing load.py / metrics.py / rules.py.

Flow:
  1. User uploads a CSV (e.g. their "jobs" export from Jobber).
  2. We show a dropdown per REQUIRED field, pre-filled with our best guess.
     The guess comes from Claude (semantic match against real column names
     and a few sample rows) when ANTHROPIC_API_KEY is available, falling
     back to fuzzy string matching otherwise -- either way it's ONLY ever
     a pre-filled suggestion.
  3. User confirms/corrects the mapping once. Nothing is ever applied
     without this human confirmation step, regardless of which suggestion
     method produced the default -- a wrong LLM guess costs one click to
     fix, not a wrong number in a client's report.
  4. We save it keyed by a "source name" they choose (e.g. "Jobber") so the
     next time they upload from the same software, the mapping auto-applies
     with zero clicks and zero further API calls.
  5. We rename their columns to match our schema and hand off a clean
     DataFrame — load.py never has to know the original headers existed.
"""

import json
import difflib
from pathlib import Path
import pandas as pd
import streamlit as st

MAPPING_STORE_PATH = Path("saved_column_mappings.json")

# Verified against the actual src/schema.py Pydantic models.
REQUIRED_SCHEMAS = {
    "technicians": ["tech_id", "name", "role", "loaded_hourly_cost"],
    "customers": ["customer_id", "segment"],
    "jobs": [
        "job_id", "date", "customer_id", "tech_id", "job_type",
        "revenue", "labor_hours", "labor_cost", "material_cost",
        "is_callback", "parent_job_id",
    ],
    "quotes": ["quote_id", "date", "customer_id", "tech_id", "job_type", "amount", "status"],
    "invoices": ["invoice_id", "job_id", "customer_id", "segment", "invoice_date", "paid_date", "amount"],
    "time_entries": ["tech_id", "week_start", "paid_hours", "billable_hours"],
}

# Fields where a missing value is valid (Optional in schema.py) — the mapping
# UI should let these be left unmapped instead of blocking on them.
OPTIONAL_FIELDS = {"parent_job_id", "paid_date"}


def load_saved_mappings() -> dict:
    """All previously-confirmed mappings, keyed by source name then entity."""
    if MAPPING_STORE_PATH.exists():
        return json.loads(MAPPING_STORE_PATH.read_text())
    return {}


def save_mapping(source_name: str, entity: str, mapping: dict) -> None:
    all_mappings = load_saved_mappings()
    all_mappings.setdefault(source_name, {})[entity] = mapping
    MAPPING_STORE_PATH.write_text(json.dumps(all_mappings, indent=2))


def _suggest_mapping_fuzzy(required_fields: list[str], uploaded_columns: list[str]) -> dict:
    """
    Fallback: best-guess mapping using fuzzy string matching on column
    names. Catches near-misses like 'job_type' vs 'JobType', but NOT
    semantic renames like 'job_amount' vs 'revenue' -- for those you
    need _suggest_mapping_llm below. Used automatically whenever the
    LLM path is unavailable or fails.
    """
    suggestions = {}
    remaining_columns = list(uploaded_columns)
    for field in required_fields:
        matches = difflib.get_close_matches(field, remaining_columns, n=1, cutoff=0.4)
        suggestions[field] = matches[0] if matches else None
    return suggestions


def _suggest_mapping_llm(
    entity: str, required_fields: list[str], uploaded_columns: list[str], sample_rows: list[dict]
) -> dict | None:
    """
    Ask Claude to semantically match uploaded_columns to required_fields,
    using a few real sample rows as context (e.g. this is what lets it
    catch 'job_amount' -> 'revenue', which fuzzy string matching can't).

    Returns None on ANY failure (no API key, network error, malformed
    response) so the caller falls back to fuzzy matching -- this must
    never crash the mapping UI, it's a nice-to-have speedup, not a
    dependency.

    Every value in the returned mapping is validated to be either None
    or an actual column from uploaded_columns -- a hallucinated column
    name that doesn't exist in the upload is discarded (set to None)
    rather than silently accepted. Same principle as narrate.py's
    validate_narration: never trust an LLM output that doesn't match
    the ground truth we already have.
    """
    try:
        from anthropic import Anthropic
    except ImportError:
        return None

    try:
        client = Anthropic()  # reads ANTHROPIC_API_KEY from env; raises if unset

        tool_schema = {
            "name": "suggest_column_mapping",
            "description": "Map each required field to the best-matching uploaded column name, or null if none fits.",
            "input_schema": {
                "type": "object",
                "properties": {
                    field: {
                        "type": ["string", "null"],
                        "description": f"The uploaded column name that corresponds to '{field}', or null if no column matches.",
                    }
                    for field in required_fields
                },
                "required": required_fields,
                "additionalProperties": False,
            },
        }

        prompt = (
            f"This is a '{entity}' data table for a home-services contractor business "
            f"(HVAC/electrical). The uploaded file has these columns:\n"
            f"{uploaded_columns}\n\n"
            f"Here are up to 3 sample rows for context:\n"
            f"{json.dumps(sample_rows, default=str, indent=2)}\n\n"
            f"Map each of these required fields to the uploaded column that means the same "
            f"thing (semantic match, not just similar spelling — e.g. 'job_amount' likely "
            f"means 'revenue'): {required_fields}\n\n"
            f"Only use column names that actually appear in the uploaded columns list above. "
            f"If nothing in the data matches a required field, use null for that field."
        )

        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1024,
            tools=[tool_schema],
            tool_choice={"type": "tool", "name": "suggest_column_mapping"},
            messages=[{"role": "user", "content": prompt}],
        )

        tool_use_blocks = [b for b in response.content if b.type == "tool_use"]
        if not tool_use_blocks:
            return None
        raw_mapping = tool_use_blocks[0].input

        # Validate: every value must be None or an actual uploaded column.
        # A field the LLM omitted, or a hallucinated column name, becomes
        # None rather than crashing the UI or silently mismapping.
        validated = {}
        for field in required_fields:
            value = raw_mapping.get(field)
            validated[field] = value if value in uploaded_columns else None
        return validated

    except Exception:
        # Any failure (missing key, network, rate limit, malformed response)
        # -- fall back to fuzzy matching rather than breaking the mapping UI.
        return None


def suggest_mapping(
    entity: str, required_fields: list[str], uploaded_columns: list[str], sample_rows: list[dict] | None = None
) -> dict:
    """
    Best-guess mapping, LLM-assisted when possible. Tries the semantic
    (LLM) suggestion first if sample_rows are provided; falls back to
    fuzzy string matching if the LLM path is unavailable or fails.
    Either way, this is only ever a starting point — render_mapping_ui
    still requires human confirmation before anything is used.
    """
    if sample_rows is not None:
        llm_suggestion = _suggest_mapping_llm(entity, required_fields, uploaded_columns, sample_rows)
        if llm_suggestion is not None:
            return llm_suggestion
    return _suggest_mapping_fuzzy(required_fields, uploaded_columns)


def apply_mapping(df: pd.DataFrame, mapping: dict) -> pd.DataFrame:
    """
    Rename df's columns from the source names to our canonical field names.
    A field mapped to None is only allowed if it's in OPTIONAL_FIELDS —
    that column is created and filled with None (e.g. a business with no
    concept of "parent job" for callbacks just gets parent_job_id = None
    for every row, which schema.py already treats as "no parent job").
    """
    rename_lookup = {source_col: canonical_field
                      for canonical_field, source_col in mapping.items()
                      if source_col is not None}
    required_missing = [f for f, v in mapping.items() if v is None and f not in OPTIONAL_FIELDS]
    if required_missing:
        raise ValueError(f"Mapping incomplete — no column chosen for: {required_missing}")

    result = df.rename(columns=rename_lookup)
    for field, source_col in mapping.items():
        if source_col is None:  # optional field with no matching column in this source
            result[field] = None
    return result[list(mapping.keys())]


def render_mapping_ui(entity: str, uploaded_df: pd.DataFrame, source_name: str) -> pd.DataFrame | None:
    """
    Renders the mapping dropdowns for one entity (e.g. "jobs") in Streamlit.
    Returns the remapped, ready-to-use DataFrame once the user confirms,
    or None while they're still working through it.
    """
    required_fields = REQUIRED_SCHEMAS[entity]
    uploaded_columns = list(uploaded_df.columns)
    saved = load_saved_mappings().get(source_name, {}).get(entity)

    st.markdown(f"**Map columns for: {entity}**")

    if saved and all(col in uploaded_columns for col in saved.values()):
        st.success(f"Using saved mapping for '{source_name}' — no action needed.")
        if st.button(f"Re-map {entity} instead", key=f"remap_{entity}"):
            saved = None
        else:
            return apply_mapping(uploaded_df, saved)

    # Cache the suggestion in session_state, keyed by source+entity+the
    # actual column set. Streamlit reruns this whole script on every
    # interaction (e.g. clicking a different entity's dropdown) -- without
    # caching, that would re-call the API on every single rerun instead of
    # once per upload.
    cache_key = f"_suggestion_cache_{source_name}_{entity}_{hash(tuple(uploaded_columns))}"
    if cache_key not in st.session_state:
        sample_rows = uploaded_df.head(3).to_dict(orient="records")
        st.session_state[cache_key] = suggest_mapping(entity, required_fields, uploaded_columns, sample_rows)
    suggestions = st.session_state[cache_key]

    mapping = {}
    for field in required_fields:
        default = suggestions.get(field)
        default_index = (uploaded_columns.index(default) + 1) if default in uploaded_columns else 0
        is_optional = field in OPTIONAL_FIELDS
        label = f"Which column is '{field}'?" + (" (optional)" if is_optional else "")
        no_match_label = "-- not in this data --" if is_optional else "-- select --"
        choice = st.selectbox(
            label,
            options=[no_match_label] + uploaded_columns,
            index=default_index,
            key=f"map_{entity}_{field}",
        )
        mapping[field] = None if choice == no_match_label else choice

    still_missing = [f for f, v in mapping.items() if v is None and f not in OPTIONAL_FIELDS]
    if still_missing:
        st.warning(f"Map every required field above to continue. Still missing: {', '.join(still_missing)}")
        return None

    if st.button(f"Confirm mapping for {entity}", key=f"confirm_{entity}"):
        save_mapping(source_name, entity, mapping)
        st.success(f"Mapping saved for '{source_name}' → {entity}. Future uploads skip this step.")
        return apply_mapping(uploaded_df, mapping)

    return None

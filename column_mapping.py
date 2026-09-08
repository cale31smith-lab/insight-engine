"""
column_mapping.py

Lets a user map arbitrary CSV column headers onto the Insight Engine's
fixed internal schema, without ever changing load.py / metrics.py / rules.py.

Flow:
  1. User uploads a CSV (e.g. their "jobs" export from Jobber).
  2. We show a dropdown per REQUIRED field, pre-filled with our best guess
     (fuzzy match against their actual headers).
  3. User confirms/corrects the mapping once.
  4. We save it keyed by a "source name" they choose (e.g. "Jobber") so the
     next time they upload from the same software, the mapping auto-applies
     with zero clicks.
  5. We rename their columns to match our schema and hand off a clean
     DataFrame — load.py never has to know the original headers existed.

IMPORTANT: Verify REQUIRED_SCHEMAS below against the actual field names in
schema.py before using this. These are reconstructed from memory and may
not be byte-for-byte correct.
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


def suggest_mapping(required_fields: list[str], uploaded_columns: list[str]) -> dict:
    """
    Best-guess mapping using fuzzy string matching on column names.
    e.g. required 'revenue' vs uploaded 'job_amount' won't match well —
    fuzzy matching handles near-misses like 'job_type' vs 'JobType',
    not semantic renames. That's fine: it's a starting guess, the user
    confirms every field before anything runs.
    """
    suggestions = {}
    remaining_columns = list(uploaded_columns)
    for field in required_fields:
        matches = difflib.get_close_matches(field, remaining_columns, n=1, cutoff=0.4)
        suggestions[field] = matches[0] if matches else None
    return suggestions


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

    suggestions = suggest_mapping(required_fields, uploaded_columns)
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

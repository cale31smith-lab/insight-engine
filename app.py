"""
app.py

The web interface for the Contractor Insight Engine: upload data, map its
columns onto the fixed internal schema, generate the report -- no code,
no terminal.

Run it with:
    uv run streamlit run app.py

Wraps the same pipeline (src/load.py, src/rules.py, src/narrate.py,
src/report.py) as the CLI (src/run.py) -- same tested, validated code
underneath. The new piece is column_mapping.py, which sits in front of
src/load.py so a business's own export column names never have to match
ours exactly. app_logic.py holds the plain assignment/validation logic
so it stays unit-testable outside a live Streamlit session.
"""

import io
import tempfile
from datetime import date
from pathlib import Path

import pandas as pd
import streamlit as st

import column_mapping as cm
from app_logic import ENTITIES, missing_and_duplicate_entities, unresolved_entities
from src import security
from src.load import load_all_from_dfs, DataLoadError
from src.narrate import narrate_with_retry
from src.report import NarratedFinding
from src.rules import run_rules
from src.security import deliver_report, mark_sent

RULES_PATH = Path(__file__).parent / "rules.yaml"
TOP_N_FINDINGS = 8

st.set_page_config(page_title="Contractor Insight Engine", page_icon="\U0001F4CA", layout="centered")
st.title("Contractor Insight Engine")
st.caption("Upload a shop's data, map its columns, generate the report -- no code, no terminal.")

# --- Test mode (sidebar) ---
test_mode = st.sidebar.checkbox(
    "Test mode",
    value=False,
    help="Suppresses all delivery log writes. PDF output is unchanged. Use for development/testing.",
)
security.set_test_mode(test_mode)
if test_mode:
    st.warning("TEST MODE — delivery log writes suppressed for this session.")

# --- Session state ---
if "mapped_dfs" not in st.session_state:
    st.session_state.mapped_dfs = {}       # entity -> confirmed, ready-to-load DataFrame
if "file_entity" not in st.session_state:
    st.session_state.file_entity = {}      # uploaded filename -> assigned entity (or None)
if "file_bytes" not in st.session_state:
    st.session_state.file_bytes = {}       # filename -> raw bytes, snapshotted once on upload
    # NOTE: Streamlit's file_uploader return value is not reliable to re-read across
    # reruns once this script starts calling st.rerun() programmatically (Step 4 does,
    # once per confirmed mapping) -- the widget can lose track of files that were
    # uploaded earlier in the session. Snapshotting bytes here, once, and reading only
    # from this dict from then on avoids that entirely.
if "generated_report" not in st.session_state:
    st.session_state.generated_report = None  # set after generation: {pdf_bytes, password, shop_name, report_date}

if st.button("Start over"):
    st.session_state.mapped_dfs = {}
    st.session_state.file_entity = {}
    st.session_state.file_bytes = {}
    st.session_state.generated_report = None
    st.rerun()

# --- Step 1: report details ---
st.header("1. Report details")
shop_name = st.text_input("Shop name", placeholder="e.g. Summit Heating & Air")
report_period = st.text_input("Reporting period", placeholder="e.g. June 2026")
source_name = st.text_input(
    "What software did this data come from?",
    placeholder="e.g. Jobber, QuickBooks, Manual Export",
    help="Used to remember your column mapping so repeat uploads from the same "
         "software skip the mapping step entirely.",
)

# --- Step 2: upload files ---
st.header("2. Upload your data files")
st.caption(
    "Upload 6 files -- one for each of: "
    + ", ".join(e.replace("_", " ") for e in ENTITIES)
    + ". Column names inside each file can be anything; you'll map them in Step 4."
)
uploaded = st.file_uploader(
    "Drag and drop your CSV files here",
    type="csv",
    accept_multiple_files=True,
)

ready_to_map = False

if uploaded:
    for f in uploaded:
        st.session_state.file_bytes[f.name] = f.getvalue()

available_filenames = list(st.session_state.file_bytes.keys())

if available_filenames and source_name:
    # --- Step 3: identify which file is which ---
    st.header("3. Identify each file")
    for filename in available_filenames:
        current = st.session_state.file_entity.get(filename)
        default_index = (ENTITIES.index(current) + 1) if current in ENTITIES else 0
        choice = st.selectbox(
            f"What kind of data is **{filename}**?",
            options=["-- select --"] + [e.replace("_", " ") for e in ENTITIES],
            index=default_index,
            key=f"entity_select_{filename}",
        )
        st.session_state.file_entity[filename] = None if choice == "-- select --" else choice.replace(" ", "_")

    missing_entities, duplicate_entities = missing_and_duplicate_entities(st.session_state.file_entity)

    if duplicate_entities:
        st.error(f"Each data type can only be assigned to one file. Duplicated: {', '.join(duplicate_entities)}")
    elif missing_entities:
        st.info(f"Still need a file for: {', '.join(e.replace('_', ' ') for e in missing_entities)}")
    else:
        ready_to_map = True

if ready_to_map:
    # --- Step 4: map columns for each entity ---
    st.header("4. Map columns")
    for filename, entity in st.session_state.file_entity.items():
        if entity is None or entity in st.session_state.mapped_dfs:
            continue
        raw_df = pd.read_csv(io.BytesIO(st.session_state.file_bytes[filename]))
        with st.expander(f"{entity.replace('_', ' ').title()} — from {filename}", expanded=True):
            mapped = cm.render_mapping_ui(entity, raw_df, source_name)
            if mapped is not None:
                st.session_state.mapped_dfs[entity] = mapped
                st.rerun()

    remaining = unresolved_entities(st.session_state.mapped_dfs)
    if remaining:
        st.info(f"Still need to map: {', '.join(e.replace('_', ' ') for e in remaining)}")
    else:
        st.success("All 6 files mapped and ready.")

# --- Step 5: generate report ---
if not unresolved_entities(st.session_state.mapped_dfs) and shop_name and report_period:
    st.header("5. Generate report")

    if st.button("Generate Report", type="primary"):
        with st.spinner("Loading and validating data..."):
            try:
                ds = load_all_from_dfs(st.session_state.mapped_dfs)
            except DataLoadError as e:
                st.error(f"Data validation failed: {e}")
                st.stop()
        st.success(
            f"Loaded {len(ds.jobs)} jobs, {len(ds.quotes)} quotes, "
            f"{len(ds.invoices)} invoices, {len(ds.technicians)} technicians, "
            f"{len(ds.time_entries)} timesheet rows."
        )

        with st.spinner("Computing metrics and evaluating rules..."):
            fired = run_rules(ds, RULES_PATH)
        st.success(f"{len(fired)} finding(s) identified.")

        if not fired:
            st.warning("No issues were flagged for this dataset. No report to generate.")
            st.stop()

        top_fired = fired[:TOP_N_FINDINGS]

        narrated = []
        progress = st.progress(0.0, text="Narrating findings...")
        for i, rule in enumerate(top_fired, start=1):
            progress.progress(i / len(top_fired), text=f"Narrating finding {i}/{len(top_fired)}: {rule.rule_id}")
            narration = narrate_with_retry(rule)
            if narration is not None:
                narrated.append(NarratedFinding(rule=rule, narration=narration))
            else:
                st.warning(f"Skipped '{rule.rule_id}': all retry attempts failed.")
        progress.empty()

        if not narrated:
            st.error("All narration calls failed. Check that ANTHROPIC_API_KEY is set correctly.")
            st.stop()

        with st.spinner("Rendering and protecting PDF..."):
            with tempfile.TemporaryDirectory() as tmp_dir:
                client_pdf_path = Path(tmp_dir) / "report_protected.pdf"
                _, password = deliver_report(
                    ds, narrated,
                    shop_name=shop_name,
                    report_period=report_period,
                    client_pdf_path=client_pdf_path,
                    delivery_method="web",
                )
                pdf_bytes = client_pdf_path.read_bytes()

        st.session_state.generated_report = {
            "pdf_bytes": pdf_bytes,
            "password": password,
            "shop_name": shop_name,
            "report_date": date.today().isoformat(),
        }

    if st.session_state.generated_report:
        r = st.session_state.generated_report
        st.success("Report ready.")
        st.download_button(
            label="Download Protected PDF",
            data=r["pdf_bytes"],
            file_name=f"{r['shop_name'].replace(' ', '_')}_Insight_Report.pdf",
            mime="application/pdf",
        )
        st.write("**Password — share this with the client separately from the PDF:**")
        st.code(r["password"])
        if st.button("Mark as Sent (after emailing the report to the client)"):
            mark_sent(r["shop_name"], r["report_date"])
            st.success("Logged as sent in delivery log.")

# Contractor Insight Engine

## What this is
A data consulting + AI analytics product for small HVAC and electrical
contractors. Core deliverable: an automated, recurring (monthly subscription)
PDF business-insight report generated from a contractor's operational data.

Business context: targeting ~500 customers over 5 years at ~$600/month
recurring. MVP is being proven end-to-end with synthetic data before
approaching real customers.

## Pipeline architecture
Five-stage pipeline, one responsibility per stage:

1. **LOAD** — `src/load.py` — ingest raw client data (from Streamlit upload
   interface) into the schema defined in `src/schema.py`
2. **METRICS** — `src/metrics.py` — compute operational metrics from loaded data
3. **RULES** — `src/rules.py` + `rules.yaml` — apply business
   rules/thresholds to flag issues and opportunities from the metrics
4. **NARRATE** — `src/narrate.py` — pass flagged issues through a single
   hosted LLM API to generate plain-English explanations/recommendations
5. **RENDER** — `src/report.py` — produce the final client-facing PDF via
   Jinja2 + WeasyPrint (templates in `templates/`)

Pipeline runner: `src/run.py` ties all five stages together.
```
uv run python -m src.run --data ./synthetic_data --shop-name "My Shop" --period "June 2026"
```

Streamlit upload interface: `app.py` (UI) + `app_logic.py` (business logic).

Column-mapping layer: `column_mapping.py` — LLM-assisted semantic mapping
with fuzzy-match fallback, so the pipeline accepts exports from any software.
Persisted mappings live in `saved_column_mappings.json`.

## Stack
- Python 3.12, managed with `uv`
- pandas — data manipulation
- Pydantic — schema validation/data models
- SQLite (dev) → Neon Postgres (target) for storage
- One hosted LLM API, called only through the narrate function (don't call
  the LLM API from anywhere else in the pipeline)
- Jinja2 + WeasyPrint — PDF report rendering
- Streamlit — client-facing data upload interface
- GitHub Actions — scheduling / CI

## Status
- MVP pipeline complete across all 9 build-guide steps: repo, schema/loaders,
  metrics engine, rules engine, LLM narration, PDF renderer, pipeline
  runner, validation, generalization
- Streamlit upload interface added
- Testing against a synthetic 12-month dataset for a fictional shop with 6
  deliberately planted issues (blind test — the pipeline should surface all
  6 without being told what they are)

## Not yet built (don't assume these exist)
- n8n workflow automation — deferred until Jobber/QuickBooks API connectors
  are in scope
- Column-mapping UI layer in the Streamlit interface (the backend
  `column_mapping.py` module exists; a user-facing confirmation/override UI
  is not yet built)
- Jobber/QuickBooks integrations
- Power BI — internal-only, single license, not part of this repo's
  client-facing pipeline

## Target-state stack (later, not now)
Core pipeline logic (Python/pandas/Pydantic) is meant to stay stack-stable
even as infrastructure around it upgrades to: n8n Cloud Enterprise
(orchestration), Azure Database for PostgreSQL, Azure Container Apps
(hosting), Postmark (email), GitHub Enterprise (repo/CI). Don't build toward
this yet — lean MVP first.

## Conventions
- Tests live in `tests/`, named `test_<module>.py` (e.g. `test_metrics.py`)
- Run tests: `uv run pytest`
- Run blind-test validation (all 6 planted issues): `uv run python -m src.run
  --data ./synthetic_data --shop-name "Summit Heating & Air" --period "June 2026"`
- Output PDFs land in `output/`
- One-off diagnostics at repo root: `check_load.py`, `check_live_narration.py`

## When making changes
- Keep the LOAD → METRICS → RULES → NARRATE → RENDER separation intact —
  don't let PDF-rendering logic leak into the rules engine, don't let LLM
  calls happen outside the narrate stage, etc.
- Run the blind-test validation after changes that touch METRICS or RULES
  to confirm all 6 planted issues are still caught.
- Flag any change that would require a real customer's data structure to
  differ from the current fixed schema — that's a sign the column-mapping
  layer is now needed.

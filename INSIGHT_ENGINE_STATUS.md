# Contractor Insight Engine — Status & Roadmap

**Last updated:** September 14, 2026
**Repo:** `C:\Dev\insight-engine`
**Product:** Automated PDF business-insight reports for small HVAC and electrical contractors
**Business model (proposed):** ~$600/month recurring subscription, target ~500 customers over 5 years

---

## Part 1: What's Been Built

### The core pipeline (complete and validated)

A five-stage pipeline that takes contractor CSV data and produces a finished PDF report in about 20–28 seconds.

| Stage | File | What it does |
|---|---|---|
| LOAD | `src/load.py`, `src/schema.py` | Ingests CSVs into validated Pydantic models (Technician, Customer, Job, Quote, Invoice, TimeEntry) |
| METRICS | `src/metrics.py` | Computes 7 operational metrics: margin by job type, callback rate, tech utilization, quote win rate, pricing consistency, cash cycle, AR outstanding. Supporting metrics `avg_ticket` and `revenue_per_tech_day` also exist and are used internally by the dollar-impact estimators |
| RULES | `src/rules.py` + `rules.yaml` | Applies thresholds to flag issues, each with a dollar-impact estimate. Thresholds live in `rules.yaml`; dollar-impact estimators are Python functions in `src/rules.py` (`IMPACT_FNS`), one per rule |
| NARRATE | `src/narrate.py` | One LLM call per finding, converting numbers into plain-English explanations. `narrate_with_retry` (3 attempts, 2 s delay) lives here and is called by both the CLI runner and the Streamlit app |
| RENDER | `src/report.py` | Jinja2 + WeasyPrint → professional navy/teal PDF |

**Runner:** `src/run.py` ties all five stages together in one CLI command.

### Interfaces

- **Streamlit web app** (`app.py` + `app_logic.py`) — a 5-stage wizard: report details → file upload → entity assignment → column mapping → report generation. No terminal required.
- **Column mapping** (`column_mapping.py`) — LLM-assisted semantic mapping of a client's column names to the internal schema, with fuzzy-match fallback, suggestion caching, and validation against hallucinated column names. Saved mappings persist in `saved_column_mappings.json`.
- **Column-mapping confirmation UI** — `render_mapping_ui()` in `column_mapping.py`, called from `app.py` Step 4. Renders per-field dropdowns pre-filled with LLM/fuzzy suggestions, blocks on unmapped required fields, shows a live 5-row data preview with canonical column names applied before the user confirms, and saves confirmed mappings so repeat uploads from the same software skip the step entirely.

### Quality and validation

- **31 passing tests** in `tests/`, maintained throughout development
- **Blind test passed:** built a synthetic shop with 6 deliberately hidden problems, ran the pipeline without looking at the answer key — found all 6, zero false positives, dollar figures hand-verified
- **Generalization test passed:** built a second, entirely different synthetic shop. The same unchanged code found all 6 of its different problems. This proves the system isn't overfit to one dataset — the single most important technical result so far
- **Determinism confirmed:** two full reruns produced identical findings, amounts, and ordering
- **Hallucination guard:** narration output is rejected if the LLM's echoed metric value or dollar impact deviates from the computed input by more than 1% (relative), with a $1 absolute floor on the dollar-impact tolerance

### Bugs found and fixed during live testing

- Stale file-uploader references across Streamlit reruns (fixed by snapshotting file bytes into session state)
- NaN→None conversion failing on all-blank float64 columns from real CSVs
- Format normalization added for non-ISO dates and currency-formatted numbers (`"$5,000.00"`, `"01/15/2026"`, `"15-Jan-2026"`)
- Missing dollar signs on page 2 of the rendered PDF

### Development workflow (set up this session)

- Migrated from copy-paste-into-PowerShell to **Claude Code** working directly in the repo
- Wrote and committed `CLAUDE.md` — a repo-root context file documenting architecture, stack, conventions, and what is deliberately not built yet. Claude Code reads this automatically at the start of every session
- Confirmed the full loop works: read repo → edit → verify → commit → push

### Business artifacts

- Business plan document
- Status-update email to boss
- Discovery call script with post-call logging template

---

## Part 2: Where Things Actually Stand

**Technically:** strong. The pipeline works, it's tested, it generalizes, and it's deterministic. This is further than most solo MVPs get.

**Commercially:** unvalidated. Zero contractors have been spoken to. Zero paying customers. All validation to date has been against synthetic data that was generated with reasonable but invented assumptions.

**The open strategic question:** the model assumes $600/month *recurring*, but the product may structurally be a one-time diagnostic. A shop owner who learns their margin by job type in month one may not need to relearn it in month four. Competitors like Jobber already ship built-in reporting. Whether recurring revenue survives contact with real customers is the single biggest unknown — and it's a question no amount of additional code answers.

---

## Part 3: Next Major Improvements, Ranked by Feasibility

Ranked easiest/fastest first. Note that feasibility and *value* are not the same thing — see the recommendation at the end.

| # | Improvement | Effort | Feasibility | Notes |
|---|---|---|---|---|
| 1 | **Customer discovery calls** (first 10 shops) | 1–2 weeks, no code | **Very high** | Script already written. Requires only a lead list and willingness to make calls. Zero technical risk |
| 2 | **Repo hygiene** — `.gitignore` for `output/`, clean up root-level diagnostic scripts | <1 hour | **Very high** | Trivial, prevents generated PDFs polluting git history |
| 3 | **Hosted deployment** (Streamlit Community Cloud or Azure Container Apps) | 2–4 days | **High** | Needed before any customer touches it. Streamlit Cloud is the fast path; Azure is the target-state path |
| 4 | **Automated monthly scheduling** via GitHub Actions | 2–3 days | **Medium-high** | Straightforward once hosted. Makes "recurring" real rather than manual |
| 5 | **Jobber API connector** (Step 10) | 1–2 weeks | **Medium** | Requires developer sandbox approval (external dependency, unknown wait). API learning curve. Eliminates manual CSV export — the biggest onboarding friction |
| 6 | **QuickBooks connector** | 2–3 weeks | **Medium-low** | OAuth complexity plus accounting-data edge cases. Meaningfully harder than Jobber |
| 7 | **Multi-tenancy, auth, and billing** (Stripe) | 3–5 weeks | **Low-medium** | This is what turns a working tool into an actual SaaS business. Substantial scope: user accounts, data isolation, subscription lifecycle, payment failure handling |
| 8 | **n8n orchestration layer** | — | **Deliberately deferred** | Only worth it once connectors exist. Pipeline logic must stay in tested Python, never in a visual workflow tool |
| 9 | **Power BI internal dashboard** | — | **Deliberately deferred** | Internal single-license only, not client-facing. Not on the critical path |

---

## Part 4: Recommendation

**Do #1 before anything on this list.**

Items 3 through 7 represent roughly two to three months of solid engineering work. Every one of them is a bet that the product, as currently conceived, is something contractors will pay $600/month for. That bet is currently unhedged — it rests entirely on synthetic data and assumption.

The cheapest possible test is ten phone calls. The specific hypothesis to test: **how many of the first 10 shop owners already know their margin by job type before being asked?** If most of them do, the core value proposition is weaker than assumed and the product needs rethinking before more code gets written. If most don't, that's real signal — and the roadmap above becomes worth executing.

If the discovery calls come back positive, the natural build order is: **3 → 5 → 4 → 7**. That sequence gets a real contractor's real data through a hosted system with minimal manual work, which is the shortest path to a genuine pilot.

**One trap worth naming:** items 3 through 5 are comfortable work. They're bounded, technical, and produce visible progress. Discovery calls are uncomfortable, unbounded, and may produce an answer nobody wants. The pull toward the comfortable work is strong and should be resisted until the calls are done.

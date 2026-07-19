"""
src/run.py

The pipeline runner. Ties LOAD -> METRICS -> RULES -> NARRATE -> RENDER
into one command:

    uv run python -m src.run --data ./synthetic_data --shop-name "Summit Heating & Air" --period "June 2026"

Acceptance test (per the build guide): fresh clone -> uv sync -> run ->
PDF in under 2 minutes.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from src.load import load_all
from src.narrate import narrate_finding, NarrationError
from src.report import render_report, NarratedFinding
from src.rules import run_rules

PROJECT_ROOT = Path(__file__).parent.parent
DEFAULT_RULES_PATH = PROJECT_ROOT / "rules.yaml"

TOP_N_FINDINGS = 8  # generous cap; with 7 rules currently defined, this effectively shows every real finding
MAX_RETRIES = 3
RETRY_DELAY_SECONDS = 2


def narrate_with_retry(rule, max_retries: int = MAX_RETRIES) -> "Narration | None":
    """
    A single flaky network call shouldn't take down the whole report.
    Retries a few times with a short delay, then gives up on that one
    finding and lets the rest of the report generate -- printing a
    warning so the gap is visible rather than silent.
    """
    last_error = None
    for attempt in range(1, max_retries + 1):
        try:
            return narrate_finding(rule)
        except (NarrationError, Exception) as e:  # network errors are broad; narrow later if needed
            last_error = e
            if attempt < max_retries:
                print(f"  [retry {attempt}/{max_retries}] {rule.rule_id} failed ({e}); retrying...")
                time.sleep(RETRY_DELAY_SECONDS)
    print(f"  [SKIPPED] {rule.rule_id} failed after {max_retries} attempts: {last_error}")
    return None


def main():
    parser = argparse.ArgumentParser(description="Run the Contractor Insight Engine pipeline.")
    parser.add_argument("--data", type=Path, required=True, help="Path to folder of input CSVs")
    parser.add_argument("--shop-name", type=str, default="Sample Shop", help="Shop name for the report header")
    parser.add_argument("--period", type=str, default="Current Period", help="Reporting period label")
    parser.add_argument("--rules", type=Path, default=DEFAULT_RULES_PATH, help="Path to rules.yaml")
    parser.add_argument("--output", type=Path, default=Path("output/report.pdf"), help="Output PDF path")
    args = parser.parse_args()

    start = time.time()

    print(f"[1/5] Loading data from {args.data} ...")
    ds = load_all(args.data)
    print(f"      {len(ds.jobs)} jobs, {len(ds.quotes)} quotes, {len(ds.invoices)} invoices, "
          f"{len(ds.technicians)} techs, {len(ds.time_entries)} timesheet rows")

    print("[2/5] Metrics computed as part of rule evaluation (see step 3).")

    print(f"[3/5] Evaluating rules from {args.rules} ...")
    fired = run_rules(ds, args.rules)
    print(f"      {len(fired)} rules fired")

    top_fired = fired[:TOP_N_FINDINGS]
    if not top_fired:
        print("No findings fired -- nothing to report. Exiting.")
        sys.exit(0)

    print(f"[4/5] Narrating top {len(top_fired)} findings via LLM ...")
    narrated: list[NarratedFinding] = []
    for rule in top_fired:
        print(f"  - {rule.rule_id} ({rule.key or 'shop-wide'})")
        narration = narrate_with_retry(rule)
        if narration is not None:
            narrated.append(NarratedFinding(rule=rule, narration=narration))

    if not narrated:
        print("All narration calls failed -- cannot render a report. Check your ANTHROPIC_API_KEY.")
        sys.exit(1)

    print(f"[5/5] Rendering report to {args.output} ...")
    result_path = render_report(
        ds, narrated,
        shop_name=args.shop_name,
        report_period=args.period,
        output_path=args.output,
    )

    elapsed = time.time() - start
    print(f"\nDone in {elapsed:.1f}s -- {result_path.resolve()}")
    if elapsed > 120:
        print("WARNING: exceeded the 2-minute acceptance target from the build guide.")


if __name__ == "__main__":
    main()

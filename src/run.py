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
from src.narrate import narrate_with_retry, NarrationError
from src.report import NarratedFinding
from src.rules import run_rules
from src import security
from src.security import deliver_report, mark_sent

PROJECT_ROOT = Path(__file__).parent.parent
DEFAULT_RULES_PATH = PROJECT_ROOT / "rules.yaml"

TOP_N_FINDINGS = 8  # generous cap; with 7 rules currently defined, this effectively shows every real finding


def main():
    parser = argparse.ArgumentParser(description="Run the Contractor Insight Engine pipeline.")
    parser.add_argument("--data", type=Path, default=None, help="Path to folder of input CSVs")
    parser.add_argument("--shop-name", type=str, default="Sample Shop", help="Shop name for the report header")
    parser.add_argument("--period", type=str, default="Current Period", help="Reporting period label")
    parser.add_argument("--rules", type=Path, default=DEFAULT_RULES_PATH, help="Path to rules.yaml")
    parser.add_argument("--output", type=Path, default=Path("output/report.pdf"), help="Output PDF path (password-protected client copy)")
    parser.add_argument("--mark-sent", action="store_true", help="Log that a report was sent; requires --shop-name and --report-date")
    parser.add_argument("--report-date", type=str, default=None, help="Report date in YYYY-MM-DD format (used with --mark-sent)")
    parser.add_argument("--test", action="store_true", help="Test mode: suppress all delivery log writes (PDF output is unchanged)")
    args = parser.parse_args()

    if args.test:
        security.set_test_mode(True)
        print("*** TEST MODE — delivery log writes suppressed for this run ***")

    if args.mark_sent:
        if not args.report_date:
            print("Error: --report-date YYYY-MM-DD is required with --mark-sent")
            sys.exit(1)
        mark_sent(args.shop_name, args.report_date)
        print(f"Marked '{args.shop_name}' report for {args.report_date} as sent.")
        return

    if not args.data:
        print("Error: --data is required for report generation")
        sys.exit(1)

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

    print(f"[5/5] Rendering and protecting report ...")
    client_path, password = deliver_report(
        ds, narrated,
        shop_name=args.shop_name,
        report_period=args.period,
        client_pdf_path=args.output,
        delivery_method="cli",
    )

    elapsed = time.time() - start
    print(f"\nDone in {elapsed:.1f}s -- {client_path.resolve()}")
    print(f"Password: {password}")
    print("(Share the password with the client separately. Run --mark-sent after emailing.)")
    if elapsed > 120:
        print("WARNING: exceeded the 2-minute acceptance target from the build guide.")


if __name__ == "__main__":
    main()

"""
regenerate_report.py

Re-protect an existing internal PDF copy with a new password.
Use this when a client loses their password -- no source data needed,
only the retained internal copy in internal_reports/.

    uv run python regenerate_report.py \
        --internal-pdf internal_reports/summit_heating_air_2026-09-15.pdf \
        --output output/summit_heating_air_2026-09-15_resent.pdf
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from src.security import protect_pdf


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Regenerate a password-protected PDF from a retained internal copy."
    )
    parser.add_argument(
        "--internal-pdf", type=Path, required=True,
        help="Path to the retained internal (unprotected) PDF in internal_reports/",
    )
    parser.add_argument(
        "--output", type=Path, required=True,
        help="Where to write the new password-protected PDF",
    )
    args = parser.parse_args()

    if not args.internal_pdf.exists():
        print(f"Error: {args.internal_pdf} not found.")
        sys.exit(1)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    password = protect_pdf(args.internal_pdf, args.output)
    print(f"Protected PDF: {args.output.resolve()}")
    print(f"New password:  {password}")


if __name__ == "__main__":
    main()

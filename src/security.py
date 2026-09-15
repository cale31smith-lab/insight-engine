"""
src/security.py

PDF delivery security: password protection, delivery logging, and retention.

Public API
----------
protect_pdf(input_path, output_path) -> str
    AES-256 password-protect a PDF; returns the generated password.
    Never writes the password anywhere -- that's the caller's job.

deliver_report(ds, narrated, shop_name, report_period, client_pdf_path,
               delivery_method) -> (Path, str)
    Shared wrapper for CLI and Streamlit:
      1. Renders unprotected PDF to internal_reports/{slug}_{date}.pdf
      2. Password-protects it to client_pdf_path
      3. Logs a "generated" event (no password)
      4. Runs the 90-day retention cleanup
    Returns (client_pdf_path, password).

mark_sent(client_name, report_date)
    Log that a report was manually sent to the client (e.g. via email).
    Call this separately after you've actually emailed it.

set_test_mode(enabled)
    Suppress all delivery log writes for the current process run.
    Opt-in; default is False. Call once at startup (CLI --test flag or
    Streamlit sidebar toggle) before any deliver_report / mark_sent call.
    PDF rendering and password protection are unaffected -- only the CSV
    log rows are skipped.

cleanup_old_internal_reports(days=90) -> int
    Delete internal PDFs whose filename date is older than `days` days.
    Parses the date from {slug}_{YYYY-MM-DD}.pdf -- does not rely on
    filesystem mtime, which can change on copy/move.
    Logs each deletion. Returns the number of files deleted.
"""

from __future__ import annotations

import csv
import re
import secrets
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pikepdf

from src.load import Dataset
from src.report import NarratedFinding, render_report

INTERNAL_REPORTS_DIR = Path(__file__).parent.parent / "internal_reports"
DELIVERY_LOG_PATH = INTERNAL_REPORTS_DIR / "delivery_log.csv"
_LOG_FIELDS = ["client_name", "report_date", "delivery_method", "event", "timestamp"]

_DATE_RE = re.compile(r"^(.+)_(\d{4}-\d{2}-\d{2})$")

_test_mode: bool = False


def set_test_mode(enabled: bool) -> None:
    """
    Suppress all delivery log writes for this process run (opt-in).
    PDF rendering and password protection are unaffected.
    Call once at startup before any deliver_report / mark_sent call.
    """
    global _test_mode
    _test_mode = enabled


def _client_slug(name: str) -> str:
    return re.sub(r"[^\w]", "", name.lower().replace(" ", "_"))


def _log_event(
    client_name: str, report_date: str, delivery_method: str, event: str
) -> None:
    """Append one event row to the delivery log. Never call with a password."""
    if _test_mode:
        return
    INTERNAL_REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    write_header = not DELIVERY_LOG_PATH.exists()
    with DELIVERY_LOG_PATH.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=_LOG_FIELDS)
        if write_header:
            writer.writeheader()
        writer.writerow({
            "client_name": client_name,
            "report_date": report_date,
            "delivery_method": delivery_method,
            "event": event,
            "timestamp": datetime.now(UTC).isoformat(),
        })


def protect_pdf(input_path: Path, output_path: Path) -> str:
    """
    AES-256 (R=6) password-protect the PDF at input_path, write to output_path.
    Returns the generated password. The password is never written to any file
    by this function -- surface it to the user and discard it.
    """
    password = secrets.token_urlsafe(8)
    with pikepdf.open(input_path) as pdf:
        pdf.save(
            output_path,
            encryption=pikepdf.Encryption(owner=password, user=password, R=6),
        )
    return password


def mark_sent(client_name: str, report_date: str) -> None:
    """
    Log that this report was manually sent to the client.
    Call this after you've actually emailed the protected PDF --
    not at generation time.
    """
    _log_event(client_name, report_date, delivery_method="email", event="sent")


def cleanup_old_internal_reports(days: int = 90) -> int:
    """
    Delete internal PDFs whose filename date is older than `days` days.
    Parses {slug}_{YYYY-MM-DD}.pdf -- ignores files that don't match.
    Logs each deletion. Returns count of deleted files.
    """
    if not INTERNAL_REPORTS_DIR.exists():
        return 0
    cutoff = date.today() - timedelta(days=days)
    deleted = 0
    for pdf in sorted(INTERNAL_REPORTS_DIR.glob("*.pdf")):
        m = _DATE_RE.match(pdf.stem)
        if not m:
            continue
        try:
            report_date = date.fromisoformat(m.group(2))
        except ValueError:
            continue
        if report_date < cutoff:
            pdf.unlink()
            _log_event(m.group(1), m.group(2), "retention", "internal_copy_deleted")
            deleted += 1
    return deleted


def deliver_report(
    ds: Dataset,
    narrated: list[NarratedFinding],
    shop_name: str,
    report_period: str,
    client_pdf_path: Path,
    delivery_method: str = "cli",
) -> tuple[Path, str]:
    """
    Shared delivery wrapper used by both the CLI runner and the Streamlit app.

    Renders the unprotected PDF to internal_reports/{slug}_{date}.pdf (the
    retained copy that lets passwords be regenerated without re-running the
    pipeline), then produces a password-protected copy at client_pdf_path.
    Logs a "generated" event and runs the 90-day retention cleanup before
    returning.

    Returns (client_pdf_path, password). The password is not logged anywhere.
    """
    slug = _client_slug(shop_name)
    today = date.today().isoformat()
    INTERNAL_REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    internal_path = INTERNAL_REPORTS_DIR / f"{slug}_{today}.pdf"

    render_report(
        ds, narrated,
        shop_name=shop_name,
        report_period=report_period,
        output_path=internal_path,
    )
    password = protect_pdf(internal_path, client_pdf_path)
    _log_event(shop_name, today, delivery_method, "generated")
    cleanup_old_internal_reports()

    return client_pdf_path, password

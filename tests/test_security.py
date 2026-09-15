"""
tests/test_security.py

Covers PDF password protection, delivery log integrity, mark_sent, and
the 90-day retention cleanup. All tests run offline -- no API key, no
pipeline execution. A monkeypatch fixture redirects INTERNAL_REPORTS_DIR
and DELIVERY_LOG_PATH to tmp_path so nothing touches the real filesystem.
"""

from __future__ import annotations

import csv
from datetime import date, timedelta
from pathlib import Path

import pikepdf
import pytest

import src.security as sec
from src.security import (
    _log_event,
    cleanup_old_internal_reports,
    mark_sent,
    protect_pdf,
)


def _make_pdf(path: Path) -> None:
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(612, 792))
    pdf.save(path)


@pytest.fixture(autouse=True)
def _isolate_storage(tmp_path, monkeypatch):
    """Redirect all storage writes to tmp_path for every test in this module."""
    monkeypatch.setattr(sec, "INTERNAL_REPORTS_DIR", tmp_path)
    monkeypatch.setattr(sec, "DELIVERY_LOG_PATH", tmp_path / "delivery_log.csv")


# ---------------------------------------------------------------------------
# Password protection
# ---------------------------------------------------------------------------

def test_protect_pdf_returned_password_opens_pdf(tmp_path):
    src = tmp_path / "plain.pdf"
    dst = tmp_path / "protected.pdf"
    _make_pdf(src)

    password = protect_pdf(src, dst)

    with pikepdf.open(dst, password=password) as pdf:
        assert len(pdf.pages) == 1


def test_protect_pdf_fails_without_password(tmp_path):
    src = tmp_path / "plain.pdf"
    dst = tmp_path / "protected.pdf"
    _make_pdf(src)
    protect_pdf(src, dst)

    with pytest.raises(pikepdf.PasswordError):
        pikepdf.open(dst)


def test_regenerate_password_no_source_data(tmp_path):
    """
    protect_pdf on an existing internal copy produces a new working password
    without touching any raw source data -- this is the regeneration path.
    """
    internal = tmp_path / "client_2026-09-15.pdf"
    new_protected = tmp_path / "client_resent.pdf"
    _make_pdf(internal)

    new_password = protect_pdf(internal, new_protected)

    with pikepdf.open(new_protected, password=new_password) as pdf:
        assert len(pdf.pages) == 1


# ---------------------------------------------------------------------------
# Delivery log
# ---------------------------------------------------------------------------

def test_delivery_log_contains_no_password(tmp_path):
    sensitive = "s3cr3t-p4ssw0rd-xyz"
    _log_event("Test Client", "2026-09-15", "cli", "generated")

    log_text = (tmp_path / "delivery_log.csv").read_text()
    assert "Test Client" in log_text
    assert "generated" in log_text
    assert sensitive not in log_text


def test_delivery_log_has_expected_columns(tmp_path):
    _log_event("Acme HVAC", "2026-09-15", "web", "generated")

    rows = list(csv.DictReader((tmp_path / "delivery_log.csv").open()))
    assert len(rows) == 1
    row = rows[0]
    assert row["client_name"] == "Acme HVAC"
    assert row["report_date"] == "2026-09-15"
    assert row["delivery_method"] == "web"
    assert row["event"] == "generated"
    assert row["timestamp"]
    assert "password" not in row


def test_mark_sent_appends_sent_event(tmp_path):
    mark_sent("Test Client", "2026-09-15")

    rows = list(csv.DictReader((tmp_path / "delivery_log.csv").open()))
    assert len(rows) == 1
    assert rows[0]["event"] == "sent"
    assert rows[0]["client_name"] == "Test Client"
    assert rows[0]["delivery_method"] == "email"


# ---------------------------------------------------------------------------
# Retention cleanup
# ---------------------------------------------------------------------------

def test_cleanup_deletes_old_file_and_keeps_recent(tmp_path):
    old = tmp_path / f"testclient_{(date.today() - timedelta(days=91)).isoformat()}.pdf"
    recent = tmp_path / f"testclient_{date.today().isoformat()}.pdf"
    _make_pdf(old)
    _make_pdf(recent)

    count = cleanup_old_internal_reports(days=90)

    assert count == 1
    assert not old.exists()
    assert recent.exists()


def test_cleanup_logs_deletion_event(tmp_path):
    old = tmp_path / f"acmehvac_{(date.today() - timedelta(days=100)).isoformat()}.pdf"
    _make_pdf(old)

    cleanup_old_internal_reports(days=90)

    rows = list(csv.DictReader((tmp_path / "delivery_log.csv").open()))
    assert any(r["event"] == "internal_copy_deleted" for r in rows)


def test_cleanup_skips_files_without_date_in_name(tmp_path):
    undated = tmp_path / "no_date_here.pdf"
    _make_pdf(undated)

    count = cleanup_old_internal_reports(days=90)

    assert count == 0
    assert undated.exists()


def test_cleanup_returns_zero_when_dir_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(sec, "INTERNAL_REPORTS_DIR", tmp_path / "nonexistent")

    assert cleanup_old_internal_reports() == 0

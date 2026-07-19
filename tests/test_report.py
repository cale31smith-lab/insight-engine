"""
tests/test_report.py

The report renderer is inherently visual -- "does this look right" isn't
a unit-testable claim. What IS testable: that rendering doesn't crash,
produces a real PDF (correct file signature), and isn't suspiciously
tiny (which would mean the template silently rendered blank).
"""

from pathlib import Path

from src.load import Dataset
from src.narrate import Narration
from src.rules import FiredRule
from src.report import render_report, NarratedFinding
from tests.test_metrics import JOBS, INVOICES, TIME_ENTRIES
from tests.test_rules import TECHNICIANS, QUOTES

DATASET = Dataset(
    technicians=TECHNICIANS, customers=[], jobs=JOBS, quotes=QUOTES,
    invoices=INVOICES, time_entries=TIME_ENTRIES,
)

SAMPLE_RULE = FiredRule(
    rule_id="negative_margin_job_type",
    description="Job type has negative gross margin",
    scope="job_type",
    key="Furnace Install",
    metric_value=-0.10,
    threshold=-0.05,
    dollar_impact=300.0,
    recommendation_template_id="fix_pricing_or_drop_job_type",
)

SAMPLE_NARRATION = Narration(
    finding="Furnace Install jobs are losing money.",
    why_it_matters="Negative margin on every job of this type.",
    action="Reprice or discontinue this job type.",
    how_measured="(revenue - labor - material) / revenue.",
    metric_value_echoed=-0.10,
    dollar_impact_echoed=300.0,
)


def test_render_report_produces_valid_pdf(tmp_path):
    out_path = tmp_path / "test_report.pdf"
    narrated = [NarratedFinding(rule=SAMPLE_RULE, narration=SAMPLE_NARRATION)]

    result = render_report(
        DATASET, narrated,
        shop_name="Test Shop", report_period="Test Period",
        output_path=out_path,
    )

    assert result.exists()
    with open(result, "rb") as f:
        header = f.read(5)
    assert header == b"%PDF-", "Output is not a valid PDF (missing %PDF- signature)"
    assert result.stat().st_size > 5000, "PDF suspiciously small -- template may have rendered blank"

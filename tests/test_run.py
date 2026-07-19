"""
tests/test_run.py

Tests the pipeline runner's retry logic in isolation, and a full
end-to-end run against the real synthetic dataset with narration
mocked out (so this test needs no API key and costs nothing to run
in CI). The live, real-API end-to-end run is a manual check
(see check_live_narration.py) -- not something CI should depend on.
"""

from pathlib import Path
from unittest.mock import patch

import pytest

from src.narrate import Narration, NarrationError
from src.rules import FiredRule
from src.run import narrate_with_retry, main

SAMPLE_RULE = FiredRule(
    rule_id="negative_margin_job_type",
    description="Job type has negative gross margin",
    scope="job_type",
    key="Furnace Install",
    metric_value=-0.319,
    threshold=-0.05,
    dollar_impact=316783.0,
    recommendation_template_id="fix_pricing_or_drop_job_type",
)

SAMPLE_NARRATION = Narration(
    finding="Test finding.", why_it_matters="Test.", action="Test.",
    how_measured="Test.", metric_value_echoed=-0.319, dollar_impact_echoed=316783.0,
)


def test_narrate_with_retry_succeeds_first_try():
    with patch("src.run.narrate_finding", return_value=SAMPLE_NARRATION) as mock_call:
        result = narrate_with_retry(SAMPLE_RULE)
    assert result == SAMPLE_NARRATION
    assert mock_call.call_count == 1


def test_narrate_with_retry_succeeds_after_transient_failure():
    with patch("src.run.narrate_finding", side_effect=[NarrationError("boom"), SAMPLE_NARRATION]) as mock_call, \
         patch("src.run.time.sleep"):  # skip the real delay in tests
        result = narrate_with_retry(SAMPLE_RULE, max_retries=3)
    assert result == SAMPLE_NARRATION
    assert mock_call.call_count == 2


def test_narrate_with_retry_gives_up_after_max_attempts():
    with patch("src.run.narrate_finding", side_effect=NarrationError("boom")) as mock_call, \
         patch("src.run.time.sleep"):
        result = narrate_with_retry(SAMPLE_RULE, max_retries=3)
    assert result is None
    assert mock_call.call_count == 3


def test_full_pipeline_end_to_end(tmp_path, monkeypatch):
    """
    Runs the entire LOAD -> METRICS -> RULES -> NARRATE -> RENDER pipeline
    against the real synthetic dataset, with narration mocked (canned
    response for every fired rule) so this needs no API key and costs
    nothing to run in CI. Confirms a real PDF comes out the other end.
    """
    output_path = tmp_path / "report.pdf"
    synthetic_data_dir = Path(__file__).parent.parent / "synthetic_data"

    monkeypatch.setattr("src.run.narrate_finding", lambda rule: SAMPLE_NARRATION)
    monkeypatch.setattr(
        "sys.argv",
        [
            "run.py",
            "--data", str(synthetic_data_dir),
            "--shop-name", "Test Shop",
            "--period", "Test Period",
            "--output", str(output_path),
        ],
    )

    main()

    assert output_path.exists()
    with open(output_path, "rb") as f:
        assert f.read(5) == b"%PDF-"
    assert output_path.stat().st_size > 5000

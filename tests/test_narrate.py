"""
tests/test_narrate.py

Tests the trust boundary of the narration step: validate_narration()
must accept a correct response and reject any response where the LLM
altered the numbers it was given. This is tested directly against
raw dicts (as if they came back from the API) so it runs with no
network access and no API key -- exactly the point, since this logic
must be provably correct independent of any live model call.
"""

from unittest.mock import MagicMock

import pytest

from src.rules import FiredRule
from src.narrate import validate_narration, narrate_finding, NarrationError

RULE = FiredRule(
    rule_id="negative_margin_job_type",
    description="Job type has negative gross margin",
    scope="job_type",
    key="Furnace Install",
    metric_value=-0.319,
    threshold=-0.05,
    dollar_impact=316783.0,
    recommendation_template_id="fix_pricing_or_drop_job_type",
)

VALID_RESPONSE = {
    "finding": "Furnace Install jobs are losing money on every job.",
    "why_it_matters": "This job type has a -31.9% gross margin, meaning materials and labor cost more than what's billed.",
    "action": "Review and raise pricing on Furnace Install jobs, or pause offering them until repriced.",
    "how_measured": "Calculated as (revenue - labor cost - material cost) / revenue across all non-callback Furnace Install jobs.",
    "metric_value_echoed": -0.319,
    "dollar_impact_echoed": 316783.0,
}


def test_valid_narration_passes():
    narration = validate_narration(RULE, VALID_RESPONSE)
    assert narration.finding
    assert narration.metric_value_echoed == -0.319


def test_rejects_altered_metric_value():
    bad = dict(VALID_RESPONSE)
    bad["metric_value_echoed"] = -0.25  # LLM "helpfully" rounded/changed it
    with pytest.raises(NarrationError, match="metric_value"):
        validate_narration(RULE, bad)


def test_rejects_altered_dollar_impact():
    bad = dict(VALID_RESPONSE)
    bad["dollar_impact_echoed"] = 300000.0  # LLM rounded a precise number
    with pytest.raises(NarrationError, match="dollar_impact"):
        validate_narration(RULE, bad)


def test_rejects_missing_field():
    bad = dict(VALID_RESPONSE)
    del bad["action"]
    with pytest.raises(NarrationError, match="Malformed"):
        validate_narration(RULE, bad)


def test_accepts_tiny_float_rounding():
    # Floating point round-trip through JSON can shift the last digit --
    # that's fine, this should NOT be rejected as an "alteration."
    close_enough = dict(VALID_RESPONSE)
    close_enough["metric_value_echoed"] = -0.3190001
    narration = validate_narration(RULE, close_enough)
    assert narration.metric_value_echoed == pytest.approx(-0.319, abs=1e-4)


def test_narrate_finding_uses_mocked_client():
    """
    Confirms narrate_finding() correctly wires a mocked LLM response
    through to validate_narration() -- without ever calling the real API.
    """
    mock_block = MagicMock()
    mock_block.type = "tool_use"
    mock_block.name = "submit_narration"
    mock_block.input = VALID_RESPONSE

    mock_response = MagicMock()
    mock_response.content = [mock_block]

    mock_client = MagicMock()
    mock_client.messages.create.return_value = mock_response

    narration = narrate_finding(RULE, client=mock_client)
    assert narration.finding == VALID_RESPONSE["finding"]
    mock_client.messages.create.assert_called_once()


def test_narrate_finding_rejects_mocked_bad_response():
    mock_block = MagicMock()
    mock_block.type = "tool_use"
    mock_block.name = "submit_narration"
    bad = dict(VALID_RESPONSE)
    bad["dollar_impact_echoed"] = 1.0  # wildly wrong
    mock_block.input = bad

    mock_response = MagicMock()
    mock_response.content = [mock_block]

    mock_client = MagicMock()
    mock_client.messages.create.return_value = mock_response

    with pytest.raises(NarrationError):
        narrate_finding(RULE, client=mock_client)

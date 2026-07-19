"""
src/narrate.py

The one place an LLM touches this pipeline. Turns a fired rule + its
exact numbers into a plain-English writeup, using a fixed tool-call
schema so the response shape is guaranteed.

The critical rule from the build guide: the LLM never computes
anything. It must echo back the exact metric_value and dollar_impact
it was given. If those echoed numbers don't match the input within a
tiny tolerance (for float rounding only), the response is rejected
outright rather than trusted.
"""

from __future__ import annotations

from typing import Optional

import anthropic
from pydantic import BaseModel, ValidationError

from src.rules import FiredRule

# Haiku is deliberately chosen here: this is a short, structured
# writing task, not a reasoning task. Cheaper and faster than Sonnet
# with no quality loss for "explain this number in plain English."
NARRATION_MODEL = "claude-haiku-4-5-20251001"


class Narration(BaseModel):
    finding: str
    why_it_matters: str
    action: str
    how_measured: str
    metric_value_echoed: float
    dollar_impact_echoed: float


class NarrationError(Exception):
    """Raised when the LLM response is missing, malformed, or alters an input number."""


NARRATE_TOOL = {
    "name": "submit_narration",
    "description": "Submit the plain-English narration for this finding.",
    "input_schema": {
        "type": "object",
        "properties": {
            "finding": {
                "type": "string",
                "description": "One sentence stating the problem, in plain English for a shop owner.",
            },
            "why_it_matters": {
                "type": "string",
                "description": "1-2 sentences on why this costs the business money.",
            },
            "action": {
                "type": "string",
                "description": "One concrete, specific recommended action.",
            },
            "how_measured": {
                "type": "string",
                "description": "One sentence explaining how this number was calculated.",
            },
            "metric_value_echoed": {
                "type": "number",
                "description": "Echo the exact metric_value you were given, unchanged.",
            },
            "dollar_impact_echoed": {
                "type": "number",
                "description": "Echo the exact dollar_impact you were given, unchanged.",
            },
        },
        "required": [
            "finding", "why_it_matters", "action", "how_measured",
            "metric_value_echoed", "dollar_impact_echoed",
        ],
    },
}


def _build_prompt(rule: FiredRule) -> str:
    return (
        "A rules engine flagged the following finding in a contractor shop's data. "
        "Write it up for the shop owner using the submit_narration tool. "
        "Do not invent, round, or recalculate any numbers -- echo the metric_value "
        "and dollar_impact exactly as given below.\n\n"
        f"Rule: {rule.rule_id}\n"
        f"Description: {rule.description}\n"
        f"Scope: {rule.scope}\n"
        f"Key: {rule.key or '(shop-wide)'}\n"
        f"Metric value: {rule.metric_value}\n"
        f"Threshold: {rule.threshold}\n"
        f"Estimated annualized dollar impact: ${rule.dollar_impact:,.2f}\n"
    )


def _call_llm(rule: FiredRule, client: Optional["anthropic.Anthropic"] = None) -> dict:
    client = client or anthropic.Anthropic()
    response = client.messages.create(
        model=NARRATION_MODEL,
        max_tokens=500,
        tools=[NARRATE_TOOL],
        tool_choice={"type": "tool", "name": "submit_narration"},
        messages=[{"role": "user", "content": _build_prompt(rule)}],
    )
    for block in response.content:
        if block.type == "tool_use" and block.name == "submit_narration":
            return block.input
    raise NarrationError("No tool_use block returned by the model.")


def validate_narration(rule: FiredRule, raw: dict, tolerance: float = 0.01) -> Narration:
    """
    Parses the raw LLM output and rejects it if the echoed numbers don't
    match what was actually fed in. This is the trust boundary of the
    whole pipeline -- separated out from _call_llm so it can be tested
    without hitting the network.
    """
    try:
        narration = Narration(**raw)
    except ValidationError as e:
        raise NarrationError(f"Malformed narration response: {e}") from e

    value_tol = max(tolerance, abs(rule.metric_value) * tolerance)
    if abs(narration.metric_value_echoed - rule.metric_value) > value_tol:
        raise NarrationError(
            f"Echoed metric_value {narration.metric_value_echoed} does not match "
            f"input {rule.metric_value} -- rejecting narration."
        )

    impact_tol = max(1.0, abs(rule.dollar_impact) * tolerance)
    if abs(narration.dollar_impact_echoed - rule.dollar_impact) > impact_tol:
        raise NarrationError(
            f"Echoed dollar_impact {narration.dollar_impact_echoed} does not match "
            f"input {rule.dollar_impact} -- rejecting narration."
        )

    return narration


def narrate_finding(rule: FiredRule, client: Optional["anthropic.Anthropic"] = None) -> Narration:
    raw = _call_llm(rule, client=client)
    return validate_narration(rule, raw)

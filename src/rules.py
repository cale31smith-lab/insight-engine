"""
src/rules.py

Reads rules.yaml (thresholds live there, not here) and scans the Step 3
metrics for violations. Returns fired rules ranked by estimated dollar
impact, highest first. No LLM here either — dollar impact is computed
deterministically from the same data the metrics came from.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

import yaml
from pydantic import BaseModel

from src.load import Dataset
from src import metrics as m


class Rule(BaseModel):
    id: str
    description: str
    metric: str
    scope: str  # "job_type" | "tech_id" | "segment" | "global"
    comparator: str  # "lt" | "gt"
    threshold: float
    recommendation_template_id: str


class FiredRule(BaseModel):
    rule_id: str
    description: str
    scope: str
    key: str  # e.g. "Furnace Install", "T04", "Property Management", or "" for global
    metric_value: float
    threshold: float
    dollar_impact: float
    recommendation_template_id: str


def load_rules(path: Path) -> list[Rule]:
    with open(path) as f:
        raw = yaml.safe_load(f)
    return [Rule(**r) for r in raw["rules"]]


def _passes(value: float, comparator: str, threshold: float) -> bool:
    if comparator == "lt":
        return value < threshold
    if comparator == "gt":
        return value > threshold
    raise ValueError(f"Unknown comparator: {comparator}")


# ---------------------------------------------------------------------------
# Dollar-impact estimators, one per rule id.
# Each takes the dataset + the flagged key + the metric value that
# triggered the rule, and returns an estimated annualized dollar figure.
# These are deliberately simple, defensible estimates for the MVP —
# refine them during the Step 8 blind test if dollar accuracy is off.
# ---------------------------------------------------------------------------

def _impact_negative_margin(ds: Dataset, job_type: str, margin: float) -> float:
    df = m._jobs_df(ds.jobs)
    df = df[(~df["is_callback"]) & (df["job_type"] == job_type)]
    revenue = df["revenue"].sum()
    return abs(margin) * revenue


def _impact_high_callback(ds: Dataset, tech_id: str, rate: float) -> float:
    by_tech = m.callback_rate_by_tech(ds.jobs)
    median = by_tech.median()
    df = m._jobs_df(ds.jobs)
    job_count = df[df["tech_id"] == tech_id].shape[0]
    avg_ticket = m.avg_ticket(ds.jobs)
    extra_callback_rate = max(rate - median, 0)
    return extra_callback_rate * job_count * avg_ticket


def _impact_underutilized(ds: Dataset, tech_id: str, util: float, target: float = 0.75) -> float:
    df = m._time_entries_df(ds.time_entries)
    paid_hours = df[df["tech_id"] == tech_id]["paid_hours"].sum()
    tech_row = next((t for t in ds.technicians if t.tech_id == tech_id), None)
    hourly_cost = tech_row.loaded_hourly_cost if tech_row else 0.0
    gap = max(target - util, 0)
    return gap * paid_hours * hourly_cost


def _impact_low_win_rate(ds: Dataset, job_type: str, rate: float) -> float:
    df = m._quotes_df(ds.quotes)
    lost = df[(df["job_type"] == job_type) & (df["status"] == "lost")]
    return float(lost["amount"].sum())


def _impact_inconsistent_pricing(ds: Dataset, job_type: str, cov: float) -> float:
    df = m._jobs_df(ds.jobs)
    df = df[(~df["is_callback"]) & (df["job_type"] == job_type)]
    return float(df["revenue"].std() * df.shape[0])


def _impact_slow_pay(ds: Dataset, segment: str, days: float) -> float:
    df = m._invoices_df(ds.invoices)
    unpaid = df[(df["segment"] == segment) & (df["paid_date"].isna())]
    return float(unpaid["amount"].sum())


def _impact_high_ar(ds: Dataset, _key: str, ar_value: float) -> float:
    return ar_value


IMPACT_FNS: dict[str, Callable] = {
    "negative_margin_job_type": _impact_negative_margin,
    "high_callback_tech": _impact_high_callback,
    "underutilized_tech": _impact_underutilized,
    "low_win_rate_job_type": _impact_low_win_rate,
    "inconsistent_pricing_job_type": _impact_inconsistent_pricing,
    "slow_pay_segment": _impact_slow_pay,
    "high_ar_outstanding": _impact_high_ar,
}


# ---------------------------------------------------------------------------
# Metric lookup: maps a rule's `metric` name to the function that
# produces it, and whether that result is a per-key Series or a
# single global float.
# ---------------------------------------------------------------------------

def _get_metric_series_or_value(rule: Rule, ds: Dataset):
    if rule.metric == "gross_margin_by_job_type":
        return m.gross_margin_by_job_type(ds.jobs)
    if rule.metric == "callback_rate_by_tech":
        return m.callback_rate_by_tech(ds.jobs)
    if rule.metric == "tech_utilization":
        return m.tech_utilization(ds.time_entries)
    if rule.metric == "win_rate_by_job_type":
        return m.win_rate_by_job_type(ds.quotes)
    if rule.metric == "pricing_consistency":
        return m.pricing_consistency(ds.jobs)
    if rule.metric == "cash_cycle_by_segment":
        return m.cash_cycle_by_segment(ds.invoices)
    if rule.metric == "ar_outstanding":
        return m.ar_outstanding(ds.invoices)  # a single float, not a Series
    raise ValueError(f"Unknown metric: {rule.metric}")


def evaluate_rules(ds: Dataset, rules: list[Rule]) -> list[FiredRule]:
    fired: list[FiredRule] = []

    for rule in rules:
        result = _get_metric_series_or_value(rule, ds)
        impact_fn = IMPACT_FNS[rule.id]

        if rule.scope == "global":
            value = float(result)
            if _passes(value, rule.comparator, rule.threshold):
                fired.append(FiredRule(
                    rule_id=rule.id,
                    description=rule.description,
                    scope=rule.scope,
                    key="",
                    metric_value=value,
                    threshold=rule.threshold,
                    dollar_impact=impact_fn(ds, "", value),
                    recommendation_template_id=rule.recommendation_template_id,
                ))
        else:
            for key, value in result.items():
                if _passes(float(value), rule.comparator, rule.threshold):
                    fired.append(FiredRule(
                        rule_id=rule.id,
                        description=rule.description,
                        scope=rule.scope,
                        key=str(key),
                        metric_value=float(value),
                        threshold=rule.threshold,
                        dollar_impact=impact_fn(ds, str(key), float(value)),
                        recommendation_template_id=rule.recommendation_template_id,
                    ))

    fired.sort(key=lambda f: f.dollar_impact, reverse=True)
    return fired


def run_rules(ds: Dataset, rules_path: Path) -> list[FiredRule]:
    rules = load_rules(rules_path)
    return evaluate_rules(ds, rules)

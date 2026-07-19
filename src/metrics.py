"""
src/metrics.py

The 7 core metrics, computed deterministically with pandas.
No LLM anywhere in this file — this is the credibility layer of the
whole product. Every function here must be provably correct, which is
why tests/test_metrics.py checks these against hand-calculated values,
not just "does it run."
"""

from __future__ import annotations

import pandas as pd

from src.schema import Invoice, Job, Quote, TimeEntry


# ---------------------------------------------------------------------------
# Helpers: convert lists of Pydantic models into DataFrames
# ---------------------------------------------------------------------------

def _jobs_df(jobs: list[Job]) -> pd.DataFrame:
    return pd.DataFrame([j.model_dump() for j in jobs])


def _quotes_df(quotes: list[Quote]) -> pd.DataFrame:
    return pd.DataFrame([q.model_dump() for q in quotes])


def _invoices_df(invoices: list[Invoice]) -> pd.DataFrame:
    return pd.DataFrame([i.model_dump() for i in invoices])


def _time_entries_df(time_entries: list[TimeEntry]) -> pd.DataFrame:
    return pd.DataFrame([t.model_dump() for t in time_entries])


# ---------------------------------------------------------------------------
# 1. Gross margin by job type
# ---------------------------------------------------------------------------

def gross_margin_by_job_type(jobs: list[Job]) -> pd.Series:
    """
    (sum(revenue) - sum(labor_cost) - sum(material_cost)) / sum(revenue)
    grouped by job_type, non-callback jobs only.
    Returns a Series indexed by job_type.
    """
    df = _jobs_df(jobs)
    df = df[~df["is_callback"]]
    grouped = df.groupby("job_type").agg(
        revenue=("revenue", "sum"),
        labor_cost=("labor_cost", "sum"),
        material_cost=("material_cost", "sum"),
    )
    margin = (grouped["revenue"] - grouped["labor_cost"] - grouped["material_cost"]) / grouped["revenue"]
    margin.name = "gross_margin"
    return margin


# ---------------------------------------------------------------------------
# 2. Callback rate
# ---------------------------------------------------------------------------

def callback_rate_by_tech(jobs: list[Job]) -> pd.Series:
    """Sum(is_callback) / count(jobs) per tech_id."""
    df = _jobs_df(jobs)
    rate = df.groupby("tech_id")["is_callback"].mean()
    rate.name = "callback_rate"
    return rate


def callback_rate_by_job_type(jobs: list[Job]) -> pd.Series:
    """Sum(is_callback) / count(jobs) per job_type."""
    df = _jobs_df(jobs)
    rate = df.groupby("job_type")["is_callback"].mean()
    rate.name = "callback_rate"
    return rate


def callback_rate_vs_team_median(jobs: list[Job]) -> pd.DataFrame:
    """Per-tech callback rate alongside the team median, for easy comparison."""
    by_tech = callback_rate_by_tech(jobs)
    median = by_tech.median()
    return pd.DataFrame({
        "callback_rate": by_tech,
        "team_median": median,
        "delta": by_tech - median,
    })


# ---------------------------------------------------------------------------
# 3. Tech utilization
# ---------------------------------------------------------------------------

def tech_utilization(time_entries: list[TimeEntry]) -> pd.Series:
    """Sum(billable_hours) / sum(paid_hours) per tech_id, across all weeks."""
    df = _time_entries_df(time_entries)
    grouped = df.groupby("tech_id").agg(
        billable=("billable_hours", "sum"),
        paid=("paid_hours", "sum"),
    )
    util = grouped["billable"] / grouped["paid"]
    util.name = "utilization"
    return util


def tech_utilization_weekly(time_entries: list[TimeEntry]) -> pd.DataFrame:
    """Weekly utilization per tech_id (billable_hours / paid_hours), not summed."""
    df = _time_entries_df(time_entries)
    df["utilization"] = df["billable_hours"] / df["paid_hours"]
    return df[["tech_id", "week_start", "utilization"]]


# ---------------------------------------------------------------------------
# 4. Unsold estimate value + win rate
# ---------------------------------------------------------------------------

def unsold_estimate_value(quotes: list[Quote]) -> pd.Series:
    """Sum(amount) where status == 'open', by job_type."""
    df = _quotes_df(quotes)
    open_df = df[df["status"] == "open"]
    value = open_df.groupby("job_type")["amount"].sum()
    value.name = "unsold_value"
    return value


def win_rate_by_job_type(quotes: list[Quote]) -> pd.Series:
    """won / (won + lost) by job_type. 'open' quotes excluded from the denominator."""
    df = _quotes_df(quotes)
    closed = df[df["status"].isin(["won", "lost"])]
    rate = closed.groupby("job_type")["status"].apply(lambda s: (s == "won").mean())
    rate.name = "win_rate"
    return rate


# ---------------------------------------------------------------------------
# 5. Pricing consistency (coefficient of variation)
# ---------------------------------------------------------------------------

def pricing_consistency(jobs: list[Job], min_n: int = 20) -> pd.Series:
    """
    Coefficient of variation (std / mean) of revenue per job_type,
    non-callback jobs only, only for job types with n >= min_n.
    Higher CoV = less consistent / more improvised pricing.
    """
    df = _jobs_df(jobs)
    df = df[~df["is_callback"]]
    grouped = df.groupby("job_type")["revenue"]
    counts = grouped.count()
    cov = grouped.std() / grouped.mean()
    cov = cov[counts >= min_n]
    cov.name = "coefficient_of_variation"
    return cov


# ---------------------------------------------------------------------------
# 6. Avg ticket / revenue per tech-day
# ---------------------------------------------------------------------------

def avg_ticket(jobs: list[Job]) -> float:
    """Mean revenue per job, across all jobs."""
    df = _jobs_df(jobs)
    return float(df["revenue"].mean())


def revenue_per_tech_day(jobs: list[Job]) -> pd.Series:
    """
    Sum(revenue) / count of distinct (tech_id, date) pairs, trended monthly.
    Returns a Series indexed by month (YYYY-MM).
    """
    df = _jobs_df(jobs)
    df["date"] = pd.to_datetime(df["date"])
    df["month"] = df["date"].dt.to_period("M").astype(str)

    def _per_month(g: pd.DataFrame) -> float:
        tech_days = g[["tech_id", "date"]].drop_duplicates().shape[0]
        return g["revenue"].sum() / tech_days if tech_days else 0.0

    result = df.groupby("month").apply(_per_month, include_groups=False)
    result.name = "revenue_per_tech_day"
    return result


# ---------------------------------------------------------------------------
# 7. Cash cycle
# ---------------------------------------------------------------------------

def cash_cycle_by_segment(invoices: list[Invoice]) -> pd.Series:
    """Mean (paid_date - invoice_date) in days, by segment. Unpaid invoices excluded."""
    df = _invoices_df(invoices)
    df = df[df["paid_date"].notna()].copy()
    df["invoice_date"] = pd.to_datetime(df["invoice_date"])
    df["paid_date"] = pd.to_datetime(df["paid_date"])
    df["days_to_pay"] = (df["paid_date"] - df["invoice_date"]).dt.days
    cycle = df.groupby("segment")["days_to_pay"].mean()
    cycle.name = "avg_days_to_pay"
    return cycle


def ar_outstanding(invoices: list[Invoice]) -> float:
    """Sum(amount) where paid_date is null — total accounts receivable outstanding."""
    df = _invoices_df(invoices)
    return float(df[df["paid_date"].isna()]["amount"].sum())

"""
src/report.py

Fills templates/report.html with the scorecard + narrated findings,
then renders it to PDF with WeasyPrint. This file is also the sales
deck per the build guide -- worth the visual investment.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import NamedTuple

from jinja2 import Environment, FileSystemLoader
from weasyprint import HTML

from src.load import Dataset
from src.narrate import Narration
from src.rules import FiredRule
from src import metrics as m

TEMPLATE_DIR = Path(__file__).parent.parent / "templates"


class NarratedFinding(NamedTuple):
    rule: FiredRule
    narration: Narration


def _severity(rank: int) -> str:
    if rank <= 2:
        return "high"
    if rank <= 4:
        return "med"
    return "low"


def build_scorecard(ds: Dataset) -> list[dict]:
    """Shop-wide headline tiles -- one representative number per metric category."""
    tiles = []

    margins = m.gross_margin_by_job_type(ds.jobs)
    worst_margin = margins.min()
    tiles.append({
        "label": "Weakest Job-Type Margin",
        "value": f"{worst_margin:.1%}",
        "status": "flag" if worst_margin < 0 else "ok",
        "sub": margins.idxmin(),
    })

    callbacks = m.callback_rate_by_tech(ds.jobs)
    worst_callback = callbacks.max()
    tiles.append({
        "label": "Highest Callback Rate",
        "value": f"{worst_callback:.1%}",
        "status": "flag" if worst_callback > 0.15 else "ok",
        "sub": callbacks.idxmax(),
    })

    util = m.tech_utilization(ds.time_entries)
    worst_util = util.min()
    tiles.append({
        "label": "Lowest Utilization",
        "value": f"{worst_util:.1%}",
        "status": "flag" if worst_util < 0.55 else "ok",
        "sub": util.idxmin(),
    })

    win_rates = m.win_rate_by_job_type(ds.quotes)
    worst_win = win_rates.min()
    tiles.append({
        "label": "Weakest Win Rate",
        "value": f"{worst_win:.1%}",
        "status": "flag" if worst_win < 0.20 else "ok",
        "sub": win_rates.idxmin(),
    })

    pricing = m.pricing_consistency(ds.jobs)
    if len(pricing) > 0:
        worst_cov = pricing.max()
        tiles.append({
            "label": "Least Consistent Pricing",
            "value": f"{worst_cov:.2f}",
            "status": "flag" if worst_cov > 0.48 else "ok",
            "sub": f"{pricing.idxmax()} (CoV)",
        })
    else:
        tiles.append({"label": "Pricing Consistency", "value": "N/A", "status": "", "sub": "insufficient data"})

    cash_cycle = m.cash_cycle_by_segment(ds.invoices)
    worst_cycle = cash_cycle.max()
    tiles.append({
        "label": "Slowest Cash Cycle",
        "value": f"{worst_cycle:.0f}d",
        "status": "flag" if worst_cycle > 30 else "ok",
        "sub": cash_cycle.idxmax(),
    })

    ar = m.ar_outstanding(ds.invoices)
    tiles.append({
        "label": "AR Outstanding",
        "value": f"${ar:,.0f}",
        "status": "flag" if ar > 500 else "ok",
        "sub": "total unpaid",
    })

    avg_ticket = m.avg_ticket(ds.jobs)
    tiles.append({
        "label": "Average Ticket",
        "value": f"${avg_ticket:,.0f}",
        "status": "",
        "sub": "all jobs",
    })

    return tiles


def build_findings_context(narrated: list[NarratedFinding]) -> tuple[list[dict], float]:
    findings = []
    total = 0.0
    for i, nf in enumerate(narrated, start=1):
        total += nf.rule.dollar_impact
        findings.append({
            "rank": i,
            "rule_id": nf.rule.rule_id,
            "key": nf.rule.key or "Shop-wide",
            "finding": nf.narration.finding,
            "why_it_matters": nf.narration.why_it_matters,
            "action": nf.narration.action,
            "how_measured": nf.narration.how_measured,
            "dollar_impact": nf.rule.dollar_impact,
            "severity": _severity(i),
        })
    return findings, total


def render_report(
    ds: Dataset,
    narrated: list[NarratedFinding],
    shop_name: str,
    report_period: str,
    output_path: Path,
) -> Path:
    findings, total_opportunity = build_findings_context(narrated)
    scorecard = build_scorecard(ds)

    env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)))
    template = env.get_template("report.html")
    html_str = template.render(
        shop_name=shop_name,
        report_period=report_period,
        generated_date=date.today().strftime("%B %d, %Y"),
        scorecard=scorecard,
        findings=findings,
        total_opportunity=total_opportunity,
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    HTML(string=html_str, base_url=str(TEMPLATE_DIR)).write_pdf(str(output_path))
    return output_path

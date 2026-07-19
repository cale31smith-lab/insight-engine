"""
generate_data.py
Generates 12 months of synthetic data for a fictional HVAC/electrical shop
("Summit Heating & Air") matching the normalized schema in the Build Guide.

Usage:
    python generate_data.py --seed 42 --outdir ./synthetic_data

Change --seed (and the PLANTED_* constants below) to generate a second,
differently-seeded shop for the generalization check (Step 9).
"""

import argparse
import csv
import random
from datetime import date, timedelta
from pathlib import Path

# ---------------------------------------------------------------------------
# CONFIG — change these to reseed / re-plant issues for a second test shop
# ---------------------------------------------------------------------------
NUM_TECHS = 10
NUM_CUSTOMERS = 420
NUM_JOBS = 1553
NUM_QUOTES = 710
START_DATE = date(2025, 7, 1)
END_DATE = date(2026, 6, 30)

JOB_TYPES = [
    "AC Repair", "AC Install", "Furnace Repair", "Furnace Install",
    "Electrical Panel Upgrade", "Wiring Repair", "Maintenance", "Diagnostic",
]
SEGMENTS = ["Residential", "Property Management", "Commercial"]

# --- The 6 planted issues live here, in one place, on purpose -------------
PLANTED = {
    "money_losing_job_type": "Furnace Install",       # negative gross margin
    "high_callback_tech": "T04",                      # ~18% vs ~5% median
    "underutilized_tech": "T09",                      # ~42% vs ~78% median
    "bad_quote_type": "Electrical Panel Upgrade",      # ~12% win rate vs ~55%
    "improvised_pricing_type": "AC Repair",            # high CoV in revenue
    "slow_pay_segment": "Property Management",         # ~55 day cash cycle
}


def daterange_days(start, end):
    return (end - start).days


def random_date(start, end, rng):
    span = daterange_days(start, end)
    return start + timedelta(days=rng.randint(0, span))


def gen_technicians(rng):
    rows = []
    roles = ["HVAC Tech", "Electrician", "Apprentice"]
    for i in range(1, NUM_TECHS + 1):
        tech_id = f"T{i:02d}"
        role = rng.choice(roles)
        cost = round(rng.uniform(28, 55), 2)
        rows.append({
            "tech_id": tech_id,
            "name": f"Tech {i}",
            "role": role,
            "loaded_hourly_cost": cost,
        })
    return rows


def gen_customers(rng):
    rows = []
    # weight segments roughly like a real shop: mostly residential
    weights = [0.65, 0.20, 0.15]
    for i in range(1, NUM_CUSTOMERS + 1):
        seg = rng.choices(SEGMENTS, weights=weights, k=1)[0]
        rows.append({"customer_id": f"C{i:04d}", "segment": seg})
    return rows


def base_price(job_type, rng):
    """Realistic base revenue ranges per job type, non-callback."""
    ranges = {
        "AC Repair": (150, 650),
        "AC Install": (3500, 9500),
        "Furnace Repair": (180, 700),
        "Furnace Install": (3200, 8800),
        "Electrical Panel Upgrade": (1800, 4500),
        "Wiring Repair": (200, 900),
        "Maintenance": (89, 220),
        "Diagnostic": (75, 150),
    }
    lo, hi = ranges[job_type]
    return rng.uniform(lo, hi)


def gen_jobs(rng, tech_ids, customer_rows):
    rows = []
    customer_ids = [c["customer_id"] for c in customer_rows]
    tech_median_callback = 0.055  # team baseline ~5.5%

    for i in range(1, NUM_JOBS + 1):
        job_id = f"J{i:05d}"
        d = random_date(START_DATE, END_DATE, rng)
        job_type = rng.choice(JOB_TYPES)
        tech_id = rng.choice(tech_ids)
        customer_id = rng.choice(customer_ids)

        revenue = base_price(job_type, rng)

        # --- planted: pricing inconsistency on AC Repair ---
        if job_type == PLANTED["improvised_pricing_type"]:
            # wide, erratic multiplier -> high coefficient of variation
            revenue *= rng.uniform(0.5, 2.6)

        labor_hours = round(rng.uniform(1, 8), 1)

        # --- planted: money-losing job type ---
        if job_type == PLANTED["money_losing_job_type"]:
            # push cost well above revenue
            labor_cost = revenue * rng.uniform(0.55, 0.75)
            material_cost = revenue * rng.uniform(0.55, 0.80)
        else:
            labor_cost = revenue * rng.uniform(0.20, 0.35)
            material_cost = revenue * rng.uniform(0.10, 0.30)

        # --- planted: high callback rate for one tech ---
        if tech_id == PLANTED["high_callback_tech"]:
            is_callback = 1 if rng.random() < 0.18 else 0
        else:
            is_callback = 1 if rng.random() < tech_median_callback else 0

        rows.append({
            "job_id": job_id,
            "date": d.isoformat(),
            "customer_id": customer_id,
            "tech_id": tech_id,
            "job_type": job_type,
            "revenue": round(revenue, 2),
            "labor_hours": labor_hours,
            "labor_cost": round(labor_cost, 2),
            "material_cost": round(material_cost, 2),
            "is_callback": is_callback,
            "parent_job_id": "",  # left blank for MVP; callbacks not linked to a parent yet
        })
    return rows


def gen_quotes(rng, tech_ids, customer_rows):
    rows = []
    customer_ids = [c["customer_id"] for c in customer_rows]
    for i in range(1, NUM_QUOTES + 1):
        quote_id = f"Q{i:05d}"
        d = random_date(START_DATE, END_DATE, rng)
        job_type = rng.choice(JOB_TYPES)
        tech_id = rng.choice(tech_ids)
        customer_id = rng.choice(customer_ids)
        amount = round(base_price(job_type, rng), 2)

        # --- planted: quote type that doesn't close ---
        if job_type == PLANTED["bad_quote_type"]:
            status = rng.choices(
                ["won", "lost", "open"], weights=[0.12, 0.68, 0.20], k=1
            )[0]
        else:
            status = rng.choices(
                ["won", "lost", "open"], weights=[0.55, 0.30, 0.15], k=1
            )[0]

        rows.append({
            "quote_id": quote_id,
            "date": d.isoformat(),
            "customer_id": customer_id,
            "tech_id": tech_id,
            "job_type": job_type,
            "amount": amount,
            "status": status,
        })
    return rows


def gen_invoices(rng, jobs, customer_segment_map):
    rows = []
    for i, job in enumerate(jobs, start=1):
        invoice_id = f"INV{i:05d}"
        invoice_date = date.fromisoformat(job["date"])
        segment = customer_segment_map[job["customer_id"]]

        # --- planted: slow-paying segment ---
        if segment == PLANTED["slow_pay_segment"]:
            pay_delay = int(rng.gauss(55, 15))
        else:
            pay_delay = int(rng.gauss(9, 6))
        pay_delay = max(pay_delay, 0)

        # ~8% of invoices remain unpaid (AR outstanding) regardless of segment
        if rng.random() < 0.08:
            paid_date = ""
        else:
            paid_date = (invoice_date + timedelta(days=pay_delay)).isoformat()

        rows.append({
            "invoice_id": invoice_id,
            "job_id": job["job_id"],
            "customer_id": job["customer_id"],
            "segment": segment,
            "invoice_date": invoice_date.isoformat(),
            "paid_date": paid_date,
            "amount": job["revenue"],
        })
    return rows


def gen_time_entries(rng, tech_ids):
    rows = []
    week = START_DATE
    weeks = []
    while week <= END_DATE:
        weeks.append(week)
        week += timedelta(days=7)

    for tech_id in tech_ids:
        # --- planted: underutilized tech ---
        if tech_id == PLANTED["underutilized_tech"]:
            util_center = 0.42
        else:
            util_center = 0.78

        for w in weeks:
            paid_hours = round(rng.uniform(36, 42), 1)
            util = max(0.15, min(0.98, rng.gauss(util_center, 0.08)))
            billable_hours = round(paid_hours * util, 1)
            rows.append({
                "tech_id": tech_id,
                "week_start": w.isoformat(),
                "paid_hours": paid_hours,
                "billable_hours": billable_hours,
            })
    return rows


def write_csv(rows, path, fieldnames):
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_answer_key(path, jobs, invoices):
    # compute a couple of real summary numbers so the answer key is grounded
    fi_jobs = [j for j in jobs if j["job_type"] == PLANTED["money_losing_job_type"]]
    fi_margin = sum(
        j["revenue"] - j["labor_cost"] - j["material_cost"] for j in fi_jobs
    ) / max(sum(j["revenue"] for j in fi_jobs), 1)

    pm_invoices = [i for i in invoices if i["segment"] == PLANTED["slow_pay_segment"]]
    pm_paid = [i for i in pm_invoices if i["paid_date"]]
    avg_cycle = sum(
        (date.fromisoformat(i["paid_date"]) - date.fromisoformat(i["invoice_date"])).days
        for i in pm_paid
    ) / max(len(pm_paid), 1)

    content = f"""# ANSWER KEY — Summit Heating & Air (synthetic)

Six planted issues. Do not read this until after your blind-test run (Step 8).

1. **Money-losing job type**: `{PLANTED['money_losing_job_type']}`
   Approx. gross margin: {fi_margin:.1%} (should be strongly negative)

2. **High-callback tech**: `{PLANTED['high_callback_tech']}`
   Target callback rate: ~18% vs team median ~5.5%

3. **Under-utilized tech**: `{PLANTED['underutilized_tech']}`
   Target utilization: ~42% vs team median ~78%

4. **Quote type that doesn't close**: `{PLANTED['bad_quote_type']}`
   Target win rate: ~12% vs other types ~55%

5. **Improvised pricing / inconsistent pricing type**: `{PLANTED['improvised_pricing_type']}`
   Revenue multiplier randomized 0.5x-2.6x -> high coefficient of variation vs other job types

6. **Slow-paying segment**: `{PLANTED['slow_pay_segment']}`
   Approx. avg cash cycle: {avg_cycle:.0f} days vs other segments ~9 days

Scoring target: find >=5 of 6, dollar figures within +/-15% of what's shown above
(re-derive dollar impact from the CSVs, not from this file), zero false positives.
"""
    with open(path, "w") as f:
        f.write(content)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--outdir", type=str, default="./synthetic_data")
    args = parser.parse_args()

    rng = random.Random(args.seed)
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    technicians = gen_technicians(rng)
    tech_ids = [t["tech_id"] for t in technicians]

    customers = gen_customers(rng)
    customer_segment_map = {c["customer_id"]: c["segment"] for c in customers}

    jobs = gen_jobs(rng, tech_ids, customers)
    quotes = gen_quotes(rng, tech_ids, customers)
    invoices = gen_invoices(rng, jobs, customer_segment_map)
    time_entries = gen_time_entries(rng, tech_ids)

    write_csv(technicians, outdir / "technicians.csv",
              ["tech_id", "name", "role", "loaded_hourly_cost"])
    write_csv(customers, outdir / "customers.csv",
              ["customer_id", "segment"])
    write_csv(jobs, outdir / "jobs.csv",
              ["job_id", "date", "customer_id", "tech_id", "job_type", "revenue",
               "labor_hours", "labor_cost", "material_cost", "is_callback", "parent_job_id"])
    write_csv(quotes, outdir / "quotes.csv",
              ["quote_id", "date", "customer_id", "tech_id", "job_type", "amount", "status"])
    write_csv(invoices, outdir / "invoices.csv",
              ["invoice_id", "job_id", "customer_id", "segment", "invoice_date", "paid_date", "amount"])
    write_csv(time_entries, outdir / "time_entries.csv",
              ["tech_id", "week_start", "paid_hours", "billable_hours"])

    write_answer_key(outdir / "ANSWER_KEY.md", jobs, invoices)

    print(f"Generated synthetic data (seed={args.seed}) in {outdir.resolve()}")
    print(f"  technicians.csv   {len(technicians)} rows")
    print(f"  customers.csv     {len(customers)} rows")
    print(f"  jobs.csv          {len(jobs)} rows")
    print(f"  quotes.csv        {len(quotes)} rows")
    print(f"  invoices.csv      {len(invoices)} rows")
    print(f"  time_entries.csv  {len(time_entries)} rows")
    print(f"  ANSWER_KEY.md     written")


if __name__ == "__main__":
    main()

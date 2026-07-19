from pathlib import Path
from src.load import load_all
from src.rules import run_rules
from src.narrate import narrate_finding

ds = load_all(Path("synthetic_data"))
fired = run_rules(ds, Path("rules.yaml"))

top = fired[0]
print(f"Narrating: {top.rule_id} ({top.key})")
print(f"Metric value: {top.metric_value}, Dollar impact: ${top.dollar_impact:,.2f}")
print()

narration = narrate_finding(top)
print("FINDING:", narration.finding)
print("WHY IT MATTERS:", narration.why_it_matters)
print("ACTION:", narration.action)
print("HOW MEASURED:", narration.how_measured)
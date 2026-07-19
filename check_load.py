from pathlib import Path
from src.load import load_all

ds = load_all(Path("synthetic_data"))
print("technicians:", len(ds.technicians))
print("customers:", len(ds.customers))
print("jobs:", len(ds.jobs))
print("quotes:", len(ds.quotes))
print("invoices:", len(ds.invoices))
print("time_entries:", len(ds.time_entries))
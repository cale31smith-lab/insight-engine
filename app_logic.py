"""
app_logic.py

Pure logic pulled out of app.py so it's unit-testable without a running
Streamlit session (Streamlit's UI calls only work inside `streamlit run`).
Keep any new non-UI decision logic for app.py in here, not inline in the
script body.
"""

ENTITIES = ["technicians", "customers", "jobs", "quotes", "invoices", "time_entries"]


def missing_and_duplicate_entities(
    file_entity: dict[str, str | None]
) -> tuple[list[str], set[str]]:
    """
    Given {filename: assigned_entity_or_None}, return:
      - missing: entities in ENTITIES with no file assigned to them yet
      - duplicates: entities assigned to more than one file (user error)
    """
    assigned = [e for e in file_entity.values() if e]
    duplicates = {e for e in assigned if assigned.count(e) > 1}
    missing = [e for e in ENTITIES if e not in assigned]
    return missing, duplicates


def unresolved_entities(mapped_dfs: dict) -> list[str]:
    """Entities that don't yet have a confirmed, column-mapped DataFrame."""
    return [e for e in ENTITIES if e not in mapped_dfs]

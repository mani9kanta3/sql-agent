"""
Noticing when the database stops matching what the agent believes.

This is the failure an offline evaluation cannot catch, and the guide is
right that it is the most likely way this project breaks quietly after
deployment.

The agent answers from `app/table_notes.py` and from the vectors in
`data/schema_index.json`. Both were written against the schema as it was
on the day I built the index. A migration that renames `bills.total_amt`
to `bills.net_amount` does not break anything loudly: the index still
retrieves the right table, the description still reads well, and the
model writes a query against a column that no longer exists. That comes
back as `unknown_column`, the repair loop widens retrieval and tries
again, fails again, and the agent gives up. Every question about revenue
starts failing, and nothing in the code changed.

The signal in the query log is mean attempts drifting upward. The cause
is here, and this check names it directly.

So `build_index()` writes a snapshot of the live schema next to the
vectors, and this compares that snapshot to the database as it is now.
Fifty lines and a cron job.

    python -m scripts.check_schema_drift

Exit code 0 when the schema matches, 1 when it has moved, so it can be
wired to an alert without anyone parsing the output.
"""

import json

from . import config, db


def snapshot():
    """
    The current shape of the database, as a plain comparable dict.

    Only names and types. Row counts and indexes change constantly and
    tell the model nothing, so including them would make this alert on
    noise, and an alert that cries wolf gets switched off.
    """
    shape = {}
    for table in db.list_table_names():
        shape[table] = {
            column["column_name"]: _type_key(column)
            for column in db.columns_of(table)
        }
    return shape


def _type_key(column):
    """
    A short type string, with the parts that matter to a query.

    The length of a VARCHAR is deliberately left out. Widening a column
    from 60 to 100 characters breaks nothing and would be noise. Changing
    it to an integer breaks everything, and that shows up here.
    """
    data_type = column["data_type"]
    if data_type == "numeric" and column["numeric_precision"]:
        return f"numeric({column['numeric_precision']},{column['numeric_scale']})"
    return data_type


def save_snapshot(path=None):
    """Write the current schema shape beside the vectors."""
    path = path or config.SCHEMA_SNAPSHOT_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(snapshot(), indent=2, sort_keys=True), encoding="utf-8")
    return path


def load_snapshot(path=None):
    """The schema as it was when the index was last built, or None."""
    path = path or config.SCHEMA_SNAPSHOT_PATH
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def compare(before, after):
    """
    What changed between two snapshots.

    Returns a dict of lists. Empty everywhere means nothing moved.
    """
    changes = {
        "tables_added": [],
        "tables_removed": [],
        "columns_added": [],
        "columns_removed": [],
        "types_changed": [],
    }

    before_tables = set(before)
    after_tables = set(after)

    changes["tables_added"] = sorted(after_tables - before_tables)
    changes["tables_removed"] = sorted(before_tables - after_tables)

    for table in sorted(before_tables & after_tables):
        old_columns = before[table]
        new_columns = after[table]

        for column in sorted(set(new_columns) - set(old_columns)):
            changes["columns_added"].append(f"{table}.{column}")

        for column in sorted(set(old_columns) - set(new_columns)):
            changes["columns_removed"].append(f"{table}.{column}")

        for column in sorted(set(old_columns) & set(new_columns)):
            if old_columns[column] != new_columns[column]:
                changes["types_changed"].append(
                    f"{table}.{column}: {old_columns[column]} -> {new_columns[column]}"
                )

    return changes


def has_drifted(changes):
    """True if anything at all moved."""
    return any(changes.values())


def check():
    """
    Compare the live database to the snapshot.

    Returns (drifted, changes, message). A missing snapshot is not drift
    and not an error; it means the index has never been built, which the
    health endpoint already reports separately.
    """
    before = load_snapshot()
    if before is None:
        return False, {}, "No schema snapshot yet. Run scripts/build_schema_index.py."

    changes = compare(before, snapshot())

    if not has_drifted(changes):
        return False, changes, "Schema matches the snapshot the descriptions were written from."

    lines = []
    for label, items in changes.items():
        if items:
            lines.append(f"{label.replace('_', ' ')}: {', '.join(items)}")

    return True, changes, (
        "Schema has drifted since the descriptions were written. "
        + " | ".join(lines)
        + ". Update app/table_notes.py and re-run scripts/build_schema_index.py."
    )


if __name__ == "__main__":
    drifted, _changes, message = check()
    print(message)

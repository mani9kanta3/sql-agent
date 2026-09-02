"""
Alert when the database stops matching what the agent believes.

    python -m scripts.check_schema_drift

Exit code 0 when the schema matches the snapshot, 1 when it has moved, so
this can go straight into cron or a CI step without anyone parsing the
output:

    0 6 * * *  cd /app/backend && python -m scripts.check_schema_drift

This is the check the guide calls the single most likely way the project
breaks silently after deployment, and I agree with the reasoning. A
migration renames a column. Nothing errors at deploy time. The vectors in
data/schema_index.json still retrieve the right table, the description in
table_notes.py still reads well, and the model writes a query against a
column that no longer exists. Every question about revenue starts coming
back as unknown_column, the repair loop widens retrieval, fails again,
and gives up.

The offline evaluation cannot catch it, because the evaluation runs
against the schema it was written on. The query log shows the symptom,
which is mean attempts drifting upward. This names the cause.
"""

import sys

from app import console, drift


def main():
    console.use_utf8()

    drifted, changes, message = drift.check()

    print(message)

    if drifted:
        # Printed as a list as well as a sentence, because the sentence
        # is for a human reading an alert and the list is for whoever has
        # to go and fix the descriptions.
        print()
        for label, items in changes.items():
            for item in items:
                print(f"  {label.replace('_', ' '):18} {item}")

    return 1 if drifted else 0


if __name__ == "__main__":
    sys.exit(main())

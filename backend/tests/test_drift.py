"""
The schema drift check.

This is the failure the offline evaluation structurally cannot catch: the
eval runs against the schema it was written on, so a migration that
renames a column never shows up there. It shows up in production as every
revenue question suddenly failing with unknown_column, and in the query
log as mean attempts drifting upward.

The comparison itself is pure dictionary work, so most of this needs no
database at all.
"""

import pytest

from app import drift

BEFORE = {
    "bills": {"bill_id": "integer", "total_amt": "numeric(12,2)", "status": "character varying"},
    "products": {"prod_id": "integer", "prod_name": "character varying"},
}


def test_an_unchanged_schema_is_not_drift():
    changes = drift.compare(BEFORE, BEFORE)

    assert not drift.has_drifted(changes)
    assert changes["columns_removed"] == []


def test_a_renamed_column_shows_as_one_gone_and_one_new():
    """
    The case that motivates the whole file. A rename is invisible to the
    database as a rename; it is a drop and an add, and either half is
    enough to break the descriptions.
    """
    after = {
        "bills": {"bill_id": "integer", "net_amount": "numeric(12,2)", "status": "character varying"},
        "products": BEFORE["products"],
    }
    changes = drift.compare(BEFORE, after)

    assert drift.has_drifted(changes)
    assert "bills.total_amt" in changes["columns_removed"]
    assert "bills.net_amount" in changes["columns_added"]


def test_a_changed_type_is_caught():
    after = {
        "bills": {**BEFORE["bills"], "status": "integer"},
        "products": BEFORE["products"],
    }
    changes = drift.compare(BEFORE, after)

    assert drift.has_drifted(changes)
    assert any("bills.status" in item for item in changes["types_changed"])


def test_a_dropped_table_is_caught():
    changes = drift.compare(BEFORE, {"bills": BEFORE["bills"]})

    assert drift.has_drifted(changes)
    assert changes["tables_removed"] == ["products"]


def test_a_new_table_is_caught():
    after = {**BEFORE, "returns": {"return_id": "integer"}}
    changes = drift.compare(BEFORE, after)

    assert drift.has_drifted(changes)
    assert changes["tables_added"] == ["returns"]


def test_a_widened_varchar_is_not_reported():
    """
    Deliberately not drift. Growing a name column from 60 to 100
    characters breaks nothing, and an alert that fires on harmless
    migrations is an alert somebody switches off. Only the type itself
    is compared, not its length.
    """
    column = {
        "column_name": "supp_name",
        "data_type": "character varying",
        "character_maximum_length": 100,
        "numeric_precision": None,
        "numeric_scale": None,
    }
    narrower = {**column, "character_maximum_length": 60}

    assert drift._type_key(column) == drift._type_key(narrower)


def test_numeric_precision_is_part_of_the_type():
    """
    The opposite case. Money going from NUMERIC(12,2) to NUMERIC(12,0)
    silently rounds every total, so that one does need to be caught.
    """
    money = {
        "column_name": "total_amt", "data_type": "numeric",
        "character_maximum_length": None, "numeric_precision": 12, "numeric_scale": 2,
    }
    rounded = {**money, "numeric_scale": 0}

    assert drift._type_key(money) != drift._type_key(rounded)


@pytest.mark.usefixtures("live_db")
def test_the_live_schema_matches_the_snapshot():
    """
    Against the real database. If this fails, either the schema changed
    and app/table_notes.py is now describing a database that no longer
    exists, or the snapshot was never built.
    """
    drifted, _changes, message = drift.check()

    assert not drifted, message

"""
The layers that need a real database in front of them.

test_safety.py proves the parser refuses a DELETE. That is layer 3, and
layer 3 is the one I wrote, so it is the one most likely to have a hole
in it. These tests check the two layers underneath, which are the ones
that actually protect the database.

They skip themselves if PostgreSQL is not set up, so a fresh clone still
gets a green run before anything is installed.

Run just these with:  pytest tests/test_database_guards.py -v
"""

import pytest

from app import db, tools

pytestmark = pytest.mark.usefixtures("live_db")


def test_the_role_cannot_write_even_when_the_parser_is_bypassed():
    """
    The test that carries the security argument.

    This goes straight to db.run_readonly and skips safety.check
    entirely, which is exactly what an attacker would want to do. The
    DELETE still fails, because the credential has no permission to run
    it. That is the difference between asking a model nicely and having
    an actual control.
    """
    result, error = db.run_readonly("DELETE FROM bills")

    assert result is None
    assert error["error_type"] == "permission_denied"
    assert error["repairable"] is False


def test_the_role_cannot_create_tables():
    result, error = db.run_readonly("CREATE TABLE should_not_exist (id INT)")

    assert result is None
    assert error["error_type"] == "permission_denied"


def test_the_role_cannot_update():
    result, error = db.run_readonly("UPDATE products SET sell_price = 0")

    assert result is None
    assert error["error_type"] == "permission_denied"


def test_reading_works():
    """The other half. A read only role that cannot read is not useful."""
    result, error = db.run_readonly("SELECT COUNT(*) AS n FROM bills")

    assert error is None
    assert result["rows"][0]["n"] > 0


def test_a_statement_timeout_is_actually_set():
    """
    Layer 2. pg_sleep is blocked by the parser, so this proves the
    timeout a different way: the setting is read back from inside the
    same transaction the query runs in.
    """
    result, error = db.run_readonly("SHOW statement_timeout")

    assert error is None
    value = result["rows"][0]["statement_timeout"]
    assert value in ("5s", "5000ms")


def test_a_limit_is_forced_on_a_query_that_has_none():
    """A question is being answered, not a table exported."""
    from app import config

    outcome = tools.run_query("SELECT * FROM bill_items")

    assert outcome["ok"]
    assert outcome["row_count"] <= config.MAX_ROWS
    assert f"LIMIT {config.MAX_ROWS}" in outcome["sql"].upper()


def test_an_empty_result_comes_back_as_empty_and_not_as_a_failure():
    outcome = tools.run_query("SELECT * FROM bills WHERE bill_no = 'does-not-exist'")

    assert outcome["ok"] is True
    assert outcome["error_type"] == "empty_result"
    assert outcome["row_count"] == 0


def test_an_unknown_column_comes_back_classified_with_the_hint():
    """
    End to end proof of the taxonomy. A real PostgreSQL error, read by
    errors.classify(), arriving as something the repair node can switch
    on rather than a string to parse.
    """
    outcome = tools.run_query("SELECT customer_name FROM bills")

    assert outcome["ok"] is False
    assert outcome["error_type"] == "unknown_column"
    assert outcome["repairable"] is True


def test_the_four_tools_all_answer():
    """A smoke test over the MCP tool surface."""
    listing = tools.list_tables()
    assert listing["ok"]
    assert len(listing["tables"]) >= 15

    described = tools.describe_table("bills")
    assert described["ok"]
    assert "CREATE TABLE bills" in described["ddl"]
    # The nullable foreign key that half the hard questions turn on.
    assert any(column["name"] == "cust_id" and column["nullable"] for column in described["columns"])

    sample = tools.sample_rows("bills", 3)
    assert sample["ok"]
    assert len(sample["rows"]) == 3


def test_the_agent_cannot_read_its_own_query_log():
    """
    The monitoring table lives in the same database, so it turns up in the
    catalogue like any other table. It should not.

    Nobody asking about the shop wants a row from the query log, and an
    agent that can read the log can read every question anyone has ever
    asked it. Checked at both layers, because the first one is only a
    convenience and the second one is the actual control.
    """
    # Layer 3: the parser does not know the table exists.
    assert "agent_query_log" not in tools.known_tables()
    refused = tools.run_query("SELECT * FROM agent_query_log")
    assert refused["error_type"] == "unknown_table"

    # Layer 1: and the grant refuses it even with the parser bypassed.
    result, error = db.run_readonly("SELECT * FROM agent_query_log")
    assert result is None
    assert error["error_type"] == "permission_denied"


def test_the_log_role_can_insert_but_not_read():
    """
    The third credential. Logging needs a write, so rather than loosen the
    role that answers questions it gets its own, which can INSERT into one
    table and do nothing else at all.

    check() inserts a row and rolls it back, so running the tests does not
    fill the log with test rows.
    """
    from app import query_log

    if not query_log.is_enabled():
        pytest.skip("DB_LOG_PASSWORD is blank, logging is off")

    assert query_log.check() is True

    import psycopg2

    from app import config

    connection = psycopg2.connect(
        dbname=config.DB_NAME, user=config.DB_LOG_USER,
        password=config.DB_LOG_PASSWORD, host=config.DB_HOST, port=config.DB_PORT,
    )
    try:
        with connection.cursor() as cursor:
            for forbidden in [
                "SELECT COUNT(*) FROM bills",
                "SELECT COUNT(*) FROM agent_query_log",
                "DELETE FROM agent_query_log",
            ]:
                with pytest.raises(psycopg2.Error):
                    cursor.execute(forbidden)
                connection.rollback()
    finally:
        connection.close()


def test_the_read_only_role_can_still_see_the_keys():
    """
    A regression test for a bug I shipped and only found by reading the
    DDL the tool was producing.

    information_schema.table_constraints only shows constraints on tables
    the user "owns or has some privilege other than SELECT on". Our role
    has SELECT and nothing else, on purpose, so those views came back
    empty for it. Nothing errored. describe_table simply stopped printing
    PRIMARY KEY and REFERENCES, so every prompt lost its foreign keys and
    the model had to guess the joins from column names.

    That is the worst shape of bug: silent, and it degrades the thing the
    project is measured on. db.py now reads pg_catalog, which has no such
    privilege rule, and this test fails immediately if it ever goes back.
    """
    keys = db.foreign_keys_of("bills")
    by_column = {row["column_name"]: row["refers_to_table"] for row in keys}

    assert by_column.get("cust_id") == "customers"
    assert by_column.get("emp_id") == "employees"
    assert db.primary_key_of("bills") == ["bill_id"]

    # And the DDL the model actually sees has to carry them.
    ddl = tools.describe_table("bills")["ddl"]
    assert "PRIMARY KEY" in ddl
    assert "REFERENCES customers(cust_id)" in ddl


def test_the_archive_really_has_no_foreign_keys():
    """
    The other half of the same point. bill_archive is flat by design, so
    an empty list here is correct rather than the bug above showing up
    again. Checking a table that should have keys and a table that should
    not is what separates those two cases.
    """
    assert db.foreign_keys_of("bill_archive") == []
    assert db.foreign_keys_of("bill_items") != []


def test_the_status_values_really_are_upper_case():
    """
    The sample rows tell the model that status is 'PAID'. If the seed
    data ever changed to lower case, every prompt would still be saying
    upper case and the agent would quietly start returning zero rows.
    """
    outcome = tools.run_query("SELECT DISTINCT status FROM bills")

    assert outcome["ok"]
    values = {row["status"] for row in outcome["rows"]}
    assert values <= {"PAID", "PARTIAL", "CANCELLED"}


def test_most_bills_have_no_customer():
    """
    The nullable foreign key is the point of half the hard questions, so
    if the seed data stopped producing walk in bills the eval would get
    easier without anyone noticing.
    """
    outcome = tools.run_query(
        "SELECT COUNT(*) AS total, COUNT(cust_id) AS named FROM bills"
    )

    row = outcome["rows"][0]
    assert row["named"] < row["total"] * 0.5

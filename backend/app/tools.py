"""
The four tools. Everything the agent is allowed to do to the database.

The guide says to put the safety inside the MCP server rather than the
agent, so that it holds no matter what calls the tool. I agree with the
reason and I have done it slightly differently, because putting real
logic inside an MCP entry point would mean the only way to test any of
it is to start a server and speak the protocol at it.

So the tool bodies live here, and mcp_server/server.py is a thin file
that registers these four functions and nothing else. The agent calls
this module directly, in process. Both callers go through exactly the
same run_query(), so the argument the guide is making still holds: there
is no route to the database that skips the checks. And the checks can be
tested by importing a function, which is why tests/test_safety.py is
forty fast tests instead of an integration suite.

Four tools, kept small:

    list_tables()               names and one line descriptions
    describe_table(name)        columns, types, nullability, keys
    sample_rows(name, n)        a few real rows, so values are learned
    run_query(sql)              the envelope, and structured errors
"""

from . import config, db, errors, safety
from .table_notes import note_for

# Tables that belong to the application rather than to the shop.
#
# agent_query_log is the monitoring table this project writes itself. It
# is in the same database, so it turns up in the catalogue like anything
# else, and the first time I added it the schema index went from fifteen
# tables to sixteen and started offering the agent its own log as a thing
# to answer questions from.
#
# That is wrong twice over. Nobody asking about the shop wants a row from
# the query log, and an agent that can read the log can read every
# question anyone has ever asked it, which is a small privacy problem I
# would rather not create. Hiding it here also means safety.check()
# refuses any query that names it, because that check works from this
# same list.
#
# The read only role has SELECT revoked on it as well, so this is a
# convenience rather than the control. The control is the grant.
INTERNAL_TABLES = {"agent_query_log"}

# Filled on first use. The list of table names is needed on every single
# run_query() call, for the check that the query only names real tables,
# and the schema does not change while the process is running.
_table_names = None


def known_tables(refresh=False):
    """The shop's tables, with the application's own tables left out."""
    global _table_names
    if _table_names is None or refresh:
        _table_names = set(db.list_table_names()) - INTERNAL_TABLES
    return _table_names


# ------------------------------------------------------------ tool one


def list_tables():
    """
    Every table, with the sentence explaining what it is for.

    The description is the useful half. A model given only the names has
    to guess what bill_archive holds, and it usually guesses that it is
    a backup it can ignore.
    """
    names = sorted(known_tables())
    return {
        "ok": True,
        "tables": [
            {
                "name": name,
                "description": note_for(name),
                "approx_rows": db.row_count_of(name),
            }
            for name in names
        ],
    }


# ------------------------------------------------------------ tool two


def describe_table(name):
    """
    The shape of one table: columns, types, nullability, keys.

    Written out as CREATE TABLE rather than as a list of columns. The
    model has seen a very large amount of DDL and comparatively little
    of whatever format I would have invented, so giving it the format it
    already knows costs nothing and reads better in a prompt.
    """
    if name not in known_tables():
        return errors.build(
            error_type="unknown_table",
            message=f"There is no table called {name}.",
            hint=f"The tables are: {', '.join(sorted(known_tables()))}.",
        )

    columns = db.columns_of(name)
    primary_key = db.primary_key_of(name)
    foreign_keys = db.foreign_keys_of(name)
    fk_by_column = {row["column_name"]: row for row in foreign_keys}

    lines = [f"CREATE TABLE {name} ("]
    for column in columns:
        parts = [f"    {column['column_name']}", _type_of(column)]

        if column["is_nullable"] == "NO":
            parts.append("NOT NULL")
        if column["column_name"] in primary_key:
            parts.append("PRIMARY KEY")

        link = fk_by_column.get(column["column_name"])
        if link:
            parts.append(f"REFERENCES {link['refers_to_table']}({link['refers_to_column']})")

        lines.append(" ".join(parts) + ",")

    # Trim the comma off the last column so it is valid DDL.
    if len(lines) > 1:
        lines[-1] = lines[-1].rstrip(",")
    lines.append(");")

    return {
        "ok": True,
        "table": name,
        "description": note_for(name),
        "ddl": "\n".join(lines),
        "columns": [
            {
                "name": column["column_name"],
                "type": _type_of(column),
                "nullable": column["is_nullable"] == "YES",
            }
            for column in columns
        ],
        "primary_key": primary_key,
        "foreign_keys": [
            {
                "column": row["column_name"],
                "references": f"{row['refers_to_table']}.{row['refers_to_column']}",
            }
            for row in foreign_keys
        ],
    }


def _type_of(column):
    """
    A readable type, with the size on it where the size matters.

    information_schema splits numeric(12,2) into three separate fields,
    which is correct and unreadable. Putting it back together means the
    model can see that a money column has two decimal places.
    """
    data_type = column["data_type"]

    if data_type == "character varying" and column["character_maximum_length"]:
        return f"VARCHAR({column['character_maximum_length']})"
    if data_type == "character" and column["character_maximum_length"]:
        return f"CHAR({column['character_maximum_length']})"
    if data_type == "numeric" and column["numeric_precision"]:
        return f"NUMERIC({column['numeric_precision']}, {column['numeric_scale']})"
    if data_type == "timestamp without time zone":
        return "TIMESTAMP"
    if data_type == "double precision":
        return "DOUBLE PRECISION"

    return data_type.upper()


# ---------------------------------------------------------- tool three


def sample_rows(name, n=None):
    """
    A few real rows from one table.

    This tool looks like the least important of the four and it is not.
    The DDL says status is VARCHAR(12). It does not say that the shop
    writes 'PAID' and not 'paid'. A query filtering on 'paid' parses,
    validates, runs, takes no time and returns zero rows, and zero rows
    is the hardest failure to notice because nothing went wrong. Three
    sample rows prevent it.

    The LIMIT is put in by hand and the table name is checked against the
    real list first, because a table name cannot be a bound parameter.
    """
    if name not in known_tables():
        return errors.build(
            error_type="unknown_table",
            message=f"There is no table called {name}.",
        )

    n = min(int(n or config.SAMPLE_ROWS), 10)
    result, error = db.run_readonly(f"SELECT * FROM {name} LIMIT {n}")
    if error:
        return error

    return {
        "ok": True,
        "table": name,
        "columns": result["columns"],
        "rows": result["rows"],
    }


# ----------------------------------------------------------- tool four


def run_query(sql):
    """
    Run one SELECT and give back rows, or a structured error.

    This is the only function in the project that executes model written
    SQL, and it is where all three layers meet:

        layer 3, here      sqlglot parses it and refuses anything that
                           is not a single SELECT over real tables, and
                           forces a LIMIT on
        layer 2, db.py     read only session, statement timeout, rollback
        layer 1, Postgres  the role has SELECT and nothing else

    The error that comes back is always a dictionary with an error_type
    on it, never a string and never an exception. That is what lets
    agent.py pick a repair strategy instead of retrying blindly.
    """
    safe_sql, rejection = safety.check(sql, known_tables())
    if rejection:
        # Attach what was attempted. The repair prompt needs to see the
        # query that failed, and without this the model is being asked
        # to fix something it cannot see.
        rejection["sql"] = sql
        return rejection

    result, error = db.run_readonly(safe_sql)
    if error:
        error["sql"] = safe_sql
        return error

    if result["row_count"] == 0:
        # Not treated as a failure. See errors.empty_result() and the
        # inspect_result node in agent.py, which runs one diagnostic
        # before deciding whether the emptiness means anything.
        empty = errors.empty_result(safe_sql)
        empty["columns"] = result["columns"]
        empty["rows"] = []
        empty["row_count"] = 0
        empty["ms"] = result["ms"]
        return empty

    return {
        "ok": True,
        "sql": safe_sql,
        "columns": result["columns"],
        "rows": result["rows"],
        "row_count": result["row_count"],
        "ms": result["ms"],
        # True when the LIMIT we forced on is what stopped it. The answer
        # should say "the first 200" rather than implying that is all
        # there is.
        "truncated": result["row_count"] >= config.MAX_ROWS,
    }


if __name__ == "__main__":
    # python -m app.tools
    listing = list_tables()
    print(f"{len(listing['tables'])} tables\n")

    print(describe_table("bills")["ddl"], "\n")
    print("sample:", sample_rows("bills", 2)["rows"], "\n")

    print("a real query:", run_query("SELECT COUNT(*) AS n FROM bills"))
    print("a write:     ", run_query("DELETE FROM bills")["error_type"])
    print("two at once: ", run_query("SELECT 1; DROP TABLE bills")["error_type"])

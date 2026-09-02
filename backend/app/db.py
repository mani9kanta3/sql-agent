"""
The only way this project talks to the database, and it can only read.

Layer 2 of the envelope lives here. Every single query, including the
schema introspection, goes through run_readonly() and therefore through
all of this:

    * a connection made with DB_RO_USER, which holds SELECT and nothing
      else
    * the session marked read only, so PostgreSQL refuses a write with
      "cannot execute INSERT in a read-only transaction" even if the
      grant were somehow wrong
    * SET LOCAL statement_timeout, so a bad join is killed instead of
      sitting there
    * ROLLBACK at the end rather than COMMIT, always, including on the
      happy path

That last one looks odd for a SELECT because a SELECT has nothing to
commit. It is there because I do not want a commit anywhere in this
file at all. If a write ever did slip through every other layer, there
would still be no line of code that could make it permanent.

There is no connection pool. A question every few seconds does not need
one, and a fresh connection per query means a query that somehow leaves
the session in a strange state cannot affect the next one. If this ever
served real traffic that would be the first thing to change.
"""

import time

import psycopg2
from psycopg2.extras import RealDictCursor

from . import config, errors


def get_connection():
    """
    One new connection as the read only role.

    set_session(readonly=True) is the important line. psycopg2 sends
    "SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY", so every
    transaction on this connection starts read only without me having
    to remember to write BEGIN READ ONLY each time.
    """
    connection = psycopg2.connect(
        dbname=config.DB_NAME,
        user=config.DB_RO_USER,
        password=config.DB_RO_PASSWORD,
        host=config.DB_HOST,
        port=config.DB_PORT,
        sslmode=config.DB_SSLMODE,
        # Named so I can find the agent's connections in pg_stat_activity
        # when something is hanging.
        application_name="sql_agent_ro",
    )
    connection.set_session(readonly=True, autocommit=False)
    return connection


def run_readonly(sql, params=None):
    """
    Run one SELECT inside the envelope.

    Returns (result, None) on success or (None, structured_error) on
    failure. Nothing here raises, because every caller wants to do
    something sensible with the failure rather than crash, and the
    agent's whole design depends on getting an error it can read.

    result is:
        {"rows": [...], "columns": [...], "row_count": n, "ms": n}
    """
    started = time.perf_counter()
    connection = None

    try:
        connection = get_connection()
    except psycopg2.Error as error:
        # Cannot connect at all. Wrong password, database down, role
        # missing. Nothing to repair, so this is not given an error_type
        # the agent will retry on.
        return None, errors.build(
            error_type="unknown",
            message=f"Could not connect to the database: {error}",
            hint="Check DB_RO_USER and DB_RO_PASSWORD in backend/.env.",
        )

    try:
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            # SET LOCAL only lasts for this transaction, so it cannot
            # leak into anything else. Written with a literal because
            # SET does not take a bound parameter, and the value is an
            # int from config, never anything a user typed.
            cursor.execute(f"SET LOCAL statement_timeout = {int(config.STATEMENT_TIMEOUT_MS)}")

            cursor.execute(sql, params if params else None)

            # A statement with no result set, which after safety.check()
            # should be impossible. Handled anyway rather than letting
            # fetchall() raise something unhelpful.
            if cursor.description is None:
                return None, errors.build(
                    error_type="unknown",
                    message="The statement returned no result set.",
                )

            columns = [column.name for column in cursor.description]
            rows = [dict(row) for row in cursor.fetchall()]

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        return {
            "rows": rows,
            "columns": columns,
            "row_count": len(rows),
            "ms": elapsed_ms,
        }, None

    except psycopg2.Error as error:
        # This is where the taxonomy comes from. The exception carries
        # the SQLSTATE, and errors.classify() turns it into one of the
        # seven types the repair loop knows how to answer.
        return None, errors.classify(error)

    finally:
        if connection is not None:
            # Roll back even after a clean SELECT. See the note at the
            # top of the file.
            try:
                connection.rollback()
            except psycopg2.Error:
                pass
            connection.close()


def list_table_names():
    """
    Every table in the public schema, as a plain sorted list.

    safety.check() needs this to know which table names are real, so it
    runs on every query. It is a cheap catalogue read and PostgreSQL
    caches it, but if this ever showed up in a profile the answer would
    be to cache it for the life of the process, since the schema does
    not change while the agent is running.
    """
    sql = """
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = 'public'
          AND table_type = 'BASE TABLE'
        ORDER BY table_name
    """
    result, error = run_readonly(sql)
    if error:
        return []
    return [row["table_name"] for row in result["rows"]]


def columns_of(table_name):
    """
    Column name, type, nullability and default for one table.

    Parameterised even though the table name comes from our own code and
    not from a user. It costs nothing and means this function stays safe
    if it is ever called with something less trustworthy.
    """
    sql = """
        SELECT
            column_name,
            data_type,
            character_maximum_length,
            numeric_precision,
            numeric_scale,
            is_nullable,
            column_default
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = %s
        ORDER BY ordinal_position
    """
    result, error = run_readonly(sql, (table_name,))
    if error:
        return []
    return result["rows"]


# The next two functions read pg_catalog rather than information_schema,
# and that is not a style preference. It is a bug I shipped and then found
# by looking at the DDL the tools were producing.
#
# information_schema.table_constraints only shows constraints on tables
# the current user "owns or has some privilege other than SELECT on".
# Our role has SELECT and deliberately nothing else, so those views come
# back completely empty for it. Nothing errors. describe_table just
# quietly stopped printing PRIMARY KEY and REFERENCES, which meant every
# prompt was missing the foreign keys and the model had to guess the
# joins from column names. On this schema that guess is exactly what
# fails, because bill_archive has no keys at all and tbl_prod_master_old
# has a p_id that means nothing.
#
# pg_catalog has no such privilege rule, so the read only role can read
# it. The queries are uglier and they work.


def foreign_keys_of(table_name):
    """
    Which columns of this table point at which columns of which table.

    The model gets joins wrong far less often when it is shown the keys
    than when it has to infer them from two columns sharing a name.
    """
    sql = """
        SELECT
            att.attname      AS column_name,
            ref_cls.relname  AS refers_to_table,
            ref_att.attname  AS refers_to_column
        FROM pg_constraint con
        JOIN pg_class cls ON cls.oid = con.conrelid
        JOIN pg_namespace nsp ON nsp.oid = cls.relnamespace
        -- conkey and confkey are parallel arrays: the nth column here
        -- points at the nth column there. Unnesting them together keeps
        -- the pairs lined up, which matters for a composite key.
        JOIN LATERAL unnest(con.conkey, con.confkey) AS cols(src, tgt) ON TRUE
        JOIN pg_attribute att
          ON att.attrelid = con.conrelid AND att.attnum = cols.src
        JOIN pg_class ref_cls ON ref_cls.oid = con.confrelid
        JOIN pg_attribute ref_att
          ON ref_att.attrelid = con.confrelid AND ref_att.attnum = cols.tgt
        WHERE con.contype = 'f'
          AND nsp.nspname = 'public'
          AND cls.relname = %s
        ORDER BY att.attname
    """
    result, error = run_readonly(sql, (table_name,))
    if error:
        return []
    return result["rows"]


def primary_key_of(table_name):
    """The primary key columns, so describe_table can mark them."""
    sql = """
        SELECT att.attname AS column_name
        FROM pg_constraint con
        JOIN pg_class cls ON cls.oid = con.conrelid
        JOIN pg_namespace nsp ON nsp.oid = cls.relnamespace
        -- WITH ORDINALITY keeps the columns of a composite key in the
        -- order they were declared in, which is the order that matters.
        JOIN LATERAL unnest(con.conkey) WITH ORDINALITY AS k(attnum, ord) ON TRUE
        JOIN pg_attribute att
          ON att.attrelid = con.conrelid AND att.attnum = k.attnum
        WHERE con.contype = 'p'
          AND nsp.nspname = 'public'
          AND cls.relname = %s
        ORDER BY k.ord
    """
    result, error = run_readonly(sql, (table_name,))
    if error:
        return []
    return [row["column_name"] for row in result["rows"]]


def row_count_of(table_name):
    """
    How many rows the table holds, from the planner's own estimate.

    COUNT(*) on every table would be a full scan each time the schema is
    described. reltuples is an estimate kept by ANALYZE and it is close
    enough for "this table is big, that one has twelve rows", which is
    all the model needs it for.
    """
    sql = """
        SELECT reltuples::BIGINT AS estimate
        FROM pg_class
        WHERE relname = %s
          AND relkind = 'r'
    """
    result, error = run_readonly(sql, (table_name,))
    if error or not result["rows"]:
        return None
    estimate = result["rows"][0]["estimate"]
    # -1 means the table has never been analysed, so there is no estimate.
    return None if estimate is None or estimate < 0 else int(estimate)


if __name__ == "__main__":
    # A smoke test for the connection and the read only rule. Run it
    # with: python -m app.db
    tables = list_table_names()
    print(f"connected as {config.DB_RO_USER}, {len(tables)} tables: {', '.join(tables)}")

    _, error = run_readonly("DELETE FROM bills")
    print(f"DELETE was refused as: {error['error_type']} - {error['message']}")

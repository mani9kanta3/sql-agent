"""
Writing one row per answered question, for monitoring.

Evaluation tells me it worked before I shipped. This tells me when it
stopped. They catch different things, because the eval runs against a
schema frozen when I wrote the questions and production does not.

**This is the only file in app/ that can write to the database, and it
can write exactly one row shape to exactly one table.** It connects as
sql_agent_log, a third role that holds INSERT on agent_query_log and
nothing else: no SELECT anywhere, so it cannot read the shop's data, and
no UPDATE or DELETE, so it cannot alter or erase its own history.

That is deliberate. The whole safety argument rests on the credential
that answers questions being unable to write, and logging is a write. The
answer is a separate narrow credential, not a wider shared one.

Two rules, same as tracing:

**It is optional.** No DB_LOG_PASSWORD in the .env and every function
here does nothing.

**It never breaks a request.** A logging failure is my problem, not the
person's who asked the question, so everything is wrapped and failures
are printed once rather than raised.
"""

import psycopg2

from . import config

_enabled = bool(config.DB_LOG_PASSWORD)
_warned = False


def is_enabled():
    return _enabled


def _connection():
    """A connection as the insert only role."""
    return psycopg2.connect(
        dbname=config.DB_NAME,
        user=config.DB_LOG_USER,
        password=config.DB_LOG_PASSWORD,
        host=config.DB_HOST,
        port=config.DB_PORT,
        sslmode=config.DB_SSLMODE,
        application_name="sql_agent_log",
    )


INSERT = """
    INSERT INTO agent_query_log (
        question, mode, final_sql, attempts_used, error_types, tables_used,
        gave_up, refused, rows_returned, latency_ms, tokens, cost_usd,
        model, trace_id
    ) VALUES (
        %(question)s, %(mode)s, %(final_sql)s, %(attempts_used)s,
        %(error_types)s, %(tables_used)s, %(gave_up)s, %(refused)s,
        %(rows_returned)s, %(latency_ms)s, %(tokens)s, %(cost_usd)s,
        %(model)s, %(trace_id)s
    )
"""


def record(result, mode="agent"):
    """
    Write one answered question to the log.

    result is whatever agent.ask() or baseline.ask() returned, so the
    caller does not have to assemble anything. Nothing is raised: this is
    called after the person already has their answer.
    """
    if not _enabled:
        return

    connection = None
    try:
        connection = _connection()
        with connection.cursor() as cursor:
            cursor.execute(INSERT, {
                "question": result.get("question", "")[:2000],
                "mode": mode,
                "final_sql": result.get("sql"),
                "attempts_used": result.get("attempts", 0),
                # A list becomes a Postgres array through psycopg2 on its
                # own. Storing it as an array rather than a string means
                # UNNEST works and "which errors actually happen" is one
                # query rather than string parsing.
                "error_types": [item["error_type"] for item in result.get("history", [])],
                "tables_used": result.get("tables_used") or [],
                "gave_up": bool(result.get("gave_up")),
                "refused": bool(result.get("refused")),
                "rows_returned": result.get("row_count"),
                "latency_ms": result.get("latency_ms"),
                "tokens": result.get("tokens"),
                "cost_usd": result.get("cost"),
                "model": result.get("model"),
                "trace_id": result.get("trace_id"),
            })
        connection.commit()

    except Exception as error:
        _warn_once(error)
        if connection is not None:
            try:
                connection.rollback()
            except Exception:
                pass

    finally:
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass


def _warn_once(error):
    """
    Complain the first time and then stay quiet.

    If logging is broken it is broken for every question, and one warning
    per request would bury the output I actually came to read.
    """
    global _warned
    if not _warned:
        print(f"Query log write failed, carrying on without it: {error}")
        _warned = True


def check():
    """
    Can the log role connect and insert?

    Used by /api/health. A silently failing log is the same problem as a
    silently failing trace: nothing raises, the rows just never arrive,
    and you find out when you go looking for a month of history that is
    not there.

    The test row is rolled back rather than committed, so calling this
    does not pollute the data it is checking.
    """
    if not _enabled:
        return False

    connection = None
    try:
        connection = _connection()
        with connection.cursor() as cursor:
            cursor.execute(INSERT, {
                "question": "health check", "mode": "agent", "final_sql": None,
                "attempts_used": 0, "error_types": [], "tables_used": [],
                "gave_up": False, "refused": False, "rows_returned": None,
                "latency_ms": None, "tokens": None, "cost_usd": None,
                "model": None, "trace_id": None,
            })
        connection.rollback()
        return True
    except Exception:
        return False
    finally:
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass

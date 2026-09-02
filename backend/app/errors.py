"""
Turning a database failure into something the agent can act on.

This file is small and it is the reason the project is not a retry loop.

Blind retry only fixes one kind of problem. A syntax error needs the
parser message and nothing else. An unknown column means my schema
retrieval handed the model the wrong tables, so regenerating with the
same tables will fail the same way forever. Those are different problems
and they need different responses, so the first job is to work out which
one happened.

PostgreSQL already tells us. Every error carries a five character
SQLSTATE code, which is exact, and often a hint like "Perhaps you meant
customer_name". A stack trace string throws all of that away and makes
the model guess. So nothing here returns a string. Everything returns a
dictionary with an error_type on it, and agent.py switches on that.

The codes are from the PostgreSQL error code appendix. I looked up the
ones I could actually cause and left the rest to fall through.
"""

# SQLSTATE -> our error type.
#
# The names on the left are PostgreSQL's. The names on the right are the
# seven the agent knows how to respond to. Anything not in here becomes
# "unknown", which is treated as unrepairable on purpose: guessing at a
# failure I have not seen is how an agent starts inventing.
SQLSTATE_MAP = {
    "42601": "syntax_error",          # syntax_error
    "42703": "unknown_column",        # undefined_column
    "42P01": "unknown_table",         # undefined_table
    "42P02": "unknown_table",         # undefined_parameter, from a bad :name
    "42702": "ambiguous_column",      # ambiguous_column
    "42P09": "ambiguous_column",      # ambiguous_alias
    "42P08": "ambiguous_column",      # ambiguous_parameter
    "42883": "unknown_function",      # undefined_function
    "42804": "type_mismatch",         # datatype_mismatch
    "42846": "type_mismatch",         # cannot_coerce
    "22P02": "type_mismatch",         # invalid_text_representation
    "22007": "type_mismatch",         # invalid_datetime_format
    "22008": "type_mismatch",         # datetime_field_overflow
    "57014": "timeout",               # query_canceled, which is our timeout
    "42501": "permission_denied",     # insufficient_privilege
    "25006": "permission_denied",     # read_only_sql_transaction
    "42P10": "syntax_error",          # invalid_column_reference
    "42809": "unknown_table",         # wrong_object_type

    # The four below were added after the evaluation, because the
    # evaluation is what found them missing.
    #
    # A question about the top three products per category came back with
    # 42803, "column must appear in the GROUP BY clause". That is one of
    # the most common SQL mistakes there is, and because it was not in
    # this table it fell through to "unknown", which is deliberately not
    # repairable. So the agent gave up after a single attempt on exactly
    # the kind of error the repair loop exists for.
    #
    # A gap in this table is worse than a bug in the repair prompts. A
    # bad prompt makes the repair less likely to work; a missing code
    # means the repair never runs at all, and the agent looks like it
    # cannot recover when really it was never asked to.
    "42803": "grouping_error",        # grouping_error
    # 42P20 was lumped in with 42803 above until an evaluation run caught
    # it. Both are "you have misused an aggregate", so one bucket looked
    # reasonable, and it is not: they are different mistakes and they need
    # opposite advice.
    #
    #   42803  a column is neither aggregated nor grouped
    #   42P20  a window function was used somewhere it cannot be, which
    #          in practice is always WHERE
    #
    # Sharing an instruction meant the repair for the second one talked
    # about GROUP BY, which is irrelevant to it, so the model produced the
    # same illegal WHERE three times running and the agent gave up. That
    # is blind retry wearing a taxonomy, which is the exact thing this
    # file exists to prevent, at a finer grain than I had looked.
    "42P20": "windowing_error",       # windowing_error
    "21000": "cardinality_violation", # subquery returned more than one row
    "22012": "division_by_zero",      # division_by_zero
}

# Which types are worth another attempt, and which are not.
#
# permission_denied is deliberately not repairable. If a query got that
# far it tried to do something it should never have tried, and the right
# response is to fail loudly and log it, not to quietly write a different
# query and pretend it did not happen.
REPAIRABLE = {
    "syntax_error",
    "unknown_column",
    "unknown_table",
    "ambiguous_column",
    "unknown_function",
    "type_mismatch",
    "timeout",
    "grouping_error",
    "windowing_error",
    "cardinality_violation",
    "division_by_zero",
}

NOT_REPAIRABLE = {
    "permission_denied",
    "rejected",       # sqlglot said no. See the note below
    "unknown",
}


def classify(exception):
    """
    Read a psycopg2 exception and give back a structured error.

    Everything the repair node needs is in the returned dictionary, so
    nothing downstream ever has to parse an error message with string
    matching.
    """
    sqlstate = getattr(exception, "pgcode", None)
    error_type = SQLSTATE_MAP.get(sqlstate, "unknown")

    # psycopg2 keeps the parts of the error separately in .diag. The
    # hint is the useful one. PostgreSQL is genuinely good at "Perhaps
    # you meant ..." and handing that straight to the model fixes a lot
    # of unknown_column errors on the first repair.
    diag = getattr(exception, "diag", None)
    message = getattr(diag, "message_primary", None) or str(exception)
    hint = getattr(diag, "message_hint", None)
    position = getattr(diag, "statement_position", None)

    return build(
        error_type=error_type,
        message=message.strip(),
        hint=hint,
        sqlstate=sqlstate,
        position=int(position) if position else None,
    )


def build(error_type, message, hint=None, sqlstate=None, position=None):
    """
    Make one structured error by hand.

    Used for the failures that never reach the database at all: sqlglot
    rejecting a statement, or a query coming back with no rows.
    """
    return {
        "ok": False,
        "error_type": error_type,
        "message": message,
        "hint": hint,
        "sqlstate": sqlstate,
        "position": position,
        "repairable": error_type in REPAIRABLE,
    }


def rejected(message, hint=None):
    """
    The parser refused to let this query run.

    Not repairable, and that is not laziness. sqlglot only rejects a
    statement for one of two reasons: it is not a single SELECT, or it
    names a table that does not exist in this database. The first is
    something the agent should never be trying and I want to see it in
    the log rather than paper over it. The second is caught earlier and
    better by the unknown_table path once the database answers.
    """
    return build(error_type="rejected", message=message, hint=hint)


def empty_result(sql):
    """
    Zero rows. This is not really an error and it is not treated as one.

    A query that returns nothing is very often correct. "No sales in
    that period" is a true and useful answer. inspect_result() in
    agent.py runs one diagnostic before deciding, and most of the time
    the honest answer is that the data is not there.
    """
    return {
        "ok": True,
        "error_type": "empty_result",
        "message": "The query ran without error and matched no rows.",
        "hint": None,
        "sqlstate": None,
        "position": None,
        "repairable": False,
        "sql": sql,
    }

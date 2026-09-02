"""
Layer 3 of the envelope: read the SQL before the database ever sees it.

sqlglot parses the statement into a tree, so this is not string matching.
Looking for the word "delete" in the SQL would be beaten by a column
called deleted_at, by a comment, and by a string literal. Walking the
parsed tree and asking "is there a Delete node anywhere in here" cannot
be beaten by any of those, because it is reading the same structure
PostgreSQL will read.

What this layer is for, and what it is not for.

It is for catching problems without a round trip, and for making the
refusal legible. When it rejects something I get a clear reason instead
of a database error.

It is **not** what makes the project safe. The read only role is. If
every line in this file were deleted, a DROP TABLE would still fail,
because the credential has no such permission. I put that the right way
round because getting it backwards is the mistake this whole section of
the guide is warning about.

Three checks, then one rewrite:

    1. one statement only
    2. that statement is a SELECT, and nothing anywhere inside it is a
       write, a DDL, or a command sqlglot could not model
    3. every table it names exists in this database
    4. a LIMIT is added if missing, or lowered if it is too big
"""

import sqlglot
from sqlglot import exp

from . import config, errors

DIALECT = "postgres"

# Node types that must never appear anywhere in the tree, not even
# inside a subquery or a CTE.
#
# exp.Command is the important one and it is easy to miss. sqlglot parses
# anything it does not have a proper node for into a Command, which is
# where VACUUM, COPY, SET, CALL and every other odd statement ends up. So
# this entry is not a specific ban, it is "if sqlglot did not recognise
# it, I am not running it".
FORBIDDEN_NODES = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Drop,
    exp.Create,
    exp.Alter,
    exp.Merge,
    exp.Grant,
    exp.Command,
    exp.Transaction,
    exp.Commit,
    exp.Rollback,
)

# Functions that read or write outside the rows. None of these can help
# answer a question about the shop, and pg_sleep is a free way to hold a
# connection open for the whole statement timeout.
FORBIDDEN_FUNCTIONS = {
    "pg_read_file",
    "pg_read_binary_file",
    "pg_ls_dir",
    "pg_stat_file",
    "pg_sleep",
    "pg_sleep_for",
    "pg_terminate_backend",
    "pg_cancel_backend",
    "lo_import",
    "lo_export",
    "dblink",
    "dblink_exec",
    "query_to_xml",
    "set_config",
    "pg_reload_conf",
}


def check(sql, known_tables):
    """
    Look at one piece of SQL and decide whether it may run.

    known_tables is the set of real table names, passed in rather than
    imported, so the tests can hand this function a fake schema without
    a database anywhere near them.

    Returns (safe_sql, None) if it is allowed, where safe_sql has the
    LIMIT fixed up, or (None, structured_error) if it is not.
    """
    if not sql or not sql.strip():
        return None, errors.rejected("The model returned an empty query.")

    # --------------------------------------------- 1. one statement only
    try:
        statements = sqlglot.parse(sql, dialect=DIALECT)
    except Exception as error:
        # A parse failure here is a syntax error, and it is worth
        # repairing, so it goes back as syntax_error and not rejected.
        # sqlglot's message names the position, which is what the model
        # needs to fix it.
        return None, errors.build(
            error_type="syntax_error",
            message=f"The query could not be parsed: {error}",
            hint="Return one valid PostgreSQL SELECT statement.",
        )

    # parse() drops trailing semicolons, so a list longer than one means
    # there really were two statements. That is how a "; DROP TABLE"
    # would arrive and it stops here.
    real = [statement for statement in statements if statement is not None]
    if len(real) != 1:
        return None, errors.rejected(
            f"Expected one statement, got {len(real)}.",
            hint="Send a single SELECT. Do not chain statements with a semicolon.",
        )

    tree = real[0]

    # ------------------------------------------------ 2. SELECT and only
    # A plain SELECT parses as Select. "WITH x AS (...) SELECT" also
    # parses as Select with a with= argument, so that is covered.
    # UNION, INTERSECT and EXCEPT parse as SetOperation, which is fine.
    if not isinstance(tree, (exp.Select, exp.Union, exp.Except, exp.Intersect, exp.Subquery)):
        return None, errors.rejected(
            f"Only SELECT is allowed, this is {type(tree).__name__.upper()}.",
            hint="This database is read only. Rewrite the question as a SELECT.",
        )

    for node in tree.walk():
        if isinstance(node, FORBIDDEN_NODES):
            return None, errors.rejected(
                f"{type(node).__name__.upper()} is not allowed anywhere in the query.",
                hint="This database is read only.",
            )

    # SELECT ... INTO makes a table. It is a Select node, so the loop
    # above walks straight past it.
    if tree.args.get("into"):
        return None, errors.rejected(
            "SELECT INTO creates a table and is not allowed.",
            hint="Use a plain SELECT.",
        )

    # SELECT ... FOR UPDATE takes row locks on a read only connection.
    if tree.args.get("locks"):
        return None, errors.rejected(
            "Row locking (FOR UPDATE / FOR SHARE) is not allowed.",
            hint="Use a plain SELECT.",
        )

    for node in tree.find_all(exp.Anonymous, exp.Func):
        name = _function_name(node)
        if name in FORBIDDEN_FUNCTIONS:
            return None, errors.rejected(
                f"The function {name}() is not allowed.",
                hint="Answer the question using only the shop's tables.",
            )

    # ------------------------------------------------- 3. real tables only
    used = tables_in(tree)
    unknown = sorted(used - {name.lower() for name in known_tables})
    if unknown:
        return None, errors.build(
            error_type="unknown_table",
            message=f"These tables do not exist: {', '.join(unknown)}.",
            hint=f"The tables in this database are: {', '.join(sorted(known_tables))}.",
        )

    # ---------------------------------------------------- 4. force a LIMIT
    return apply_limit(tree), None


def _function_name(node):
    """The function's name in lower case, whatever kind of node it is."""
    if isinstance(node, exp.Anonymous):
        return str(node.this).lower()
    return type(node).__name__.lower()


def tables_in(tree):
    """
    Every real table the query reads, in lower case.

    The catch is CTE names. In "WITH recent AS (...) SELECT * FROM
    recent", sqlglot sees "recent" as a Table node, because that is what
    it looks like in the FROM clause. It is not a table, it is a name
    the query invented two lines earlier. Counting it as one would make
    every CTE query fail the unknown table check, which took me a while
    to work out because the error said the table did not exist and it
    genuinely did not.
    """
    cte_names = {
        cte.alias_or_name.lower()
        for cte in tree.find_all(exp.CTE)
        if cte.alias_or_name
    }

    names = set()
    for table in tree.find_all(exp.Table):
        name = (table.name or "").lower()
        if name and name not in cte_names:
            names.add(name)

    return names


def apply_limit(tree):
    """
    Make sure the statement ends with a LIMIT we are happy with.

    Missing, or bigger than MAX_ROWS, and it becomes MAX_ROWS. Smaller
    and it is left alone, because the model asking for the top 5 is the
    model answering the question properly.

    This runs on the parsed tree and not on the string, so a query that
    already has a LIMIT inside a subquery does not confuse it.
    """
    limit = tree.args.get("limit")
    current = None

    if limit is not None:
        try:
            current = int(limit.expression.this)
        except (AttributeError, TypeError, ValueError):
            # A LIMIT with an expression in it rather than a plain
            # number. Rare, and not worth guessing at, so it is replaced.
            current = None

    if current is None or current > config.MAX_ROWS:
        tree = tree.limit(config.MAX_ROWS)

    # comments=False drops anything the model wrote after a -- or inside
    # a /* */. They cannot do any harm, because a comment is not part of
    # the tree and never executes. I strip them anyway for one reason:
    # the SQL returned from here is what gets logged, traced and shown on
    # the page, and I want that string to be exactly the statement that
    # ran. A "-- DELETE FROM bills" sitting in a logged query is alarming
    # to read and means nothing, which is the worst combination.
    return tree.sql(dialect=DIALECT, comments=False)

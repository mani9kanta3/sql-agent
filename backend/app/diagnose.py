"""
Working out whether zero rows means "no" or means "wrong query".

This is the part of the project I spent longest on and it is the part I
would most want to be asked about.

The tempting thing to do with an empty result is treat it as a failure
and repair. That is wrong, and it is wrong in an expensive way. If the
agent retries until something comes back, it will loosen the filter,
widen the date range, drop the status check, and eventually return rows.
Then it answers confidently, with a query attached, and the query is not
the question that was asked. That is a hallucination wearing evidence,
which is worse than an ordinary one because it looks checked.

The opposite mistake is just as real. A query filtering on status =
'paid' against a column holding 'PAID' returns nothing, instantly, with
no error. Reporting "no paid bills" there is a wrong answer delivered
confidently too.

So the rule is one diagnostic, not a loop. Take the query, remove its
most suspicious filter, and run it again.

    still empty  ->  the data genuinely is not there. Answer "no rows
                     match" and stop. This is a correct answer and the
                     agent should be able to give it.

    rows appear  ->  the filter is the problem, so it is worth exactly
                     one repair, and the repair is told which filter
                     looked wrong.

Which filter is "most suspicious" is a heuristic and I want to be honest
that it is one. An equality against a text literal is the thing that
goes wrong most often, because case and spelling are invisible in the
schema, so those are dropped first. Failing that, the last condition in
the AND chain, which is usually the most specific one the model added.
"""

import sqlglot
from sqlglot import exp

DIALECT = "postgres"


def relax_filter(sql):
    """
    Give back the same query with one filter taken out.

    Returns (relaxed_sql, description_of_what_was_removed), or
    (None, None) when there is nothing to relax, which happens for a
    query with no WHERE clause at all. An aggregate over a whole table
    returning nothing is a different situation and there is no useful
    diagnostic for it.
    """
    try:
        tree = sqlglot.parse_one(sql, dialect=DIALECT)
    except Exception:
        return None, None

    where = tree.args.get("where")
    if where is None:
        return None, None

    condition = where.this
    predicates = _split_and(condition)

    if not predicates:
        return None, None

    suspect = _most_suspicious(predicates)
    removed = suspect.sql(dialect=DIALECT)

    remaining = [item for item in predicates if item is not suspect]

    # Work on a copy. tree is used again by the caller for the error
    # message and mutating it in place gave me a very confusing five
    # minutes where the failed query and the relaxed query were the
    # same object.
    relaxed = tree.copy()

    if not remaining:
        # That was the only condition, so the WHERE clause goes entirely.
        relaxed.set("where", None)
    else:
        rebuilt = remaining[0]
        for item in remaining[1:]:
            rebuilt = exp.And(this=rebuilt, expression=item)
        relaxed.set("where", exp.Where(this=rebuilt))

    return relaxed.sql(dialect=DIALECT), removed


def case_variant(sql):
    """
    The same query with its most suspicious text filter made case and
    spacing insensitive, or (None, None) if there is no such filter.

    This exists because the plain relax-the-filter rule has a false
    positive, and it is not a rare one.

    The rule says: take the filter out, and if rows come back then the
    filter is suspect. For a query whose only filter is an exact lookup,
    that is always true. "SELECT ... WHERE bill_no = 'NOPE-1'" returns
    nothing, and removing the filter returns every bill in the shop, so
    the diagnostic concludes the filter is wrong when actually the answer
    is simply that there is no such bill. The agent then burns its
    remaining attempts rewriting a query that was right the first time.

    So the test is narrowed. The failure this whole path exists for is a
    value written in the wrong case: status = 'paid' against a column
    holding 'PAID'. That is a specific, checkable thing, so it gets
    checked specifically rather than inferred from rows appearing.

        rows for the case-insensitive version  -> it really is the case
                                                  or the spacing, repair
        still nothing                          -> the value is not in
                                                  that column at all, so
                                                  answer honestly

    Only equality and LIKE against a text literal can be probed this way.
    A wrong date range or a wrong number is not a spelling problem, and
    for those the original relax-the-filter rule is still the best
    available signal.

    Returns (probe_sql, description_of_the_filter).
    """
    try:
        tree = sqlglot.parse_one(sql, dialect=DIALECT)
    except Exception:
        return None, None

    where = tree.args.get("where")
    if where is None:
        return None, None

    predicates = _split_and(where.this)
    if not predicates:
        return None, None

    suspect = _most_suspicious(predicates)

    if not isinstance(suspect, (exp.EQ, exp.Like, exp.ILike)):
        return None, None
    if not _has_string_literal(suspect):
        return None, None

    description = suspect.sql(dialect=DIALECT)

    # Work on a copy, and find the same node inside it. Mutating the tree
    # the caller still holds gave me a confusing few minutes once already.
    probe = tree.copy()
    target = _same_predicate_in(probe, description)
    if target is None:
        return None, None

    target.replace(_loosened(target))
    return probe.sql(dialect=DIALECT), description


def _same_predicate_in(tree, rendered):
    """Find the node in this tree whose SQL matches the one we picked."""
    for node in tree.find_all(exp.EQ, exp.Like, exp.ILike):
        if node.sql(dialect=DIALECT) == rendered:
            return node
    return None


def _loosened(node):
    """
    A version of one predicate that ignores case and surrounding spaces.

    LIKE becomes ILIKE, which PostgreSQL already provides. An equality
    becomes LOWER(TRIM(col::TEXT)) = LOWER(TRIM('literal')). The cast is
    there because the column is not always text: comparing a VARCHAR is
    the common case, but LOWER() on a non-text column is an error, and an
    error here would look like "the value is not there" and give exactly
    the wrong answer.
    """
    if isinstance(node, (exp.Like, exp.ILike)):
        return exp.ILike(this=node.this, expression=node.expression)

    return exp.EQ(
        this=_normalise(node.this),
        expression=_normalise(node.expression),
    )


def _normalise(side):
    """LOWER(TRIM(side::TEXT)) around one side of a comparison."""
    cast = exp.Cast(this=side, to=exp.DataType.build("text"))
    return exp.Lower(this=exp.Trim(this=cast))


def _split_and(condition):
    """
    Flatten an AND chain into a list of conditions.

    "a AND b AND c" parses as And(And(a, b), c), so this walks down the
    left spine. An OR is left alone as a single unit, because taking half
    of an OR out changes the meaning in a way that is not a relaxation.
    """
    if isinstance(condition, exp.And):
        return _split_and(condition.this) + _split_and(condition.expression)
    return [condition]


def _most_suspicious(predicates):
    """
    Pick the filter most likely to be the wrong one.

    Scored, highest wins:

      3  equality against a text literal      status = 'paid'
      2  LIKE or ILIKE against a literal      name LIKE '%screw%'
      1  equality against anything else       cat_id = 4
      0  everything else, mostly ranges       bill_dt >= '2026-01-01'

    Ties go to the last one written, which in practice is the most
    specific thing the model added on top of the obvious date filter.
    """
    best = predicates[-1]
    best_score = -1

    for predicate in predicates:
        score = 0

        if isinstance(predicate, exp.EQ):
            score = 3 if _has_string_literal(predicate) else 1
        elif isinstance(predicate, (exp.Like, exp.ILike)):
            score = 2

        # >= rather than > so that a later predicate wins a tie.
        if score >= best_score:
            best = predicate
            best_score = score

    return best


def _has_string_literal(node):
    """True if either side of the comparison is a quoted string."""
    for literal in node.find_all(exp.Literal):
        if literal.is_string:
            return True
    return False


if __name__ == "__main__":
    # python -m app.diagnose
    examples = [
        "SELECT COUNT(*) FROM bills WHERE bill_dt >= '2026-01-01' AND status = 'paid'",
        "SELECT * FROM products WHERE prod_name LIKE '%cement%'",
        "SELECT SUM(total_amt) FROM bills",
    ]
    for sql in examples:
        relaxed, removed = relax_filter(sql)
        print(f"\n{sql}")
        print(f"  removed: {removed}")
        print(f"  becomes: {relaxed}")

"""
The error taxonomy.

Small tests for a small file, but the classification is what picks the
repair strategy, so getting it wrong means the agent responds to a
missing column by fixing punctuation.

The important assertion in here is the one about permission_denied not
being repairable. If that ever flips, the agent starts quietly rewriting
queries that tried to do something they should not have, instead of
failing where I can see it.
"""

import pytest

from app import errors


class FakeDiag:
    """Stands in for psycopg2's error.diag, which has no public constructor."""

    def __init__(self, message_primary=None, message_hint=None, statement_position=None):
        self.message_primary = message_primary
        self.message_hint = message_hint
        self.statement_position = statement_position


class FakePgError(Exception):
    """A psycopg2 error, close enough for classify() to read."""

    def __init__(self, pgcode, message, hint=None, position=None):
        super().__init__(message)
        self.pgcode = pgcode
        self.diag = FakeDiag(message, hint, position)


@pytest.mark.parametrize("sqlstate,expected", [
    ("42601", "syntax_error"),
    ("42703", "unknown_column"),
    ("42P01", "unknown_table"),
    ("42702", "ambiguous_column"),
    ("42883", "unknown_function"),
    ("42804", "type_mismatch"),
    ("22P02", "type_mismatch"),
    ("57014", "timeout"),
    ("42501", "permission_denied"),
    ("25006", "permission_denied"),
    ("42803", "grouping_error"),
    ("42P20", "windowing_error"),
    ("21000", "cardinality_violation"),
    ("22012", "division_by_zero"),
])
def test_sqlstate_codes_map_to_the_right_type(sqlstate, expected):
    structured = errors.classify(FakePgError(sqlstate, "something went wrong"))
    assert structured["error_type"] == expected


def test_a_group_by_error_is_repairable():
    """
    A regression test for a gap the evaluation found.

    42803 is "column must appear in the GROUP BY clause", one of the most
    common SQL mistakes there is. It was missing from the map, so it fell
    through to "unknown", which is deliberately never repaired. The agent
    gave up after a single attempt on precisely the kind of error the
    repair loop was built for.

    A missing code is worse than a weak repair prompt: a weak prompt makes
    the repair less likely to work, a missing code means it never runs.
    """
    structured = errors.classify(FakePgError(
        "42803",
        'column "bi.line_total" must appear in the GROUP BY clause '
        'or be used in an aggregate function',
    ))
    assert structured["error_type"] == "grouping_error"
    assert structured["repairable"] is True


def test_a_window_function_error_is_not_the_same_as_a_group_by_error():
    """
    Found by an evaluation run, not by a test.

    Both are "you misused an aggregate" and I had them sharing one bucket,
    so the repair for a window-in-WHERE talked about GROUP BY, which is
    irrelevant to it. The model produced the same illegal WHERE three
    times and the agent gave up. Sharing an instruction between two
    different mistakes is blind retry with extra steps.
    """
    grouping = errors.classify(FakePgError(
        "42803", 'column "bi.line_total" must appear in the GROUP BY clause'))
    windowing = errors.classify(FakePgError(
        "42P20", "window functions are not allowed in WHERE"))

    assert grouping["error_type"] != windowing["error_type"]
    assert windowing["error_type"] == "windowing_error"
    assert windowing["repairable"] is True

    from app.prompts import REPAIR_INSTRUCTIONS

    # The advice has to actually differ, or splitting the code achieves
    # nothing. The window one must say where to put the filter.
    assert REPAIR_INSTRUCTIONS["windowing_error"] != REPAIR_INSTRUCTIONS["grouping_error"]
    assert "subquery" in REPAIR_INSTRUCTIONS["windowing_error"].lower()


def test_every_repairable_type_has_a_repair_instruction():
    """
    The two halves have to stay in step.

    Adding a code to the map without adding an instruction for it means
    the repair falls back to a generic "read the error and fix it", which
    is the blind retry this project exists to avoid. This catches that at
    test time rather than in a forty minute evaluation run.
    """
    from app.prompts import REPAIR_INSTRUCTIONS

    missing = errors.REPAIRABLE - set(REPAIR_INSTRUCTIONS)
    assert not missing, f"no repair instruction for: {sorted(missing)}"


def test_an_unknown_code_does_not_become_a_guess():
    """
    Anything I have not seen is "unknown" and is not repaired.

    Guessing at a failure I do not recognise is how an agent starts
    inventing, so the honest thing is to stop.
    """
    structured = errors.classify(FakePgError("XX999", "internal error"))
    assert structured["error_type"] == "unknown"
    assert structured["repairable"] is False


def test_the_database_hint_is_kept():
    """
    PostgreSQL is genuinely good at "Perhaps you meant". Passing that
    straight to the model fixes a lot of unknown_column errors on the
    first repair, so losing it would cost real accuracy.
    """
    structured = errors.classify(FakePgError(
        "42703",
        'column "cust_name" does not exist',
        hint='Perhaps you meant to reference the column "customers.cust_name".',
    ))
    assert structured["hint"] is not None
    assert "cust_name" in structured["hint"]


def test_permission_denied_is_never_repaired():
    """
    The assertion I most want to stay true.

    A query that got a permission error tried to do something it should
    never have tried. Repairing it means writing a different query and
    moving on quietly, which hides exactly the event I want to see.
    """
    structured = errors.classify(FakePgError("42501", "permission denied for table bills"))
    assert structured["error_type"] == "permission_denied"
    assert structured["repairable"] is False
    assert "permission_denied" in errors.NOT_REPAIRABLE


def test_a_timeout_is_repairable():
    """A slow query can be made cheaper, so it is worth another attempt."""
    structured = errors.classify(FakePgError("57014", "canceling statement due to statement timeout"))
    assert structured["error_type"] == "timeout"
    assert structured["repairable"] is True


def test_an_empty_result_is_not_an_error():
    """
    Zero rows comes back with ok: True.

    This one line is the difference between an agent that can say "no
    sales that month" and one that loosens the filter until something
    comes back.
    """
    empty = errors.empty_result("SELECT * FROM bills WHERE 1 = 0")
    assert empty["ok"] is True
    assert empty["error_type"] == "empty_result"
    assert empty["repairable"] is False

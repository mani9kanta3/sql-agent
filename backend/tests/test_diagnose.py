"""
The empty result diagnostic.

These tests are about which filter gets dropped, because that decision
is a heuristic and a heuristic with no tests is a guess that nobody is
watching.

The rule being tested: an equality against a text literal is dropped
first, because case and spelling are invisible in the schema and
status = 'paid' against a column holding 'PAID' is the failure this
whole path exists for.
"""

from app import diagnose


def test_the_string_equality_is_dropped_before_the_date_range():
    """
    The one that matters. Given a date filter and a status filter, the
    status filter is the suspect, because a date range that is slightly
    wrong still returns rows and a misspelled status returns none.
    """
    sql = (
        "SELECT COUNT(*) FROM bills "
        "WHERE bill_dt >= '2026-01-01' AND status = 'paid'"
    )
    relaxed, removed = diagnose.relax_filter(sql)

    assert "status" in removed
    assert "bill_dt" in relaxed
    assert "status" not in relaxed


def test_a_single_condition_removes_the_whole_where_clause():
    sql = "SELECT * FROM products WHERE prod_name = 'Cement'"
    relaxed, removed = diagnose.relax_filter(sql)

    assert "WHERE" not in relaxed.upper()
    assert "Cement" in removed


def test_a_query_with_no_filter_has_nothing_to_relax():
    """
    An aggregate over a whole table returning nothing is a different
    situation and there is no useful diagnostic for it, so the caller is
    told there is nothing to try rather than being handed the same query
    back.
    """
    relaxed, removed = diagnose.relax_filter("SELECT SUM(total_amt) FROM bills")

    assert relaxed is None
    assert removed is None


def test_a_like_filter_is_preferred_over_a_number_comparison():
    sql = "SELECT * FROM products WHERE sell_price > 100 AND prod_name LIKE '%cement%'"
    relaxed, removed = diagnose.relax_filter(sql)

    assert "LIKE" in removed.upper()
    assert "sell_price" in relaxed


def test_only_one_filter_is_removed_at_a_time():
    """
    One diagnostic, not a loop. Removing everything would always return
    rows and would prove nothing at all.
    """
    sql = (
        "SELECT * FROM bills "
        "WHERE bill_dt >= '2026-01-01' AND status = 'PAID' AND total_amt > 500"
    )
    relaxed, _removed = diagnose.relax_filter(sql)

    remaining = sum(1 for column in ["bill_dt", "status", "total_amt"] if column in relaxed)
    assert remaining == 2


def test_a_text_equality_can_be_probed_case_insensitively():
    """
    The narrowing that stops the diagnostic firing on every exact lookup.
    Both sides are lowered and trimmed, and the cast is there because
    LOWER() on a non-text column is an error, and an error would read as
    "the value is not there".
    """
    probe, described = diagnose.case_variant(
        "SELECT bill_no FROM bills WHERE status = 'paid'"
    )

    assert described == "status = 'paid'"
    assert "LOWER" in probe.upper()
    assert "TRIM" in probe.upper()
    assert "CAST" in probe.upper()


def test_a_like_becomes_an_ilike():
    probe, _described = diagnose.case_variant(
        "SELECT * FROM products WHERE prod_name LIKE '%cement%'"
    )

    assert "ILIKE" in probe.upper()


def test_a_date_range_cannot_be_probed_this_way():
    """
    A wrong date is not a spelling problem, so there is nothing specific
    to test and the caller falls back to the general rule.
    """
    probe, described = diagnose.case_variant(
        "SELECT SUM(total_amt) FROM bills WHERE bill_dt >= '2030-01-01'"
    )

    assert probe is None
    assert described is None


def test_broken_sql_does_not_raise():
    """
    The diagnostic runs after a query already succeeded, so this should
    not happen. It returning None instead of throwing means a surprise
    here cannot take down an answer that was otherwise fine.
    """
    relaxed, removed = diagnose.relax_filter("this is not sql at all")
    assert relaxed is None
    assert removed is None

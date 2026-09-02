"""
The graph nodes that do not need a language model.

Most of the graph can be tested without spending a token. validate_static
is pure parsing, execute is a database call, inspect_result is a database
call plus a decision, and decide is just rules. Only generate_sql and
write_answer actually need a model, and those are what the forty question
evaluation is for.

These exist because of a bug I introduced while adding tracing. Pulling
the body of inspect_result out into a helper left it referring to a
`state` variable that was no longer in scope. Nothing failed at import,
the tests all passed, and the code was only reachable when a query
returned no rows and relaxing its filter returned some. Two of the forty
evaluation questions go down exactly that path, so it would have shown up
as two mysteriously failed questions after a forty minute run.

The lesson is that the empty-result branch is the least travelled and the
most interesting code in the project, so it needs tests that do not
depend on a model happening to write the right query.
"""

import pytest

from app import agent

pytestmark = pytest.mark.usefixtures("live_db")


def _state(**overrides):
    """A minimal graph state, with only what the node under test reads."""
    base = {
        "question": "a question",
        "attempts": 1,
        "history": [],
        "tokens": 0,
        "widened": False,
    }
    base.update(overrides)
    return base


# ------------------------------------------------------- validate_static


def test_validate_passes_a_good_query_and_adds_a_limit():
    out = agent.validate_static(_state(sql="SELECT bill_no FROM bills"))

    assert out["error"] is None
    assert "LIMIT" in out["safe_sql"].upper()


def test_validate_rejects_a_write_without_touching_the_database():
    out = agent.validate_static(_state(sql="DELETE FROM bills"))

    assert out["error"]["error_type"] == "rejected"
    # The failure is remembered, or the repair prompt has nothing to show
    # the model and it regenerates the same thing.
    assert len(out["history"]) == 1
    assert out["history"][0]["sql"] == "DELETE FROM bills"


# --------------------------------------------------------------- execute


def test_execute_returns_rows_for_a_real_query():
    out = agent.execute(_state(safe_sql="SELECT COUNT(*) AS n FROM bills LIMIT 200"))

    assert out["error"] is None
    assert out["result"]["row_count"] == 1


def test_execute_classifies_a_bad_column_and_remembers_it():
    out = agent.execute(_state(safe_sql="SELECT customer_name FROM bills LIMIT 200"))

    assert out["result"] is None
    assert out["error"]["error_type"] == "unknown_column"
    assert len(out["history"]) == 1


# --------------------------------------------------------- inspect_result


def test_a_result_with_rows_is_left_alone():
    result = {"ok": True, "row_count": 3, "sql": "SELECT 1", "rows": [1, 2, 3]}

    assert agent.inspect_result(_state(result=result)) == {}


def test_an_empty_result_with_a_wrong_filter_asks_for_one_repair():
    """
    The branch that was broken.

    status is stored upper case, so filtering on 'paid' matches nothing.
    Removing that filter does return rows, so the filter is the suspect
    and it is worth exactly one repair.
    """
    sql = "SELECT bill_no FROM bills WHERE status = 'paid' LIMIT 200"
    result = {"ok": True, "error_type": "empty_result", "sql": sql, "rows": [], "row_count": 0}

    out = agent.inspect_result(_state(result=result))

    assert out["error"]["error_type"] == "empty_filter"
    assert out["error"]["repairable"] is True
    # The repair has to be told which filter looked wrong, not left to guess.
    assert "status" in out["error"]["message"]
    assert len(out["history"]) == 1
    # The known-wrong query must not survive as "the query it ran".
    assert out["result"] is None


def test_a_lookup_that_genuinely_matches_nothing_is_answered_honestly():
    """
    The other half, and the one the plain relax-the-filter rule gets
    wrong.

    No bill has this number. Removing the filter returns every bill in
    the shop, so "rows appeared without it" is true and means nothing.
    Ignoring case still matches nothing, which is the real evidence, so
    the honest answer is that there is no such bill and no repair is
    worth spending.
    """
    sql = "SELECT bill_no FROM bills WHERE bill_no = 'NOPE-1' LIMIT 200"
    result = {"ok": True, "error_type": "empty_result", "sql": sql, "rows": [], "row_count": 0}

    out = agent.inspect_result(_state(result=result))

    assert out["error"] is None
    assert "not in the database" in out["diagnostic_note"]


def test_the_case_probe_tells_the_two_empty_cases_apart():
    """
    Both queries return nothing and both return rows once the filter is
    removed, so the plain rule cannot separate them. The case probe can:
    'paid' is really 'PAID', and 'NOPE-1' is really absent.
    """
    wrong_case = "SELECT bill_no FROM bills WHERE status = 'paid' LIMIT 200"
    absent = "SELECT bill_no FROM bills WHERE bill_no = 'NOPE-1' LIMIT 200"

    def inspect(sql):
        return agent.inspect_result(_state(result={
            "ok": True, "error_type": "empty_result",
            "sql": sql, "rows": [], "row_count": 0,
        }))

    assert inspect(wrong_case)["error"] is not None
    assert inspect(absent)["error"] is None


def test_an_anti_join_that_correctly_matches_nothing_is_not_repaired():
    """
    The failure the evaluation caught, and the reason the general
    relax-the-filter rule was removed.

    "Which products have never been sold" is a NOT EXISTS, and every
    product has sold, so nothing comes back and that is the right
    answer. Remove the NOT EXISTS and naturally every product appears,
    which the old rule read as proof the filter was wrong. The agent
    then rewrote a correct query three times and gave up on a question
    it had already answered.

    Repairing on suspicion costs more than it saves. Only positive
    evidence counts now.
    """
    sql = (
        "SELECT p.prod_name FROM products AS p "
        "WHERE NOT EXISTS (SELECT 1 FROM bill_items bi WHERE bi.prod_id = p.prod_id) "
        "LIMIT 200"
    )
    result = {"ok": True, "error_type": "empty_result", "sql": sql, "rows": [], "row_count": 0}

    out = agent.inspect_result(_state(result=result))

    assert out["error"] is None
    assert out.get("history", []) == []


def test_an_empty_aggregate_with_no_filter_has_nothing_to_diagnose():
    sql = "SELECT SUM(total_amt) FROM bills LIMIT 200"
    result = {"ok": True, "error_type": "empty_result", "sql": sql, "rows": [], "row_count": 0}

    out = agent.inspect_result(_state(result=result))

    assert out["error"] is None
    assert out["diagnostic_note"]


# ---------------------------------------------------------------- decide


def test_a_repairable_error_under_the_cap_goes_to_repair():
    error = {"error_type": "syntax_error", "repairable": True}

    assert agent.decide(_state(error=error, attempts=1)) == "prepare_repair"


def test_the_attempt_cap_stops_the_loop():
    from app import config

    error = {"error_type": "syntax_error", "repairable": True}

    assert agent.decide(_state(error=error, attempts=config.MAX_ATTEMPTS)) == "give_up"


def test_permission_denied_never_gets_repaired():
    """
    A query that got this far tried something it should never have tried.
    Repairing it quietly writes a different query and hides the event.
    """
    error = {"error_type": "permission_denied", "repairable": False}

    assert agent.decide(_state(error=error, attempts=1)) == "give_up"


# -------------------------------------------------------------- give_up


def test_giving_up_reports_every_attempt():
    history = [
        {"sql": "SELECT a", "error_type": "syntax_error", "message": "bad syntax"},
        {"sql": "SELECT b", "error_type": "unknown_column", "message": "no column b"},
    ]
    error = {"error_type": "unknown_column", "message": "no column b"}

    out = agent.give_up(_state(error=error, history=history, attempts=3))

    assert out["gave_up"] is True
    # An honest failure shows the work. That is a useful answer; an
    # invented one that looks like an answer is not.
    assert "syntax_error" in out["answer"]
    assert "unknown_column" in out["answer"]

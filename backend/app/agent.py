"""
The graph. This is the project.

    question
       |
   select_schema        pick the 3-5 tables this question needs
       |
   generate_sql   <-------------------+
       |                              |
   validate_static      sqlglot, no   |
       |                database yet  |
       |  fail ---------------------->|
       |                              |
   execute              read only,    |
       |                5s timeout    |
       |                              |
   inspect_result                     |
       |                              |
   +---+--------+----------+          |
   ok        error    empty/suspect   |
   |            |          |          |
   |            +----+-----+          |
   |                 |                |
 answer        attempts < 3 ? --yes-->+  repair
                     |
                     no
                     |
                  give_up            honest, with what it tried

Three things about this design are worth defending.

**The database is the oracle.** The agent is not marking its own
homework. A syntax error, a missing column, a type mismatch and a
timeout are all facts, reported by PostgreSQL, not opinions the model
formed about its own work. That is why a loop is justified here and why
it would not be justified for, say, an essay.

**The repair is not a retry.** The error type picks the strategy. A
syntax error regenerates with the parser message. An unknown column
means schema retrieval picked the wrong tables, so retrieval is widened
*before* regenerating, because regenerating over the same wrong tables
would fail the same way three times.

**It stops.** Three attempts, then it says so and shows what it tried.
An agent that loops forever on an impossible question is worse than one
that gives up in four seconds.
"""

import time

from typing import Optional
from typing_extensions import TypedDict

from langgraph.graph import END, StateGraph

from . import config, diagnose, llm, prompts, safety, schema_store, tools, tracing


class AgentState(TypedDict, total=False):
    """
    What travels through the graph.

    history is the field that matters. Without the record of what has
    already failed, the repair node regenerates the identical broken
    query, because from the model's point of view nothing about the
    question has changed. I found that out by watching three identical
    queries go past in a trace.
    """

    question: str

    tables: list           # table names chosen for this attempt
    schema_context: str    # the DDL and sample rows put in the prompt
    widened: bool          # has retrieval already been widened once

    sql: str               # the current candidate query
    safe_sql: str          # after sqlglot forced a LIMIT on

    attempts: int
    history: list          # [{"sql": ..., "error_type": ..., "message": ...}]

    result: Optional[dict]
    error: Optional[dict]
    diagnostic_note: str   # what the empty result diagnostic found

    answer: str
    refused: bool
    refusal_reason: str
    gave_up: bool

    tokens: int
    cost: float          # dollars, summed from the real per call usage
    started: float


# ---------------------------------------------------------------- nodes


def select_schema(state):
    """
    Choose which tables go in the prompt.

    Everything is not an option. Fifteen tables of DDL plus sample rows
    is a lot of context, and the cost is accuracy as much as tokens: a
    model shown every table finds a way to use them.

    Traced as a "retriever" and not a plain span, because that is what it
    is, and because the scores are the first thing I look at when an
    answer used the wrong table.
    """
    wide = state.get("widened", False)
    k = config.TABLES_RETRIEVED_WIDE if wide else config.TABLES_RETRIEVED

    with tracing.observe(
        "select-schema",
        as_type="retriever",
        input={"question": state["question"], "top_k": k},
    ) as span:
        picked = schema_store.select_tables(state["question"], k=k)
        names = [name for name, _score in picked]

        span.update(
            output=[{"table": name, "score": round(score, 3)} for name, score in picked],
            metadata={"widened": wide},
        )

    return {
        "tables": names,
        "schema_context": schema_store.build_context(names),
    }


def generate_sql(state):
    """
    Write the query, or refuse.

    First attempt uses the plain prompt. Later attempts use the repair
    prompt, which carries the structured error, the database's hint, and
    every query already tried.
    """
    repairing = bool(state.get("error"))

    if repairing:
        prompt = prompts.repair_sql(
            state["question"],
            state["schema_context"],
            state["error"],
            state.get("history", []),
        )
    else:
        prompt = prompts.generate_sql(state["question"], state["schema_context"])

    # "repair-sql" and "write-sql" are different rows in the trace on
    # purpose. Scanning a trace and seeing repair-sql tells me instantly
    # that this question needed a second go.
    reply, usage = llm.generate_text(
        prompt,
        system_instruction=prompts.SQL_SYSTEM,
        name="repair-sql" if repairing else "write-sql",
    )

    # The model saying the schema cannot answer this is a result, not a
    # failure. Ten of the forty evaluation questions are unanswerable on
    # purpose and getting them right is a score of its own.
    if prompts.REFUSAL_TOKEN in reply:
        reason = reply.split(prompts.REFUSAL_TOKEN, 1)[1].lstrip(": ").strip()
        return {
            "refused": True,
            "refusal_reason": reason or "The database does not hold this information.",
            "answer": reason or "The database does not hold this information.",
            "tokens": state.get("tokens", 0) + usage["total"],
            "cost": state.get("cost", 0.0) + usage["cost"],
        }

    return {
        "sql": llm.extract_sql(reply),
        "attempts": state.get("attempts", 0) + 1,
        "tokens": state.get("tokens", 0) + usage["total"],
        "cost": state.get("cost", 0.0) + usage["cost"],
    }


def validate_static(state):
    """
    Check the SQL before the database sees it.

    This node exists so that a query that was never going to work does
    not cost a round trip, and so that the rejection reason is mine and
    legible rather than a database error.

    The same check runs again inside tools.run_query. That is on purpose
    and not an oversight. run_query is also what the MCP server exposes,
    so it has to be safe on its own, whatever calls it. Parsing twice
    costs about a millisecond and buys an invariant I can state without
    caveats: nothing reaches the database without passing safety.check().
    """
    with tracing.observe(
        "validate-sql",
        as_type="guardrail",
        input={"sql": state["sql"]},
    ) as span:
        safe_sql, rejection = safety.check(state["sql"], tools.known_tables())

        if rejection:
            rejection["sql"] = state["sql"]
            span.update(
                output={"allowed": False, "error_type": rejection["error_type"]},
                level="WARNING",
                status_message=rejection["message"][:200],
            )
            return {"error": rejection, "history": _remember(state, rejection)}

        span.update(output={"allowed": True, "sql": safe_sql})

    return {"safe_sql": safe_sql, "error": None}


def execute(state):
    """Run it. Everything goes through the one gate."""
    with tracing.observe(
        "execute-sql",
        as_type="tool",
        input={"sql": state["safe_sql"]},
    ) as span:
        outcome = tools.run_query(state["safe_sql"])

        if not outcome.get("ok"):
            # The structured error goes on the span as an error, so a
            # failed attempt is red in the Langfuse list rather than
            # looking like every other row.
            span.update(
                output={"error_type": outcome["error_type"], "hint": outcome.get("hint")},
                level="ERROR",
                status_message=outcome["message"][:200],
            )
            return {"error": outcome, "result": None, "history": _remember(state, outcome)}

        span.update(
            output={
                "row_count": outcome.get("row_count", 0),
                "columns": outcome.get("columns", []),
                "error_type": outcome.get("error_type"),
            },
            metadata={"db_ms": outcome.get("ms")},
        )

    return {"result": outcome, "error": None}


def inspect_result(state):
    """
    Decide what the result actually means.

    Only the empty case does any work. A query that returned rows is
    done, and a query that errored has already been classified by
    errors.classify() on the way out of psycopg2.

    For the empty case, one diagnostic. See diagnose.py for why it is
    one and not a loop.
    """
    result = state.get("result") or {}

    if result.get("error_type") != "empty_result":
        return {}

    with tracing.observe(
        "diagnose-empty-result",
        as_type="evaluator",
        input={"sql": result["sql"]},
    ) as span:
        outcome = _diagnose_empty(state, result)
        span.update(output=outcome.get("diagnostic_note"))
        return outcome


def _diagnose_empty(state, result):
    """
    The one diagnostic, split out so the tracing above stays readable.

    See diagnose.py for why this is one attempt and not a loop.
    """
    # Only one thing here can justify a repair, and it is positive
    # evidence that a text filter is written in the wrong case.
    #
    # This used to also repair whenever removing the filter returned
    # rows, which is what the guide prescribes. The evaluation showed
    # that rule doing real damage. "Which products have never been sold"
    # is answered by WHERE NOT EXISTS (...), which correctly returns
    # nothing. Remove the NOT EXISTS and of course every product comes
    # back, so the rule declared the filter wrong, the agent rewrote a
    # query that was right, and after three goes it gave up on a
    # question it had answered correctly in four seconds.
    #
    # The same is true of any exact lookup and of IS NULL and NOT IN.
    # "Rows appear once you delete the condition that was doing the
    # work" is nearly always true and almost never informative.
    #
    # So the rule is now: repair only on evidence, never on suspicion.
    # If there is no specific, checkable reason to think the filter is
    # wrong, the honest answer is that there are no matching rows, and
    # being able to say that is the whole point of this path.
    probe_sql, described = diagnose.case_variant(result["sql"])

    if not probe_sql:
        return _no_data(
            "The query ran correctly and matched nothing, and there is no "
            "text filter that could be a spelling or casing mistake."
        )

    probe = tools.run_query(probe_sql)

    if not (probe.get("ok") and probe.get("row_count")):
        # The value is not in that column in any casing, so the filter is
        # fine and the answer really is "none".
        return _no_data(
            f"Nothing matches [{described}] even ignoring upper and lower case, "
            f"so that value is not in the database."
        )

    return _wrong_filter(
        state,
        result,
        described,
        f"The query matched no rows, but the same filter [{described}] "
        f"ignoring upper and lower case returns {probe['row_count']} row(s). "
        f"The value is stored in a different case or with different spacing.",
        f"[{described}] matches once case is ignored.",
    )


def _no_data(note):
    """The query was right and the answer is genuinely nothing."""
    return {"diagnostic_note": note, "error": None}


def _wrong_filter(state, result, described, message, note):
    """One repair, told exactly which filter looked wrong and why."""
    problem = {
        "ok": False,
        "error_type": "empty_filter",
        "message": message,
        "hint": None,
        "repairable": True,
        "sql": result["sql"],
    }
    return {
        "error": problem,
        "history": _remember(state, problem),
        "diagnostic_note": note,
        # Clear the empty result. If the repair goes on to fail and the
        # agent gives up, leaving this here would make ask() report the
        # first query as "the query it ran", which is the one query we
        # already know was wrong.
        "result": None,
    }


def write_answer(state):
    """Turn the rows into a sentence a person can read."""
    result = state["result"]

    if result.get("error_type") == "empty_result":
        prompt = prompts.write_empty_answer(
            state["question"],
            result["sql"],
            state.get("diagnostic_note", ""),
        )
    else:
        # Only the first few rows go to the model. The API caller still
        # gets everything; a hundred rows of context buys nothing and
        # costs a lot.
        shown = result["rows"][: config.ROWS_SHOWN_TO_MODEL]
        prompt = prompts.write_answer(
            state["question"],
            result["sql"],
            result["columns"],
            shown,
            truncated=result.get("truncated", False),
        )

    answer, usage = llm.generate_text(
        prompt,
        system_instruction=prompts.ANSWER_SYSTEM,
        name="write-answer",
    )

    return {
        "answer": answer.strip(),
        "tokens": state.get("tokens", 0) + usage["total"],
        "cost": state.get("cost", 0.0) + usage["cost"],
    }


def prepare_repair(state):
    """
    Set up the next attempt based on what kind of failure this was.

    This node is why the project is not a retry loop. Most error types
    only need the error text sent back, and generate_sql already does
    that. Two of them need something more:

      unknown_column / unknown_table
          The model reached for something that is not there. Nine times
          out of ten that means schema retrieval gave it the wrong
          tables, so widen the retrieval and rebuild the context. Asking
          the same model the same question over the same wrong tables
          will fail the same way three times.

      timeout
          Nothing to widen. The query was too expensive, and the repair
          instruction tells it to make the query cheaper.
    """
    error_type = state["error"]["error_type"]

    if error_type in ("unknown_column", "unknown_table") and not state.get("widened"):
        wide = schema_store.select_tables(
            state["question"],
            k=config.TABLES_RETRIEVED_WIDE,
        )
        names = [name for name, _score in wide]
        return {
            "widened": True,
            "tables": names,
            "schema_context": schema_store.build_context(names),
        }

    return {}


def give_up(state):
    """
    Fail honestly, and show the work.

    Three failed attempts and the answer is that it could not do it,
    with every query it tried and why each one failed. That is a useful
    answer. Something invented that looks like an answer is not.
    """
    error = state.get("error") or {}
    tried = "\n".join(
        f"  {number}. {item['error_type']}: {item['message']}"
        for number, item in enumerate(state.get("history", []), start=1)
    )

    return {
        "gave_up": True,
        "answer": (
            f"I could not answer this after {state.get('attempts', 0)} attempts. "
            f"The last problem was {error.get('error_type', 'unknown')}: "
            f"{error.get('message', 'no detail')}.\n\nWhat I tried:\n{tried}"
        ),
    }


# ---------------------------------------------------------------- edges


def after_generate(state):
    """A refusal skips everything else and goes straight out."""
    return END if state.get("refused") else "validate_static"


def after_validate(state):
    """Rejected here means the database is never touched."""
    return "decide" if state.get("error") else "execute"


def after_inspect(state):
    """Anything still marked as an error goes to the repair decision."""
    return "decide" if state.get("error") else "write_answer"


def decide(state):
    """
    The one place that decides whether to try again.

    Two ways to stop: the error is not the kind that repairing can fix,
    or the attempt cap is reached. Both end at give_up rather than at a
    made up answer.
    """
    error = state.get("error") or {}

    if not error.get("repairable", False):
        return "give_up"

    if state.get("attempts", 0) >= config.MAX_ATTEMPTS:
        return "give_up"

    return "prepare_repair"


def _remember(state, error):
    """Add one failure to the history the repair prompt will see."""
    return state.get("history", []) + [{
        "sql": error.get("sql", state.get("sql", "")),
        "error_type": error.get("error_type", "unknown"),
        "message": error.get("message", ""),
    }]


# ----------------------------------------------------------- the graph

_graph = None


def build_graph():
    """
    Wire the nodes up. Built once and reused.

    "decide" is a node that does nothing, which looks odd. LangGraph
    routes with conditional edges out of a node, so having a real node
    there means the three places that can fail all point at one decision
    instead of each carrying their own copy of the same rules. The
    attempt cap is then written in exactly one place.
    """
    graph = StateGraph(AgentState)

    graph.add_node("select_schema", select_schema)
    graph.add_node("generate_sql", generate_sql)
    graph.add_node("validate_static", validate_static)
    graph.add_node("execute", execute)
    graph.add_node("inspect_result", inspect_result)
    graph.add_node("write_answer", write_answer)
    graph.add_node("prepare_repair", prepare_repair)
    graph.add_node("give_up", give_up)
    graph.add_node("decide", lambda state: {})

    graph.set_entry_point("select_schema")

    graph.add_edge("select_schema", "generate_sql")
    graph.add_conditional_edges("generate_sql", after_generate)
    graph.add_conditional_edges("validate_static", after_validate)
    graph.add_edge("execute", "inspect_result")
    graph.add_conditional_edges("inspect_result", after_inspect)
    graph.add_conditional_edges("decide", decide)

    # The loop closes here. prepare_repair goes back to generate_sql,
    # which now writes with the error and the history in front of it.
    graph.add_edge("prepare_repair", "generate_sql")

    graph.add_edge("write_answer", END)
    graph.add_edge("give_up", END)

    return graph.compile()


def get_graph():
    global _graph
    if _graph is None:
        _graph = build_graph()
    return _graph


def ask(question):
    """
    Answer one question. This is what the API calls.

    Returns everything the frontend shows: the answer, the SQL that
    produced it, how many rows came back, how many attempts it took, and
    whether it refused or gave up.
    """
    started = time.perf_counter()

    # The whole graph runs inside one root observation, so every span the
    # nodes open lands underneath it and the trace comes out shaped like
    # the graph rather than as a flat list.
    with tracing.trace("sql-agent-answer", question, mode="agent") as root:
        final = get_graph().invoke({
            "question": question,
            "attempts": 0,
            "history": [],
            "tokens": 0,
            "cost": 0.0,
            "widened": False,
        })

        result = final.get("result") or {}
        latency_ms = int((time.perf_counter() - started) * 1000)

        answer = {
            "question": question,
            "answer": final.get("answer", ""),
            "sql": result.get("sql") or final.get("safe_sql") or final.get("sql"),
            "columns": result.get("columns", []),
            "rows": result.get("rows", []),
            "row_count": result.get("row_count", 0),
            "attempts": final.get("attempts", 0),
            "tables_used": final.get("tables", []),
            "refused": final.get("refused", False),
            "refusal_reason": final.get("refusal_reason", ""),
            "gave_up": final.get("gave_up", False),
            # Everything that failed on the way, which is what makes a
            # Langfuse trace of a repaired question worth watching.
            "history": final.get("history", []),
            "tokens": final.get("tokens", 0),
            "cost": round(final.get("cost", 0.0), 6),
            "latency_ms": latency_ms,
            "model": llm.model_name(),
        }

        root.update(
            output={
                "answer": answer["answer"],
                "sql": answer["sql"],
                "row_count": answer["row_count"],
            },
            metadata={
                "attempts": answer["attempts"],
                "tables_used": answer["tables_used"],
                "refused": answer["refused"],
                "gave_up": answer["gave_up"],
                # The list of what went wrong, so the trace list can be
                # filtered by failure type without opening each one.
                "failure_types": [item["error_type"] for item in answer["history"]],
                "latency_ms": latency_ms,
            },
        )

        # Read inside the block. Outside it the observation has closed and
        # there is no current trace any more, which cost me a confused ten
        # minutes of getting None back from a trace I could see in the UI.
        answer["trace_id"] = tracing.current_trace_id()

    answer["trace_url"] = tracing.trace_url(answer["trace_id"])
    return answer


if __name__ == "__main__":
    # python -m app.agent "your question"
    import sys

    question = " ".join(sys.argv[1:]) or "How many bills were raised this month?"
    outcome = ask(question)

    print(f"\nQ: {outcome['question']}")
    print(f"\nSQL ({outcome['attempts']} attempt(s)):\n{outcome['sql']}")
    print(f"\nA: {outcome['answer']}")
    print(f"\n{outcome['row_count']} rows, {outcome['latency_ms']} ms, {outcome['tokens']} tokens")

"""
The single shot version. Question in, one query out, no second chance.

This exists because a number with nothing to compare it to says nothing.
"Execution accuracy 0.72" could be very good or embarrassing and there is
no way to tell. "0.72 against 0.55 single shot, on the same forty
questions and the same model" is a claim, and it is the claim the whole
project rests on.

The guide says to build this deliberately, in week two, before the graph.
That is the right order and I nearly skipped it, because writing the
worse version of something you are about to build feels like waste. It is
not. It is the control.

It is deliberately not crippled. It gets the same schema retrieval, the
same prompt, the same model and the same safety envelope. The only thing
it does not get is a second attempt. So the difference between the two
numbers is the loop and nothing else, which is the only way the
comparison means anything.
"""

import time

from . import config, llm, prompts, schema_store, tools, tracing


def ask(question):
    """
    Answer one question with exactly one query.

    Returns the same shape as agent.ask(), so run_eval.py can run either
    of them through the same harness without caring which is which.
    """
    # Traced too, and tagged "baseline" so the Langfuse list can be
    # filtered to one mode or the other. Without that the two runs sit in
    # the same project looking identical, and the comparison I am trying
    # to make is invisible in the tool I built to see it.
    with tracing.trace("sql-baseline-answer", question, mode="baseline") as root:
        result = _ask(question)
        root.update(
            output={
                "answer": result["answer"],
                "sql": result["sql"],
                "row_count": result["row_count"],
            },
            metadata={
                "attempts": 1,
                "tables_used": result["tables_used"],
                "refused": result["refused"],
                "gave_up": result["gave_up"],
            },
        )
        result["trace_id"] = tracing.current_trace_id()

    result["trace_url"] = tracing.trace_url(result["trace_id"])
    return result


def _ask(question):
    """The single shot pipeline itself, with no tracing noise in it."""
    started = time.perf_counter()
    tokens = 0
    cost = 0.0

    picked = schema_store.select_tables(question, k=config.TABLES_RETRIEVED)
    tables = [name for name, _score in picked]
    context = schema_store.build_context(tables)

    reply, usage = llm.generate_text(
        prompts.generate_sql(question, context),
        system_instruction=prompts.SQL_SYSTEM,
        name="write-sql",
    )
    tokens += usage["total"]
    cost += usage["cost"]

    if prompts.REFUSAL_TOKEN in reply:
        reason = reply.split(prompts.REFUSAL_TOKEN, 1)[1].lstrip(": ").strip()
        return _shape(
            question=question,
            answer=reason or "The database does not hold this information.",
            refused=True,
            refusal_reason=reason,
            tables=tables,
            tokens=tokens,
            cost=cost,
            started=started,
        )

    sql = llm.extract_sql(reply)
    outcome = tools.run_query(sql)

    # One attempt means a failure is final. No repair, no diagnostic on
    # an empty result, which is exactly the point of the comparison.
    if not outcome.get("ok"):
        return _shape(
            question=question,
            answer=(
                f"The query failed with {outcome['error_type']}: {outcome['message']}"
            ),
            sql=outcome.get("sql", sql),
            gave_up=True,
            error_type=outcome["error_type"],
            tables=tables,
            tokens=tokens,
            cost=cost,
            started=started,
        )

    if outcome.get("error_type") == "empty_result":
        # No diagnostic here. The baseline reports the empty result as
        # it found it, which is sometimes right and sometimes a wrong
        # filter reported as fact.
        return _shape(
            question=question,
            answer="The query returned no rows.",
            sql=outcome["sql"],
            columns=outcome.get("columns", []),
            tables=tables,
            tokens=tokens,
            cost=cost,
            started=started,
        )

    shown = outcome["rows"][: config.ROWS_SHOWN_TO_MODEL]
    answer, usage = llm.generate_text(
        prompts.write_answer(
            question,
            outcome["sql"],
            outcome["columns"],
            shown,
            truncated=outcome.get("truncated", False),
        ),
        system_instruction=prompts.ANSWER_SYSTEM,
        name="write-answer",
    )
    tokens += usage["total"]
    cost += usage["cost"]

    return _shape(
        question=question,
        answer=answer.strip(),
        sql=outcome["sql"],
        columns=outcome["columns"],
        rows=outcome["rows"],
        row_count=outcome["row_count"],
        tables=tables,
        tokens=tokens,
        cost=cost,
        started=started,
    )


def _shape(question, answer, sql=None, columns=None, rows=None, row_count=0,
           refused=False, refusal_reason="", gave_up=False, error_type=None,
           tables=None, tokens=0, cost=0.0, started=None):
    """
    The same dictionary agent.ask() returns.

    attempts is always 1 and history is always empty. Those two fields
    are what the comparison table is measuring the absence of.
    """
    return {
        "question": question,
        "answer": answer,
        "sql": sql,
        "columns": columns or [],
        "rows": rows or [],
        "row_count": row_count,
        "attempts": 1,
        "tables_used": tables or [],
        "refused": refused,
        "refusal_reason": refusal_reason,
        "gave_up": gave_up,
        "error_type": error_type,
        "history": [],
        "tokens": tokens,
        "cost": round(cost, 6),
        "latency_ms": int((time.perf_counter() - started) * 1000) if started else 0,
        "model": llm.model_name(),
    }


if __name__ == "__main__":
    import sys

    question = " ".join(sys.argv[1:]) or "How many bills were raised this month?"
    outcome = ask(question)
    print(f"\nSQL:\n{outcome['sql']}\n\nA: {outcome['answer']}")

"""
The API. Four endpoints and nothing clever.

    POST /api/ask           ask a question
    GET  /api/schema        the tables, for the sidebar
    GET  /api/eval/latest   my own evaluation numbers
    GET  /api/health        is everything wired up

The third one is unusual and it is deliberate. Most projects put their
metrics in the README where they can quietly go stale. Serving them from
the same process means the number on the page is the number the last
evaluation run actually produced, and anyone can check it.

The answer always carries the SQL. That is a trust feature, not a debug
one. An answer with no query behind it has to be taken on faith, and the
whole point of this project is that it does not have to be.
"""

import json

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from . import agent, baseline, config, drift, query_log, tools, tracing

app = FastAPI(
    title="SQL Analyst Agent",
    description="Answers questions about a hardware shop database, and shows the SQL it used.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


class Question(BaseModel):
    question: str = Field(min_length=3, max_length=500)
    # Lets the frontend run the same question through the baseline, so
    # the comparison in the README can be reproduced by anyone rather
    # than taken on my word.
    mode: str = Field(default="agent", pattern="^(agent|baseline)$")


@app.post("/api/ask")
def ask(payload: Question):
    """
    Answer one question.

    Nothing here catches errors from the agent on purpose. A failure to
    write a query is already handled inside the graph and comes back as
    a normal answer with gave_up set. Anything that reaches this level
    is a real bug in my code and I would rather see the 500 than a
    friendly message hiding it.
    """
    runner = baseline if payload.mode == "baseline" else agent
    result = runner.ask(payload.question.strip())

    # trace_id and trace_url are set inside ask(), while the trace is
    # still open. They come back as None when tracing is switched off,
    # and the frontend simply does not draw the link.
    result["mode"] = payload.mode

    # Monitoring. Written after the answer is already assembled, and it
    # cannot raise, so a broken log never costs anyone their answer.
    query_log.record(result, mode=payload.mode)

    return result


@app.get("/api/schema")
def schema():
    """
    Every table with its description and rough size.

    The frontend shows this beside the question box. Someone who can see
    what is in the database asks better questions, and it makes an
    honest refusal understandable instead of looking like a failure.
    """
    return tools.list_tables()


@app.get("/api/eval/latest")
def latest_eval():
    """
    The most recent evaluation run, as run_eval.py wrote it.

    Returns ok: false rather than a 404 when no run exists yet, because
    the frontend showing "not run yet" is nicer than the frontend
    showing an error for a thing that is simply optional.
    """
    path = config.EVAL_DIR / "latest.json"
    if not path.exists():
        return {"ok": False, "message": "No evaluation has been run yet."}

    return json.loads(path.read_text(encoding="utf-8"))


@app.get("/api/health")
def health():
    """
    Is the database reachable, is the schema index built, is tracing on.

    The read only check is the useful line. It runs a DELETE and expects
    to be refused, so a deployment where the credential was set up wrong
    fails this check loudly instead of running for a month with a
    connection that can write.
    """
    tables = tools.known_tables(refresh=True)

    refusal = tools.run_query("DELETE FROM bills")
    read_only = not refusal.get("ok")

    drifted, _changes, message = drift.check()

    return {
        "ok": bool(tables) and read_only and not drifted,
        "tables": len(tables),
        "read_only_confirmed": read_only,
        "schema_index_built": config.SCHEMA_INDEX_PATH.exists(),
        # is_enabled() only says keys are present. check() actually calls
        # Langfuse, because a typo in the secret key looks exactly like
        # tracing working: nothing raises, the traces just never arrive.
        "tracing_enabled": tracing.is_enabled(),
        "tracing_authenticated": tracing.check(),
        "query_log_enabled": query_log.is_enabled(),
        "query_log_writable": query_log.check(),
        # The check that catches a migration having moved the database
        # underneath the schema descriptions. False here means the
        # descriptions and the vectors still match the live schema.
        "schema_drifted": drifted,
        "schema_drift": message if drifted else None,
        "model": config.GROQ_MODEL if config.LLM_PROVIDER == "groq" else config.GEMINI_MODEL,
    }

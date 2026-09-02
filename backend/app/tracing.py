"""
Langfuse tracing. One question becomes one readable trace.

Two rules, same as the scholarship project.

**It is optional.** With no keys in the .env every function here does
nothing and the agent runs exactly as before. Nobody should need a
Langfuse account to run this project.

**It never breaks a request.** Tracing is for my benefit. If it fails,
the person still gets their answer, so everything is wrapped and failures
are printed once rather than raised.

**What is different here is the shape of the trace, and it is the best
thing this project has to demo.** A flat trace saying "asked a question,
got an answer" is worth nothing. This one nests:

    sql-agent-answer                     the whole question
      select-schema        retriever     which tables were chosen, and why
      attempt-1            span
        write-sql          generation    model, tokens, cost
        validate-sql       span
        execute-sql        span          the structured error lands here
      attempt-2            span
        repair-sql         generation    the repair, with the error in it
        validate-sql       span
        execute-sql        span          ok this time
      write-answer         generation

Opening that on a question that failed first time and succeeded second
shows the entire argument of the project in about thirty seconds, without
me having to explain it.

Everything in here degrades to a no-op object rather than to an `if`.
That matters: agent.py calls `with tracing.observe(...)` unconditionally,
so the graph code reads the same whether tracing is on or off, and there
is no second untraced code path that could quietly behave differently.

Written against the Langfuse Python SDK v4. v2 and v3 had different APIs
(`langfuse.trace()` and `start_as_current_span()` respectively), so the
version in requirements.txt is pinned for a reason.
"""

from contextlib import contextmanager

from . import config

_enabled = bool(config.LANGFUSE_PUBLIC_KEY and config.LANGFUSE_SECRET_KEY)
_client = None
_warned = False


def is_enabled():
    return _enabled


def get_client():
    """Build the client once, or return None if it will not build."""
    global _client, _enabled

    if not _enabled:
        return None

    if _client is None:
        try:
            from langfuse import Langfuse

            _quieten_the_exporter()

            _client = Langfuse(
                public_key=config.LANGFUSE_PUBLIC_KEY,
                secret_key=config.LANGFUSE_SECRET_KEY,
                base_url=config.LANGFUSE_BASE_URL,
                environment=config.LANGFUSE_ENVIRONMENT,
            )
        except Exception as error:
            print(f"Langfuse would not start, carrying on without it: {error}")
            _enabled = False
            return None

    return _client


def _quieten_the_exporter():
    """
    Stop the OpenTelemetry exporter shouting when the network is down.

    The SDK v4 ships traces over OTLP on a background thread. When that
    cannot reach Langfuse it logs a multi-line warning per retry, per
    batch, at WARNING level, straight to stderr.

    My home connection dropped in the middle of a forty question
    evaluation run and the output became unreadable: every line of the
    actual results was buried under DNS resolution failures for a service
    that is, by design, entirely optional. The run itself was unaffected,
    which is the tracing design working exactly as intended, but I could
    not see that at the time because I could not read the log.

    So the exporter's logger is raised to ERROR. Nothing is hidden that
    matters: a permanently broken exporter still shows up as
    tracing_authenticated false on /api/health, which is a check rather
    than a log line and is where I would look anyway.
    """
    import logging

    for name in (
        "opentelemetry.sdk.trace.export",
        "opentelemetry.exporter.otlp.proto.http.trace_exporter",
        "opentelemetry.exporter.otlp.proto.http._log_exporter",
    ):
        logging.getLogger(name).setLevel(logging.ERROR)


def check():
    """
    Are the keys actually valid?

    Called by /api/health. Without this, a typo in the secret key looks
    exactly like tracing working: nothing raises, the traces just never
    arrive, and I would find out days later by wondering why the list was
    empty.
    """
    client = get_client()
    if client is None:
        return False
    try:
        return bool(client.auth_check())
    except Exception:
        return False


# ---------------------------------------------------------- no-op mode


class _NullSpan:
    """
    What the context manager yields when tracing is switched off.

    It swallows update() so the calling code does not need to know. The
    alternative is an `if tracing.is_enabled()` around every span in
    agent.py, which would double the size of the graph code and create a
    second, less tested path through it.
    """

    def update(self, **kwargs):
        pass

    def update_trace(self, **kwargs):
        pass


_NULL = _NullSpan()


# A note on how the two context managers below are written, because the
# obvious way is wrong and I wrote it the obvious way first.
#
# The tempting shape is:
#
#     try:
#         with client.start_as_current_observation(...) as span:
#             yield span
#     except Exception:
#         yield _NULL          # <- wrong
#
# That try does not only cover setting the observation up. It also covers
# the entire body of the caller's `with` block, because that is where the
# yield hands control back. So any exception raised by the code being
# traced gets caught here, and then the generator yields a second time,
# and Python raises "generator didn't stop after throw()". The real error
# is replaced by a confusing one about generators, from a file whose only
# job is to be optional.
#
# It cost me a real debugging session: a bad argument to the Groq client
# surfaced as a generator error thrown by the tracing module.
#
# So the try only wraps the setup. Once the observation exists, the
# caller's exceptions travel straight through the real context manager,
# which is what I want anyway: Langfuse marks the observation as failed
# and re-raises, so the trace shows the error and the caller still sees
# its own exception.


@contextmanager
def observe(name, as_type="span", input=None, **kwargs):
    """
    One observation, nested under whatever is already open.

    as_type is the Langfuse observation type and picking it correctly is
    not cosmetic. A "generation" gets model and token columns and a cost
    calculation. A "retriever" is shown as retrieval. Marking everything
    as a plain span throws all of that away.
    """
    client = get_client()
    if client is None:
        yield _NULL
        return

    try:
        observation = client.start_as_current_observation(
            name=name,
            as_type=as_type,
            input=input,
            **kwargs,
        )
    except Exception as error:
        # Only a failure to start tracing lands here. Tracing being
        # broken must not stop the answer, so carry on with the no-op.
        _warn_once(error)
        yield _NULL
        return

    with observation as span:
        yield span


@contextmanager
def trace(name, question, mode="agent"):
    """
    The root observation for one question.

    propagate_attributes is what puts the trace name, the tags and the
    session on the trace itself rather than on this one span, so the
    Langfuse list view is filterable by mode and the eval runs can be
    told apart from my manual questions.
    """
    client = get_client()
    if client is None:
        yield _NULL
        return

    try:
        from langfuse import propagate_attributes

        observation = client.start_as_current_observation(
            name=name,
            as_type="agent",
            # Only the question goes in, not the whole state. Dumping
            # every argument is the most common instrumentation mistake
            # and it makes the trace unreadable at exactly the moment
            # you need to read it.
            input={"question": question},
        )
        attributes = propagate_attributes(
            trace_name=name,
            tags=[mode],
            metadata={"mode": mode},
        )
    except Exception as error:
        _warn_once(error)
        yield _NULL
        return

    # Setup is done, so from here the caller's exceptions pass straight
    # through. See the note above the observe() function.
    with observation as span, attributes:
        yield span


def current_trace_id():
    """The id of the trace being written, so the API can link to it."""
    client = get_client()
    if client is None:
        return None
    try:
        return client.get_current_trace_id()
    except Exception:
        return None


def trace_url(trace_id):
    """A clickable link, or None when tracing is off."""
    client = get_client()
    if client is None or not trace_id:
        return None
    try:
        return client.get_trace_url(trace_id=trace_id)
    except Exception:
        return None


def score(trace_id, name, value, comment=None):
    """
    Attach a score to a finished trace.

    Used by the evaluation: every one of the forty questions is scored
    correct or not, against the same trace that shows how the answer was
    produced. That turns the Langfuse project into a browsable record of
    which questions fail and what they failed on, which a JSON file on
    disk cannot do.
    """
    client = get_client()
    if client is None or not trace_id:
        return
    try:
        client.create_score(trace_id=trace_id, name=name, value=value, comment=comment)
    except Exception as error:
        _warn_once(error)


def flush():
    """
    Push anything still queued.

    The SDK batches in the background, so a script that finishes and
    exits loses its last few traces without this. run_eval.py and
    scripts/ask.py both call it before they end.
    """
    client = get_client()
    if client is not None:
        try:
            client.flush()
        except Exception:
            pass


def _warn_once(error):
    """
    Complain the first time and then stay quiet.

    If tracing is broken it is broken for every question, and forty
    identical warnings during an evaluation run would bury the output I
    actually came to read.
    """
    global _warned
    if not _warned:
        print(f"Langfuse trace failed, carrying on without it: {error}")
        _warned = True


if __name__ == "__main__":
    # python -m app.tracing
    if not is_enabled():
        print("tracing is off, no keys in the .env")
    else:
        print(f"keys valid: {check()}")
        with trace("tracing-smoke-test", "is this thing on?") as span:
            with observe("a-child-span", input={"hello": "world"}) as child:
                child.update(output={"ok": True})
            span.update(output={"ok": True})
            print(f"trace: {trace_url(current_trace_id())}")
        flush()

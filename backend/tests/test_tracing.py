"""
Tracing must be invisible when it is off, and transparent when it is on.

These tests exist because of one bug that cost me a real debugging
session. app/tracing.py wraps its context managers in try/except so that
a broken Langfuse account cannot stop the agent answering. Written the
obvious way, that try also covers the body of the caller's `with` block,
because that is where `yield` hands control back. So a completely
unrelated exception from the code being traced got caught by the tracing
module, which then yielded a second time, and Python replaced the real
error with "generator didn't stop after throw()".

The symptom was a bad argument to the Groq client showing up as a
generator error thrown by a file whose only job is to be optional.

So: an exception raised inside a traced block must come out the other
side unchanged, with its original type and message, whether tracing is
on or off. That is what these check.
"""

import pytest

from app import tracing


class Boom(Exception):
    """A distinctive exception, so there is no doubt which one arrived."""


# ------------------------------------------------------- tracing is off


@pytest.fixture
def tracing_off(monkeypatch):
    monkeypatch.setattr(tracing, "_enabled", False)
    monkeypatch.setattr(tracing, "_client", None)


def test_spans_do_nothing_when_tracing_is_off(tracing_off):
    """
    The no-op object has to accept update() silently.

    agent.py calls update() on whatever it is given without checking. If
    the null object did not swallow that, turning tracing off would break
    the agent, which is the opposite of optional.
    """
    with tracing.observe("anything") as span:
        span.update(output={"fine": True}, metadata={"also": "fine"})

    with tracing.trace("a-trace", "a question") as root:
        root.update(output="fine")

    assert tracing.current_trace_id() is None
    assert tracing.trace_url("whatever") is None


def test_an_error_inside_a_span_is_not_swallowed_when_tracing_is_off(tracing_off):
    with pytest.raises(Boom):
        with tracing.observe("failing-span"):
            raise Boom("the real error")


def test_an_error_inside_a_trace_is_not_swallowed_when_tracing_is_off(tracing_off):
    with pytest.raises(Boom):
        with tracing.trace("failing-trace", "a question"):
            raise Boom("the real error")


# -------------------------------------------------------- tracing is on


def tracing_is_live():
    return tracing.is_enabled() and tracing.check()


live_only = pytest.mark.skipif(
    not tracing_is_live(),
    reason="no Langfuse keys in the .env, or they are not valid",
)


@live_only
def test_an_error_inside_a_real_span_keeps_its_own_type():
    """
    The regression test for the bug in the docstring above.

    Before the fix this raised RuntimeError("generator didn't stop after
    throw()") instead of Boom, and the original error was gone.
    """
    with pytest.raises(Boom, match="the real error"):
        with tracing.observe("deliberately-failing-span"):
            raise Boom("the real error")

    tracing.flush()


@live_only
def test_an_error_inside_a_real_trace_keeps_its_own_type():
    with pytest.raises(Boom, match="the real error"):
        with tracing.trace("deliberately-failing-trace", "a question"):
            raise Boom("the real error")

    tracing.flush()


@live_only
def test_a_real_trace_has_an_id_and_a_url():
    """
    The trace id has to be read inside the block.

    Outside it the observation has closed and there is no current trace,
    which is why agent.ask() reads it before leaving the `with`.
    """
    with tracing.trace("a-real-trace", "a question") as root:
        root.update(output="done")
        trace_id = tracing.current_trace_id()

    assert trace_id
    url = tracing.trace_url(trace_id)
    assert url and trace_id in url

    tracing.flush()

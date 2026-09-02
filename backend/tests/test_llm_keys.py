"""
Key rotation when the daily token budget runs out.

These exist because losing most of an evaluation run to a 429 cost me a
day, and the fix is only worth having if it actually fires. No network:
rotation is a decision about an error message, and that decision is what
is tested.

The distinction being checked is the one that matters. Groq has two rate
limits and they need opposite responses:

    tokens per minute   a burst. Sleep and it clears.
    tokens per day      the free tier's 200,000. Sleeping is pointless,
                        so use the other key.

Treating the second like the first is what makes a run sit there backing
off for an hour and then fail anyway.
"""

import pytest

from app import config, llm


@pytest.fixture(autouse=True)
def reset_key_index():
    """Every test starts on the primary key."""
    llm._key_index = 0
    yield
    llm._key_index = 0


DAILY = (
    "Error code: 429 - Rate limit reached for model `openai/gpt-oss-120b` in "
    "organization `org_x` service tier `on_demand` on tokens per day (TPD): "
    "Limit 200000, Used 199157, Requested 2193. Please try again in 9m43.2s."
)

PER_MINUTE = (
    "Error code: 429 - Rate limit reached for model `openai/gpt-oss-120b` on "
    "tokens per minute (TPM): Limit 8000, Used 7900, Requested 300. "
    "Please try again in 1.5s."
)


def test_the_daily_limit_is_recognised():
    assert llm.is_daily_limit(DAILY) is True


def test_a_per_minute_burst_is_not_treated_as_the_daily_limit():
    """
    The one that would hurt if it were wrong. Rotating keys on a per
    minute burst would burn the spare key's daily budget to avoid a one
    and a half second wait.
    """
    assert llm.is_daily_limit(PER_MINUTE) is False


def test_an_unrelated_error_is_not_a_daily_limit():
    assert llm.is_daily_limit("connection reset by peer") is False


def test_rotation_moves_to_the_spare_key(monkeypatch):
    monkeypatch.setattr(config, "GROQ_API_KEY", "gsk_primary")
    monkeypatch.setattr(config, "GROQ_API_KEY_EXTRA", "gsk_spare")

    assert llm.current_key_label() == "primary"
    assert llm.rotate_key() is True
    assert llm.current_key_label() == "spare-1"


def test_rotation_is_refused_when_there_is_nowhere_to_go(monkeypatch):
    """
    With one key configured there is no spare, so the caller has to fall
    back to ordinary backoff rather than pretending it rotated.
    """
    monkeypatch.setattr(config, "GROQ_API_KEY", "gsk_primary")
    monkeypatch.setattr(config, "GROQ_API_KEY_EXTRA", "")

    assert llm.rotate_key() is False
    assert llm.current_key_label() == "primary"


def test_a_blank_spare_is_not_counted_as_a_key(monkeypatch):
    monkeypatch.setattr(config, "GROQ_API_KEY", "gsk_primary")
    monkeypatch.setattr(config, "GROQ_API_KEY_EXTRA", "")

    assert llm._keys() == ["gsk_primary"]


def test_the_key_itself_never_appears_in_a_label(monkeypatch):
    """
    current_key_label() goes into printed output and into the trace
    metadata, so it must never be the credential.
    """
    monkeypatch.setattr(config, "GROQ_API_KEY", "gsk_secret_value_here")
    monkeypatch.setattr(config, "GROQ_API_KEY_EXTRA", "gsk_other_secret")

    for _ in range(3):
        assert "gsk_" not in llm.current_key_label()
        llm.rotate_key()

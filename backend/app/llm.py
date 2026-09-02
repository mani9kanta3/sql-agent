"""
One place that talks to a language model.

Three parts of the project call one: writing the SQL, writing the final
answer, and deciding whether a question can be answered from this schema
at all. They all come through here so retries and token counting are
written once.

Same two doors as the scholarship project. The guide says Gemini or
Claude and either is fine; I am on Groq because the free tier is
generous enough to run a forty question evaluation several times in a
day, which is what this project needs more than it needs the best model.
LLM_PROVIDER in the .env swaps it and nothing else in the code knows
which one is running.

Temperature is 0 everywhere and that is not a default I left alone. The
same question must produce the same SQL every time, or the evaluation
numbers are measuring randomness. If a change to a prompt moves
execution accuracy by two points I need to know that the prompt did it.
"""

import re
import time

from . import config, tracing

# Reasoning models put their working in these. Most providers can be
# asked to hide it, not all of them honour the request, so it is stripped
# here as well. A leaked thinking block would go straight into the SQL
# parser and fail as a syntax error, which is a confusing way to find out.
THINKING_PATTERN = re.compile(r"<think>.*?</think>\s*", re.DOTALL | re.IGNORECASE)

# Failures worth trying again. 429 is a rate limit, the 5xx family is the
# provider being busy. Neither means my request was wrong, and neither
# should end a forty question evaluation run on question six.
EMPTY_REPLY = "the model returned an empty reply"

RETRYABLE = [
    "429",
    "500",
    "502",
    "503",
    "504",
    "RESOURCE_EXHAUSTED",
    "UNAVAILABLE",
    "overloaded",
    EMPTY_REPLY,
]

# Model families that think before answering. Only these accept the
# reasoning options below; the rest reject the request outright.
REASONING_MODELS = ("openai/gpt-oss", "qwen/", "deepseek")

_groq_client = None
_gemini_client = None


def using_groq():
    return config.LLM_PROVIDER == "groq"


def model_name():
    """Which model is answering, for the eval config and the README."""
    return config.GROQ_MODEL if using_groq() else config.GEMINI_MODEL


def strip_thinking(text):
    """Take the reasoning block out of a reply, if one leaked through."""
    if not text:
        return ""

    cleaned = THINKING_PATTERN.sub("", text)

    # An unclosed <think> means the reply was cut off mid thought, so
    # there is no answer after it. Keep what came before.
    if "<think>" in cleaned:
        cleaned = cleaned.split("<think>")[0]

    return cleaned.strip()


# ---------------------------------------------------------------- Groq


# Groq has two rate limits and they need different responses.
#
#   tokens per minute   a burst. Waiting a few seconds fixes it.
#   tokens per day      the free tier's 200,000. Waiting does not fix it
#                       within any useful timeframe.
#
# One evaluation run over both modes costs about 180,000 tokens, so a
# single key gives you one experiment a day and no room to re-run after
# a fix. I lost most of a run to a daily 429 thirty-eight questions in,
# having "checked" for quota with a hundred token probe that succeeded.
# A small probe cannot tell you a large budget is free.
#
# So a daily exhaustion rotates to the next key instead of sleeping. A
# per minute limit still backs off, because that one really does clear.
_clients = {}
_key_index = 0


def _keys():
    """Every Groq key configured, in the order they should be used."""
    return [key for key in (config.GROQ_API_KEY, config.GROQ_API_KEY_EXTRA) if key]


def get_groq():
    """The client for the key currently in use, built once per key."""
    keys = _keys()
    if not keys:
        raise RuntimeError("GROQ_API_KEY is empty. Put your key in backend/.env first.")

    key = keys[_key_index % len(keys)]
    if key not in _clients:
        from groq import Groq

        _clients[key] = Groq(api_key=key)
    return _clients[key]


def current_key_label():
    """Which key is in use, for logging. Never the key itself."""
    keys = _keys()
    if not keys:
        return "none"
    return "primary" if _key_index % len(keys) == 0 else f"spare-{_key_index % len(keys)}"


def is_daily_limit(message):
    """
    Is this the daily token cap rather than a per minute burst?

    Groq says "on tokens per day (TPD)" in the message and gives a retry
    delay in minutes rather than seconds. Matching on the phrase is
    string matching against a provider's error text, which I would rather
    not rely on, so anything unrecognised falls through to the ordinary
    backoff and is simply slower.
    """
    lowered = message.lower()
    return "tokens per day" in lowered or "tpd" in lowered


def rotate_key():
    """
    Move to the next key. Returns False when there is nowhere to go.

    Deliberately does not wrap back to a key already known to be
    exhausted within one call; the caller counts its rotations.
    """
    global _key_index
    if len(_keys()) < 2:
        return False
    _key_index += 1
    return True


def _groq_call(prompt, system_instruction, temperature):
    """One Groq request. Returns (text, tokens)."""
    model = config.GROQ_MODEL

    messages = []
    if system_instruction:
        messages.append({"role": "system", "content": system_instruction})
    messages.append({"role": "user", "content": prompt})

    extra = {}

    # Writing SQL over a schema is a lookup and a join, not a puzzle.
    # "low" costs nothing that matters here and roughly triples how many
    # evaluation runs fit in a day's token allowance.
    if model.startswith("openai/gpt-oss"):
        extra["reasoning_effort"] = "low"

    # Keep the thinking out of the reply. Sending this to a model that
    # does not think returns a 400 and kills the request, so it is only
    # sent to the families that take it.
    if any(model.startswith(family) for family in REASONING_MODELS):
        extra["reasoning_format"] = "hidden"

    response = get_groq().chat.completions.create(
        model=model,
        messages=messages,
        temperature=temperature,
        **extra,
    )

    text = strip_thinking(response.choices[0].message.content or "")

    if not text:
        raise RuntimeError(EMPTY_REPLY)

    return text, _usage_of(response.usage)


# -------------------------------------------------------------- Gemini


def get_gemini():
    global _gemini_client
    if _gemini_client is None:
        if not config.GEMINI_API_KEY:
            raise RuntimeError("GEMINI_API_KEY is empty. Put your key in backend/.env first.")
        from google import genai

        _gemini_client = genai.Client(api_key=config.GEMINI_API_KEY)
    return _gemini_client


def _gemini_call(prompt, system_instruction, temperature):
    """One Gemini request. Returns (text, tokens)."""
    from google.genai import types

    response = get_gemini().models.generate_content(
        model=config.GEMINI_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            system_instruction=system_instruction,
            temperature=temperature,
            # The SDK assumes I might hand it Python functions to call
            # and warns on every request. This project never does.
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        ),
    )

    text = strip_thinking(response.text or "")

    if not text:
        raise RuntimeError(EMPTY_REPLY)

    meta = getattr(response, "usage_metadata", None)
    usage = {
        "input": getattr(meta, "prompt_token_count", 0) or 0,
        "output": getattr(meta, "candidates_token_count", 0) or 0,
        "total": getattr(meta, "total_token_count", 0) or 0,
    }
    return text, usage


# ------------------------------------------------------------- the door


def _usage_of(usage):
    """
    Token counts in the shape Langfuse expects.

    The keys have to be "input", "output" and "total". Langfuse multiplies
    those by the model's published rates to work out cost, so getting the
    names wrong means the traces show tokens but no money, which is the
    number I actually want when comparing the agent against the baseline.
    """
    if usage is None:
        return {"input": 0, "output": 0, "total": 0}

    return {
        "input": getattr(usage, "prompt_tokens", 0) or 0,
        "output": getattr(usage, "completion_tokens", 0) or 0,
        "total": getattr(usage, "total_tokens", 0) or 0,
    }


def generate_text(prompt, system_instruction=None, temperature=0.0, retries=5,
                  name="llm-call"):
    """
    Ask for a plain text answer.

    The wait doubles each time and stops at 60 seconds. A fixed wait
    walks back into the same rate limit, and one that keeps doubling
    eventually sits there for a quarter of an hour, which is not
    retrying any more.

    `name` is what the call is called in the trace. Passing it in means
    the trace reads "write-sql", "repair-sql", "write-answer" instead of
    three identical rows, which is the difference between a trace you can
    skim and one you have to click through.

    Every model call is traced from here and nowhere else, so there is no
    way to add a new call site and forget to instrument it.

    Returns (text, usage), where usage has input, output, total
    and cost.
    """
    send = _groq_call if using_groq() else _gemini_call

    with tracing.observe(
        name,
        as_type="generation",
        model=model_name(),
        input=_traced_input(prompt, system_instruction),
        model_parameters={"temperature": temperature},
    ) as generation:
        rotations = 0

        for attempt in range(retries):
            try:
                text, usage = send(prompt, system_instruction, temperature)
            except Exception as error:
                message = str(error)

                # The daily cap. Sleeping does not clear this inside any
                # useful timeframe, so move to the spare key and retry at
                # once. Only worth doing once per spare key available.
                if (using_groq() and is_daily_limit(message)
                        and rotations < len(_keys()) - 1 and rotate_key()):
                    rotations += 1
                    print(f"  daily token limit hit, switching to the {current_key_label()} key")
                    continue

                if not any(code in message for code in RETRYABLE) or attempt == retries - 1:
                    # Mark the generation as failed rather than letting it
                    # close looking successful with no output on it.
                    generation.update(level="ERROR", status_message=message[:200])
                    raise

                wait = min(2 ** (attempt + 1), 60)
                print(f"  provider said no ({message[:60]}...), waiting {wait}s")
                time.sleep(wait)
                continue

            cost = _cost_of(usage)
            generation.update(
                output=text,
                usage_details=usage,
                cost_details=cost,
            )
            # The whole usage dict goes back, not just a token count.
            # Monitoring wants cost per question and the guide is right
            # that it should be tracked rather than assumed, so the real
            # figure travels with the call that produced it instead of
            # being reconstructed later from an average.
            usage["cost"] = cost["total"]
            return text, usage


def _cost_of(usage):
    """
    What this call cost, in dollars.

    Worked out here because Langfuse has no price list for Groq's models,
    so left to itself it reports every generation as costing zero. The
    comparison between the agent and the single shot baseline is partly
    an argument about what the extra accuracy costs, and that argument
    needs a real number.
    """
    input_cost = usage["input"] / 1_000_000 * config.MODEL_INPUT_COST_PER_M
    output_cost = usage["output"] / 1_000_000 * config.MODEL_OUTPUT_COST_PER_M

    return {
        "input": input_cost,
        "output": output_cost,
        "total": input_cost + output_cost,
    }


def _traced_input(prompt, system_instruction):
    """
    What the prompt looks like in the trace.

    Sent as a message list rather than one blob, because that is the
    shape Langfuse renders as a conversation. The schema block makes
    these prompts long, and a long prompt is exactly what I want to be
    able to read when an answer is wrong.
    """
    messages = []
    if system_instruction:
        messages.append({"role": "system", "content": system_instruction})
    messages.append({"role": "user", "content": prompt})
    return messages


# SQL almost always comes back inside a fenced code block, sometimes with
# a sentence in front of it. Rather than begging the prompt for bare SQL
# and being let down occasionally, the fence is just handled.
FENCE_PATTERN = re.compile(r"```(?:sql)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


def extract_sql(text):
    """Pull the SQL out of a reply, fenced or not."""
    if not text:
        return ""

    match = FENCE_PATTERN.search(text)
    if match:
        return match.group(1).strip().rstrip(";").strip()

    # No fence. Take everything from the first SELECT or WITH, which
    # drops any "Here is the query:" preamble.
    start = re.search(r"\b(SELECT|WITH)\b", text, re.IGNORECASE)
    if start:
        return text[start.start():].strip().rstrip(";").strip()

    return text.strip().rstrip(";").strip()


if __name__ == "__main__":
    print(f"provider: {config.LLM_PROVIDER}   model: {model_name()}")
    reply, tokens = generate_text("Reply with exactly: the model is working")
    print(f"{reply.strip()}   ({tokens} tokens)")

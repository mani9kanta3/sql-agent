"""
Ask a question from the command line, without starting the API.

    python -m scripts.ask "which product sold the most units in 2026?"
    python -m scripts.ask --baseline "what was our revenue in 2023?"

Useful when I want to see what happened rather than just the answer. It
prints every failed attempt and the error type that came back, which is
the same thing the Langfuse trace shows, minus the browser.
"""

import argparse

from app import agent, baseline, console, tracing


def main():
    # Answers contain a rupee sign and the Windows console is cp1252.
    console.use_utf8()

    parser = argparse.ArgumentParser()
    parser.add_argument("question", nargs="+")
    parser.add_argument("--baseline", action="store_true", help="single shot, no repair loop")
    args = parser.parse_args()

    question = " ".join(args.question)
    runner = baseline if args.baseline else agent

    result = runner.ask(question)

    print(f"\nQ: {question}")

    # The failed attempts first, in order, because the interesting part
    # of a repaired question is what went wrong before it went right.
    for number, item in enumerate(result.get("history", []), start=1):
        print(f"\n  attempt {number} failed with {item['error_type']}")
        print(f"    {item['message']}")
        print(f"    {item['sql']}")

    if result.get("refused"):
        print(f"\nRefused: {result['refusal_reason']}")
    elif result.get("gave_up"):
        print(f"\nGave up:\n{result['answer']}")
    else:
        print(f"\nSQL (attempt {result['attempts']}):\n{result['sql']}")
        print(f"\nA: {result['answer']}")
        print(f"\n{result['row_count']} row(s)")

    print(f"\n{result['latency_ms']} ms, {result['tokens']} tokens, {result['model']}")

    if result.get("trace_url"):
        print(f"trace: {result['trace_url']}")

    # A script that exits without this loses whatever the SDK still has
    # batched, which for a one question run is usually the whole trace.
    tracing.flush()


if __name__ == "__main__":
    main()

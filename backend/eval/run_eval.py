"""
The measurement. Run the forty questions and score what comes back.

    python -m eval.run_eval              the agent
    python -m eval.run_eval --baseline   the single shot version
    python -m eval.run_eval --both       both, and write the comparison
    python -m eval.run_eval --regrade    re-score a finished run, no model calls

**Grading is on execution accuracy, not on the SQL text.** Two queries
that read nothing alike can both be right. So the agent's query is run,
the reference query is run, and the two result sets are compared. That is
how every serious text to SQL benchmark grades and it is the only method
that does not punish a correct answer for being written differently.

**The comparison is deliberately not exact string equality on rows.**
Column names are ignored, because the agent naming a column "total" and
me naming it "revenue" is not a wrong answer. Numbers are rounded to two
decimal places, because NUMERIC and DOUBLE PRECISION can disagree in the
tenth decimal and that is not a wrong answer either. Row order is ignored
unless it decides the answer, which for these questions it does not,
since the ones that care use LIMIT. And the agent may return more columns
than the reference, as long as the reference's columns are all there with
matching values. See compare() for why that last one is not me being
generous to my own agent.

Everything else is a fail, including a right number reached through the
wrong table.

**--regrade re-scores without calling the model.** The saved results keep
the SQL the agent wrote, so a change to the grading rule can be applied
to a finished run by re-running those queries. That matters because a
full run costs about forty minutes and a lot of tokens, and I should be
able to fix a bug in my own comparator without paying for that twice.
It also keeps me honest: the run is fixed and only the grading changes,
so I cannot quietly re-roll a bad result.
"""

import argparse
from itertools import permutations
import json
import statistics
from datetime import datetime
from decimal import Decimal

from app import agent, baseline, config, console, db, llm, tracing
from eval.questions import QUESTIONS


# ------------------------------------------------------------- grading


def normalise(value):
    """
    Make one cell comparable.

    Decimal, float and int all have to compare equal when they hold the
    same number, because SUM() over NUMERIC gives a Decimal and the same
    sum written slightly differently can come back as a float. Rounding
    to two places is right for money and fine for counts.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float, Decimal)):
        return round(float(value), 2)
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value).strip()


def as_rows(result):
    """A result set as a sorted list of value tuples, names dropped."""
    if not result:
        return []
    columns = result["columns"]
    rows = [tuple(normalise(row.get(column)) for column in columns) for row in result["rows"]]
    # Sorted so that row order does not decide correctness. str() on the
    # key because a row can mix numbers, text and None, and Python will
    # not compare those to each other.
    return sorted(rows, key=lambda row: str(row))


def compare(agent_result, expected_result):
    """
    Did the agent's query produce the same answer as the reference?

    Two ways to pass:

      1. The result sets are identical. The normal case.

      2. The reference's columns are all present in the agent's result,
         with the same values on the same rows. Extra columns are
         allowed; missing ones are not.

    Rule 2 exists because grading on exact column-for-column equality
    measures the wrong thing. Asked "which products are below their
    reorder level", my reference happens to select the name, the stock
    and the reorder level. An agent that returns just the names has
    answered the question correctly and would be marked wrong for being
    less verbose than me. An agent that returns the names plus the
    category has also answered it, and would be marked wrong for being
    more verbose.

    Neither is a SQL mistake. It is my reference query making an
    arbitrary choice about presentation, and the metric should not be
    measuring my choices.

    So the test is: is the reference's answer contained in the agent's?
    Every reference column has to appear, with matching values row for
    row, which is what stops a query that returned the right names but
    the wrong quantities from passing. Anything the agent adds on top is
    ignored.

    This is looser than the strictest reading of execution accuracy and I
    would say so in an interview. It is looser in one specific direction:
    it forgives presentation, not correctness. Dropping a required column
    still fails, and a right number reached through the wrong table still
    fails, because the numbers will not line up.

    Returns (matched, reason).
    """
    expected_rows = as_rows(expected_result)
    agent_rows = as_rows(agent_result)

    if agent_rows == expected_rows:
        return True, "exact"

    if not expected_result or not agent_result:
        return False, f"expected {len(expected_rows)} row(s), got {len(agent_rows)}"

    expected_width = len(expected_result["columns"])
    agent_width = len(agent_result["columns"])

    if expected_width > agent_width or len(expected_rows) != len(agent_rows):
        return False, f"expected {len(expected_rows)} row(s), got {len(agent_rows)}"

    # Try every way of picking expected_width columns out of the agent's
    # result, in order. Both are small: at most a handful of columns
    # each, so this is tens of comparisons, not thousands.
    #
    # Rows are re-sorted after projecting, because two rows that differ
    # only in a column being dropped collapse to the same tuple and the
    # original sort order no longer holds.
    for picked in permutations(range(agent_width), expected_width):
        projected = sorted(
            (tuple(row[index] for index in picked) for row in agent_rows),
            key=lambda row: str(row),
        )
        if projected == expected_rows:
            return True, "contains the expected columns"

    return False, f"columns do not match ({agent_width} returned, {expected_width} wanted)"


def run_reference(sql):
    """
    Run a ground truth query.

    Straight through db.run_readonly and not through tools.run_query,
    because run_query forces a LIMIT on and one of these reference
    queries could legitimately want more rows than that. These queries
    are mine and were written by hand, so they do not need the parser
    checking them. The connection is still the read only one.
    """
    result, error = db.run_readonly(sql)
    if error:
        # A broken reference query is my bug, not the agent's, and it
        # would silently fail every question it belongs to.
        raise RuntimeError(f"Reference query failed ({error['error_type']}): {error['message']}\n{sql}")
    return result


# --------------------------------------------------------------- runner


def score_one(runner, item):
    """Ask one question and decide whether the answer was right."""
    record = _judge(runner, item)

    # Push the verdict back onto the trace that produced it. This is the
    # bit that turns the Langfuse project into something browsable: the
    # trace list can be filtered to the questions that failed, and each
    # one still shows the queries, the errors and the repairs that led
    # there. A JSON file on disk cannot do that.
    tracing.score(
        record.get("trace_id"),
        name="execution-accuracy",
        value=1 if record["correct"] else 0,
        comment=record.get("reason"),
    )

    return record


def _judge(runner, item):
    """Ask the question and grade the answer, with no tracing in the way."""
    outcome = runner.ask(item["question"])

    record = {
        "trace_id": outcome.get("trace_id"),
        "id": item["id"],
        "category": item["category"],
        "question": item["question"],
        "sql": outcome.get("sql"),
        "attempts": outcome.get("attempts", 0),
        "refused": outcome.get("refused", False),
        "gave_up": outcome.get("gave_up", False),
        "latency_ms": outcome.get("latency_ms", 0),
        "tokens": outcome.get("tokens", 0),
        "answer": outcome.get("answer", ""),
        # Did the first attempt fail? This is what the rescue rate is
        # measured over, and it is only knowable because the agent keeps
        # a history of what it tried.
        "first_attempt_failed": bool(outcome.get("history")),
        "failures": [entry["error_type"] for entry in outcome.get("history", [])],
    }

    # ------------------------------------------- the unanswerable ten
    if item["expected_sql"] is None:
        # Correct means it refused. Giving up after three attempts is
        # not the same thing and does not count: the agent burned three
        # model calls before failing, when it should have looked at the
        # schema and said no straight away.
        record["correct"] = bool(outcome.get("refused"))
        record["reason"] = "refused" if record["correct"] else "did not refuse"
        return record

    # ----------------------------------------------- the answerable 30
    if outcome.get("refused"):
        # A false refusal. Worth counting separately, because an agent
        # that refuses everything would score 100 percent on refusal
        # accuracy and be useless.
        record["correct"] = False
        record["reason"] = "refused a question it could have answered"
        record["false_refusal"] = True
        return record

    if outcome.get("gave_up") or not outcome.get("sql"):
        record["correct"] = False
        record["reason"] = "gave up"
        return record

    expected = run_reference(item["expected_sql"])

    # Re-run the agent's own query to get its full result set. The
    # answer only carried the rows the model was shown, and comparing a
    # truncated set against a full one would fail correct answers.
    from app import tools

    got = tools.run_query(outcome["sql"])
    if got.get("error_type") == "empty_result":
        got = {"columns": got.get("columns", []), "rows": []}
    elif not got.get("ok"):
        record["correct"] = False
        record["reason"] = f"query no longer runs: {got['error_type']}"
        return record

    matched, reason = compare(got, expected)
    record["correct"] = matched
    record["reason"] = reason
    return record


def run(runner, label):
    """Run all forty and collect the records."""
    records = []
    total = len(QUESTIONS)

    for number, item in enumerate(QUESTIONS, start=1):
        print(f"  [{number:2}/{total}] {item['question'][:62]}", end=" ", flush=True)
        try:
            record = score_one(runner, item)
        except Exception as error:
            # One question blowing up should not lose the other
            # thirty nine, which cost real tokens to run.
            record = {
                "id": item["id"],
                "category": item["category"],
                "question": item["question"],
                "correct": False,
                "reason": f"crashed: {error}",
                "attempts": 0,
                "latency_ms": 0,
                "tokens": 0,
                "first_attempt_failed": False,
                "failures": [],
            }

        print("OK " if record["correct"] else "MISS")
        records.append(record)

    return summarise(records, label)


# -------------------------------------------------------------- metrics


def summarise(records, label):
    """
    Turn the records into the numbers that go in the README.

    The rescue rate is the headline. It is measured only over questions
    whose first attempt failed, because that is the population the repair
    loop is supposed to help. Measuring it over all forty would let easy
    questions that never failed inflate it.
    """
    answerable = [record for record in records if record["category"] != "unanswerable"]
    unanswerable = [record for record in records if record["category"] == "unanswerable"]

    # A question that never reached the model is not a wrong answer, and
    # counting it as one makes the agent look worse than it is for a
    # reason that has nothing to do with the agent.
    #
    # I ran into this on the free tier: Groq allows 200,000 tokens a day
    # and a full run of both modes needs about 180,000, so the last four
    # questions of the second run got a 429 and were scored as failures.
    # Reported without comment that would have been a quietly dishonest
    # number, so these are counted and shown separately rather than
    # dropped, which would be the other way of being dishonest about it.
    crashed = [record for record in records if str(record.get("reason", "")).startswith("crashed")]
    completed_unanswerable = [record for record in unanswerable if record not in crashed]

    def accuracy(rows):
        return round(sum(1 for row in rows if row["correct"]) / len(rows), 3) if rows else 0.0

    rescued_from = [record for record in answerable if record.get("first_attempt_failed")]
    rescued = [record for record in rescued_from if record["correct"]]

    solved = [record for record in answerable if record["correct"]]
    latencies = [record["latency_ms"] for record in records if record["latency_ms"]]

    by_category = {}
    for category in ["simple", "join", "hard", "unanswerable"]:
        rows = [record for record in records if record["category"] == category]
        by_category[category] = {
            "n": len(rows),
            "correct": sum(1 for row in rows if row["correct"]),
            "accuracy": accuracy(rows),
        }

    # Every error type that came up, so the README can say which repairs
    # actually fire rather than which ones I wrote code for.
    failure_counts = {}
    for record in records:
        for error_type in record.get("failures", []):
            failure_counts[error_type] = failure_counts.get(error_type, 0) + 1

    return {
        "label": label,
        "run_at": datetime.now().isoformat(timespec="seconds"),
        "model": llm.model_name(),
        "provider": config.LLM_PROVIDER,
        "questions": len(records),
        "execution_accuracy": accuracy(answerable),
        "execution_accuracy_overall": accuracy(records),
        "by_category": by_category,
        "refusal_accuracy": accuracy(unanswerable),
        # The same number over only the questions that actually ran. Both
        # are reported, and which one is fair depends on whether you are
        # judging the agent or the run.
        "refusal_accuracy_completed": accuracy(completed_unanswerable),
        "infrastructure_failures": len(crashed),
        "false_refusals": sum(1 for record in answerable if record.get("false_refusal")),
        "first_attempt_failures": len(rescued_from),
        "rescued_by_repair": len(rescued),
        "rescue_rate": round(len(rescued) / len(rescued_from), 3) if rescued_from else None,
        "mean_attempts_to_success": (
            round(statistics.mean([record["attempts"] for record in solved]), 2) if solved else None
        ),
        "median_latency_ms": int(statistics.median(latencies)) if latencies else 0,
        "total_tokens": sum(record["tokens"] for record in records),
        "failure_types": dict(sorted(failure_counts.items(), key=lambda pair: -pair[1])),
        "records": records,
    }


def show(summary):
    print(f"\n--- {summary['label']} ---")
    print(f"model                     {summary['model']}")
    print(f"execution accuracy        {summary['execution_accuracy']}  (30 answerable)")
    for category, numbers in summary["by_category"].items():
        print(f"  {category:14}          {numbers['accuracy']}  ({numbers['correct']}/{numbers['n']})")
    print(f"refusal accuracy          {summary['refusal_accuracy']}  (10 unanswerable)")
    if summary.get("infrastructure_failures"):
        print(f"  of which never ran      {summary['infrastructure_failures']} "
              f"(rate limited, not the agent's fault)")
        print(f"  refusal on those that   {summary['refusal_accuracy_completed']}")
    print(f"false refusals            {summary['false_refusals']}")
    print(f"first attempt failures    {summary['first_attempt_failures']}")
    print(f"rescued by repair         {summary['rescued_by_repair']}  rate {summary['rescue_rate']}")
    print(f"mean attempts to success  {summary['mean_attempts_to_success']}")
    print(f"median latency            {summary['median_latency_ms']} ms")
    print(f"tokens                    {summary['total_tokens']}")
    if summary["failure_types"]:
        print(f"failures seen             {summary['failure_types']}")


def save(summary, filename):
    """
    Write the results, and keep a timestamped copy.

    The copy exists because I lost a run's results by not keeping one.
    latest.json and baseline.json are written in place by every run, so
    starting a second run overwrites the first, and I did exactly that:
    backed up run one by hand, forgot to back up run two, and destroyed
    it with run three. The console log survived, but a log is not an
    artefact and I would not reconstruct a results file from one.

    So every run now also lands in runs/ under its own timestamp, and
    nothing can overwrite anything. The two stable filenames stay put
    because the API and the README both point at them.

    The archive is gitignored. It is insurance against my own mistakes,
    not something anyone else needs to read.
    """
    config.EVAL_DIR.mkdir(parents=True, exist_ok=True)
    path = config.EVAL_DIR / filename
    body = json.dumps(summary, indent=2, default=str)
    path.write_text(body, encoding="utf-8")

    archive_dir = config.EVAL_DIR / "runs"
    archive_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    archive = archive_dir / f"{stamp}-{filename}"
    archive.write_text(body, encoding="utf-8")

    print(f"\nwritten to {path}")
    print(f"    copy at {archive}")
    return path


def _comparison(base, full):
    """The side by side table the README quotes, as JSON."""
    return {
        "run_at": datetime.now().isoformat(timespec="seconds"),
        "model": full["model"],
        "single_shot": {
            key: base[key] for key in
            ["execution_accuracy", "by_category", "refusal_accuracy",
             "refusal_accuracy_completed", "infrastructure_failures",
             "false_refusals", "median_latency_ms", "total_tokens"]
        },
        "full_agent": {
            key: full[key] for key in
            ["execution_accuracy", "by_category", "refusal_accuracy",
             "refusal_accuracy_completed", "infrastructure_failures",
             "false_refusals", "rescue_rate", "rescued_by_repair",
             "first_attempt_failures", "mean_attempts_to_success",
             "median_latency_ms", "total_tokens"]
        },
    }


def regrade(filename, label):
    """
    Score a finished run again, using the current comparator.

    No model is called. The saved records already hold the SQL the agent
    wrote, so this re-runs those queries and the reference queries and
    grades the pair. A full run is forty minutes and a lot of tokens, and
    fixing a bug in my own grading should not cost that twice.

    The run itself is fixed. Only the grading changes.
    """
    from app import tools

    path = config.EVAL_DIR / filename
    if not path.exists():
        print(f"  {filename} not found, nothing to regrade")
        return None

    previous = json.loads(path.read_text(encoding="utf-8"))
    by_id = {item["id"]: item for item in QUESTIONS}
    changed = 0

    for record in previous["records"]:
        item = by_id.get(record["id"])
        if item is None or item["expected_sql"] is None:
            # The unanswerable ten are graded on whether the agent
            # refused, which no comparator change can affect.
            continue
        if record.get("refused") or record.get("gave_up") or not record.get("sql"):
            continue

        was = record["correct"]

        got = tools.run_query(record["sql"])
        if got.get("error_type") == "empty_result":
            got = {"columns": got.get("columns", []), "rows": []}
        elif not got.get("ok"):
            record["correct"] = False
            record["reason"] = f"query no longer runs: {got['error_type']}"
            changed += was != record["correct"]
            continue

        matched, reason = compare(got, run_reference(item["expected_sql"]))
        record["correct"] = matched
        record["reason"] = reason
        changed += was != matched

    summary = summarise(previous["records"], label)
    print(f"  {changed} verdict(s) changed")
    return summary


def main():
    # Answers and questions contain a rupee sign; Windows console is cp1252.
    console.use_utf8()

    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", action="store_true", help="single shot, no repair loop")
    parser.add_argument("--both", action="store_true", help="run both and write the comparison")
    parser.add_argument("--regrade", action="store_true",
                        help="re-score the saved runs with the current comparator, no model calls")
    args = parser.parse_args()

    if args.regrade:
        print("regrading baseline")
        base = regrade("baseline.json", "single shot")
        print("\nregrading agent")
        full = regrade("latest.json", "full agent")

        if base:
            show(base)
            save(base, "baseline.json")
        if full:
            show(full)
            save(full, "latest.json")
        if base and full:
            save(_comparison(base, full), "comparison.json")
        return

    if args.both:
        print("baseline (single shot)")
        base = run(baseline, "single shot")
        show(base)
        save(base, "baseline.json")

        print("\nagent (with repair loop)")
        full = run(agent, "full agent")
        show(full)
        save(full, "latest.json")

        save(_comparison(base, full), "comparison.json")

    elif args.baseline:
        summary = run(baseline, "single shot")
        show(summary)
        save(summary, "baseline.json")

    else:
        summary = run(agent, "full agent")
        show(summary)
        save(summary, "latest.json")

    tracing.flush()


if __name__ == "__main__":
    main()

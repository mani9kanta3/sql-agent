"""
Every setting the project needs, read once from the .env file.

Same idea as the scholarship project: all of it in one place, so a
missing key is one clear error when the app starts instead of a None
turning up inside a query an hour later.

One thing here is different from a normal project and it is the whole
point. There are **three sets of database credentials**, each as narrow
as its job allows:

    DB_ADMIN_*  the owner. Creates tables, loads the seed data.
                Used only by scripts/, never by app/.
    DB_RO_*     SELECT only. This is what the agent connects with, and
                it is what the whole safety argument rests on.
    DB_LOG_*    INSERT on agent_query_log and nothing else. No SELECT
                anywhere, so it cannot read the shop's data; no UPDATE
                or DELETE, so it cannot rewrite its own history.

The third one exists because monitoring needs a write and the answer to
that was a separate narrow credential rather than a wider shared one.
app/db.py, which is the only route the agent has to the database, knows
about DB_RO_* alone. So there is still no code path from a model written
query to a write, whatever the model asks for.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

# backend/app/config.py -> backend/app -> backend -> the project root
BASE_DIR = Path(__file__).resolve().parent.parent.parent

load_dotenv(BASE_DIR / "backend" / ".env")


def get(name, default=None, required=False):
    """
    Read one value from the environment.

    required=True means the project cannot run without it, so stop now
    with a message that names the key.
    """
    value = os.getenv(name, default)
    if required and not value:
        raise RuntimeError(f"{name} is missing. Copy .env.example to .env and fill it in.")
    return value


def get_int(name, default):
    """Same, for the numbers. Returns the default if the value is junk."""
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return int(default)


# ------------------------------------------------------------- database

DB_NAME = get("DB_NAME", "sql_agent")
DB_HOST = get("DB_HOST", "localhost")
DB_PORT = get("DB_PORT", "5432")

# Hosted Postgres refuses plain connections, my laptop does not care.
DB_SSLMODE = get("DB_SSLMODE", "prefer")

# The owner. Only scripts/setup_database.py and scripts/seed.py use it.
DB_ADMIN_USER = get("DB_ADMIN_USER", "postgres")
DB_ADMIN_PASSWORD = get("DB_ADMIN_PASSWORD", "")

# The agent's credential. SELECT only, created by db/readonly_role.sql.
DB_RO_USER = get("DB_RO_USER", "sql_agent_ro")
DB_RO_PASSWORD = get("DB_RO_PASSWORD", "")

# The monitoring credential. INSERT on agent_query_log and nothing else:
# no SELECT anywhere, so it cannot read the shop's data, and no UPDATE or
# DELETE, so it cannot rewrite its own history. Logging needs a write and
# the answer was a separate narrow role, not a wider shared one.
# Leave the password blank and logging is off and nothing breaks.
DB_LOG_USER = get("DB_LOG_USER", "sql_agent_log")
DB_LOG_PASSWORD = get("DB_LOG_PASSWORD", "")


# --------------------------------------------------------- the envelope

# Layer 2. Every query runs inside a read only transaction with this
# timeout set on it. Five seconds is generous for a shop database and
# short enough that a cartesian join does not hold a connection open
# while somebody waits at the other end.
STATEMENT_TIMEOUT_MS = get_int("STATEMENT_TIMEOUT_MS", 5000)

# If the model forgets a LIMIT, one is added. If it asks for more than
# this, it is lowered. A question is being answered, not a report
# exported, and 200 rows is far more than any answer needs.
MAX_ROWS = get_int("MAX_ROWS", 200)

# How many result rows go back into the prompt when the model writes the
# final answer. The full set still goes to the API caller; this is only
# what the model reads, and a hundred rows of context buys nothing.
ROWS_SHOWN_TO_MODEL = get_int("ROWS_SHOWN_TO_MODEL", 30)


# ------------------------------------------------------------ the agent

# Three tries, then stop. An agent that loops forever on an impossible
# question is worse than one that gives up quickly and says what it
# tried.
MAX_ATTEMPTS = get_int("MAX_ATTEMPTS", 3)

# How many tables go into the prompt normally, and how many after an
# unknown_column error says the retrieval picked wrong.
TABLES_RETRIEVED = get_int("TABLES_RETRIEVED", 5)
TABLES_RETRIEVED_WIDE = get_int("TABLES_RETRIEVED_WIDE", 9)

# Sample rows per table. These matter more than they look: they are how
# the model learns that status holds 'PAID' and not 'paid', which is a
# very common reason a query runs fine and returns nothing.
SAMPLE_ROWS = get_int("SAMPLE_ROWS", 3)


# --------------------------------------------------------------- models

# "groq" or "gemini". Same two doors as the scholarship project, so
# swapping provider is one line in the .env and not a rewrite.
LLM_PROVIDER = get("LLM_PROVIDER", "groq").lower()

GROQ_API_KEY = get("GROQ_API_KEY", "")

# A second key, optional.
#
# The free tier allows 200,000 tokens a day and one evaluation run over
# both modes needs about 180,000, so a single key gives you one
# experiment per day and no room to re-run after a fix. I found that out
# by losing most of a run to a 429 forty questions in.
#
# llm.py moves to this key when the first one is exhausted for the day,
# which turns "come back tomorrow" into "carry on". Leave it blank and
# nothing changes.
GROQ_API_KEY_EXTRA = get("GROQ_API_KEY_EXTRA", "")

GROQ_MODEL = get("GROQ_MODEL", "openai/gpt-oss-120b")

GEMINI_API_KEY = get("GEMINI_API_KEY", "")
GEMINI_MODEL = get("GEMINI_MODEL", "gemini-2.5-flash")

# What the model charges, in US dollars per million tokens.
#
# Langfuse works out cost automatically for the models it has prices for,
# and it does not have Groq's. Without these the traces show tokens and a
# cost of zero, which is worse than no number at all, because the agent
# versus baseline comparison is partly an argument about what the extra
# accuracy costs. So the rates are read from the .env and sent with each
# generation.
#
# Groq's published rates for openai/gpt-oss-120b at the time of writing.
# If the model is changed these have to change with it.
MODEL_INPUT_COST_PER_M = float(get("MODEL_INPUT_COST_PER_M", "0.15"))
MODEL_OUTPUT_COST_PER_M = float(get("MODEL_OUTPUT_COST_PER_M", "0.60"))

EMBEDDING_MODEL = get("EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5")

# My C: drive is full, so the model download goes on the project drive.
MODEL_DIR = Path(get("MODEL_DIR", str(BASE_DIR / "models")))
os.environ.setdefault("HF_HOME", str(MODEL_DIR))


# ------------------------------------------------------------- tracing

# Optional. Blank keys mean tracing is switched off and nothing breaks.
LANGFUSE_PUBLIC_KEY = get("LANGFUSE_PUBLIC_KEY", "")
LANGFUSE_SECRET_KEY = get("LANGFUSE_SECRET_KEY", "")

# The SDK renamed this from LANGFUSE_HOST to LANGFUSE_BASE_URL. Both are
# read here, new name first, so an older .env still works instead of
# silently sending traces to the default region.
LANGFUSE_BASE_URL = get("LANGFUSE_BASE_URL", "") or get(
    "LANGFUSE_HOST", "https://cloud.langfuse.com"
)

# Shows up as a filter in the Langfuse UI, so a trace from my laptop is
# not mixed in with one from an evaluation run.
LANGFUSE_ENVIRONMENT = get("LANGFUSE_ENVIRONMENT", "local")


# ------------------------------------------------------------- folders

DATA_DIR = BASE_DIR / "backend" / "data"

# Where build_schema_index.py writes the table vectors. It is a small
# JSON file and not a vector database, because fifteen tables do not
# need one. See schema_store.py for why.
SCHEMA_INDEX_PATH = DATA_DIR / "schema_index.json"

# The shape of the database at the moment the index was built. drift.py
# compares the live schema against this, because a migration that renames
# a column leaves the descriptions and the vectors quietly describing a
# database that no longer exists.
SCHEMA_SNAPSHOT_PATH = DATA_DIR / "schema_snapshot.json"

# Where run_eval.py writes its results, and where the API reads them
# from for GET /api/eval/latest.
EVAL_DIR = DATA_DIR / "eval"


# ----------------------------------------------------------------- api

CORS_ORIGINS = get("CORS_ORIGINS", "http://localhost:5173").split(",")

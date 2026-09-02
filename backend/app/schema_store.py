"""
Picking the three to five tables a question actually needs.

This step is easy to skip and skipping it is measurably worse. Fifteen
tables of DDL plus sample rows is a lot of prompt, and the cost is not
only tokens. The model gets distracted. Given every table in the shop it
will find a way to join purchase orders into a question about staff
salaries, because the table was there and it looked relevant.

So each table gets a sentence describing what it is for, the sentences
are embedded once, and at question time the top few are retrieved. That
is RAG, inside the agent, and it is the same thing I built in the
scholarship project.

**No vector database here.** Chroma made sense for a few thousand
scheme chunks. This is fifteen rows. The vectors go in a JSON file and
the search is one numpy dot product over a fifteen by three eighty four
matrix, which takes microseconds. Adding a vector store for fifteen rows
would be a dependency I could not defend in an interview.

**Why the model is loaded lazily.** bge-small is about 130 MB and it
takes a couple of seconds to load. The guardrail tests and the whole
safety layer never touch it, and I do not want a test suite that pays
two seconds of model loading to check that DELETE is refused.
"""

import json

import numpy as np

from . import config, tools
from .table_notes import TABLE_NOTES, note_for

_model = None

# bge models were trained with this exact sentence in front of a search
# query and nothing in front of the documents. It is not decoration:
# leaving it out costs a few points of retrieval quality, because the
# query vector ends up in a slightly different place than training put
# it.
QUERY_PREFIX = "Represent this sentence for searching relevant passages: "


def get_model():
    """Load the embedding model once, the first time something needs it."""
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer

        _model = SentenceTransformer(
            config.EMBEDDING_MODEL,
            cache_folder=str(config.MODEL_DIR),
        )
    return _model


def embed(texts):
    """
    Turn a list of strings into unit length vectors.

    normalize_embeddings means every vector has length one, so cosine
    similarity is just a dot product later and there is no dividing by
    magnitudes at query time.
    """
    return get_model().encode(
        texts,
        normalize_embeddings=True,
        show_progress_bar=False,
    )


# ------------------------------------------------------------- building


def text_for(table_name, columns):
    """
    What actually gets embedded for one table.

    Three things joined together: the name, the hand written description,
    and the column names. The column names matter because a question
    saying "cost price" should find stock_entries even though the
    description does not use that exact phrase.
    """
    column_names = " ".join(columns)
    return f"{table_name}. {note_for(table_name)} Columns: {column_names}."


def build_index():
    """
    Embed every table and write the vectors to a JSON file.

    Run by scripts/build_schema_index.py after the database is set up,
    and again whenever a table or a description changes. It is a few
    seconds of work and then the API never pays for it.
    """
    listing = tools.list_tables()
    entries = []

    for table in listing["tables"]:
        name = table["name"]
        described = tools.describe_table(name)
        columns = [column["name"] for column in described.get("columns", [])]
        entries.append({
            "table": name,
            "text": text_for(name, columns),
            "columns": columns,
        })

    vectors = embed([entry["text"] for entry in entries])
    for entry, vector in zip(entries, vectors):
        entry["vector"] = [float(number) for number in vector]

    config.SCHEMA_INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    config.SCHEMA_INDEX_PATH.write_text(
        json.dumps({"model": config.EMBEDDING_MODEL, "tables": entries}, indent=2),
        encoding="utf-8",
    )

    # Record the schema these descriptions and vectors were written
    # against, so drift.py can tell later that the database has moved
    # underneath them. Written here rather than in a separate step
    # because the two must always agree, and a snapshot that can be
    # refreshed without rebuilding the index would be worse than none.
    from . import drift

    drift.save_snapshot()

    return entries


# ------------------------------------------------------------ searching

_index = None


def load_index():
    """
    Read the vectors off disk once.

    If the file is not there the project still works, it just falls back
    to sending every table. That is slower and less accurate, but a
    missing index file should not be the difference between a working
    agent and a stack trace.
    """
    global _index
    if _index is not None:
        return _index

    if not config.SCHEMA_INDEX_PATH.exists():
        _index = None
        return None

    data = json.loads(config.SCHEMA_INDEX_PATH.read_text(encoding="utf-8"))
    tables = data["tables"]
    _index = {
        "names": [entry["table"] for entry in tables],
        # One matrix instead of a list of lists, so the search is a
        # single matrix multiply rather than a Python loop.
        "matrix": np.array([entry["vector"] for entry in tables], dtype=np.float32),
    }
    return _index


def select_tables(question, k=None):
    """
    The k tables most likely to answer this question.

    Returns a list of (table_name, score), best first.
    """
    k = k or config.TABLES_RETRIEVED
    index = load_index()

    if index is None:
        # No index built. Send everything and let the model cope.
        return [(name, 0.0) for name in sorted(TABLE_NOTES)][:k]

    query_vector = embed([QUERY_PREFIX + question])[0]

    # Both sides are unit length, so this dot product is the cosine.
    scores = index["matrix"] @ query_vector

    ranked = sorted(
        zip(index["names"], scores.tolist()),
        key=lambda pair: pair[1],
        reverse=True,
    )

    return ranked[:k]


# -------------------------------------------------------- prompt context


def build_context(table_names, with_samples=True):
    """
    The schema section of the prompt: DDL and a few rows per table.

    The sample rows are the part that earns its place. The DDL says
    status is VARCHAR(12) and three rows say it holds 'PAID'. Only one of
    those two facts stops the model writing status = 'paid' and getting
    a clean, fast, empty answer.
    """
    blocks = []

    for name in table_names:
        described = tools.describe_table(name)
        if not described.get("ok"):
            continue

        block = [f"-- {described['description']}", described["ddl"]]

        if with_samples:
            sample = tools.sample_rows(name, config.SAMPLE_ROWS)
            if sample.get("ok") and sample["rows"]:
                block.append(f"-- {len(sample['rows'])} sample rows from {name}:")
                for row in sample["rows"]:
                    block.append(f"--   {_short_row(row)}")

        blocks.append("\n".join(block))

    return "\n\n".join(blocks)


def _short_row(row):
    """
    One sample row on one line, with long text cut down.

    An address column can be two hundred characters and none of it helps
    the model write a join. Forty is enough to show the shape of the
    value, which is the only reason the row is there.
    """
    parts = []
    for key, value in row.items():
        text = "NULL" if value is None else str(value)
        if len(text) > 40:
            text = text[:37] + "..."
        parts.append(f"{key}={text}")
    return ", ".join(parts)


if __name__ == "__main__":
    # python -m app.schema_store
    for question in [
        "What was our total revenue in 2023?",
        "Which supplier did we spend the most with this year?",
        "How much stock was lost to damage?",
    ]:
        picked = select_tables(question)
        print(f"\n{question}")
        for name, score in picked:
            print(f"   {score:.3f}  {name}")

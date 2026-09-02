"""
Embed the table descriptions so the agent can pick the right tables.

Run:  python -m scripts.build_schema_index

Run it once after setup, and again any time a table is added or a
description in app/table_notes.py is reworded. It takes a few seconds
and writes one small JSON file. The API never pays this cost.

The first run downloads the embedding model, which is about 130 MB and
goes into the folder named by MODEL_DIR in the .env.
"""

from app import config, schema_store


def main():
    print(f"embedding table descriptions with {config.EMBEDDING_MODEL}")
    entries = schema_store.build_index()
    print(f"wrote {len(entries)} tables to {config.SCHEMA_INDEX_PATH}\n")

    # A quick sanity check on a few questions, because an index that
    # builds fine and retrieves badly is a silent problem. If a question
    # about 2023 does not put bill_archive near the top, the description
    # for that table is not doing its job and needs rewording.
    checks = [
        "What was our total revenue in 2023?",
        "Which product sold the most units last month?",
        "How much did we lose to damaged stock?",
        "Which suppliers are not GST registered?",
    ]

    print("retrieval check:")
    for question in checks:
        picked = schema_store.select_tables(question, k=3)
        names = ", ".join(f"{name} ({score:.2f})" for name, score in picked)
        print(f"  {question}\n    {names}")


if __name__ == "__main__":
    main()

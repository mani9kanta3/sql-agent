"""
Create the tables, the read only role, and the demo data. One command.

Run:  python -m scripts.setup_database

It is safe to run again. schema.sql drops every table before creating it,
so this always gives back a clean database with the same seeded numbers.
That matters because the evaluation ground truth is written against these
exact rows.

Everything here uses the admin credentials. This file and scripts/seed.py
are the only two places in the project that can write to the database,
and neither of them is importable from app/.
"""

import sys
from pathlib import Path

import psycopg2

from app import config
from scripts.seed import admin_connection, seed

DB_DIR = Path(__file__).resolve().parent.parent / "db"


def ensure_database():
    """
    Create the database if it is not there yet.

    This connects to the "postgres" maintenance database rather than to
    ours, because you cannot create a database from inside itself. It is
    here so that setup is genuinely one command; the alternative is a
    createdb step in the README that everyone forgets, including me.

    CREATE DATABASE cannot run inside a transaction block, which is what
    the autocommit line is for.
    """
    connection = psycopg2.connect(
        dbname="postgres",
        user=config.DB_ADMIN_USER,
        password=config.DB_ADMIN_PASSWORD,
        host=config.DB_HOST,
        port=config.DB_PORT,
        sslmode=config.DB_SSLMODE,
    )
    connection.autocommit = True

    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1 FROM pg_database WHERE datname = %s", (config.DB_NAME,))
            if cursor.fetchone():
                print(f"  database {config.DB_NAME} already exists")
            else:
                # The name comes from our own .env, never from user
                # input, and CREATE DATABASE will not take a bound
                # parameter for it.
                cursor.execute(f"CREATE DATABASE {config.DB_NAME}")
                print(f"  created database {config.DB_NAME}")
    finally:
        connection.close()


def run_sql_file(path, autocommit=False):
    """Run one .sql file as the admin user."""
    connection = admin_connection()
    connection.autocommit = autocommit
    try:
        with connection.cursor() as cursor:
            cursor.execute(path.read_text(encoding="utf-8"))
        if not autocommit:
            connection.commit()
    finally:
        connection.close()


def create_log_role():
    """
    Make the monitoring credential: INSERT on one table, nothing else.

    Skipped when DB_LOG_PASSWORD is blank, because logging is optional
    and nobody should need it to run the project.

    The sequence grant at the end is easy to forget and fails in a
    confusing way without it: agent_query_log.log_id is a BIGSERIAL, so
    every INSERT calls nextval(), and a role with INSERT on the table but
    no USAGE on its sequence gets "permission denied for sequence" on a
    table it was just granted.
    """
    if not config.DB_LOG_PASSWORD:
        print("  DB_LOG_PASSWORD is blank, skipping the log role")
        return

    connection = admin_connection()
    connection.autocommit = True
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (config.DB_LOG_USER,))
            if cursor.fetchone():
                print(f"  role {config.DB_LOG_USER} already exists, resetting its password")
                cursor.execute(
                    f"ALTER ROLE {config.DB_LOG_USER} WITH LOGIN PASSWORD %s",
                    (config.DB_LOG_PASSWORD,),
                )
            else:
                cursor.execute(
                    f"CREATE ROLE {config.DB_LOG_USER} LOGIN PASSWORD %s",
                    (config.DB_LOG_PASSWORD,),
                )

            for statement in [
                f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {config.DB_LOG_USER}",
                f"REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM {config.DB_LOG_USER}",
                f"REVOKE ALL ON SCHEMA public FROM {config.DB_LOG_USER}",
                f"GRANT CONNECT ON DATABASE {config.DB_NAME} TO {config.DB_LOG_USER}",
                f"GRANT USAGE ON SCHEMA public TO {config.DB_LOG_USER}",
                f"GRANT INSERT ON agent_query_log TO {config.DB_LOG_USER}",
                f"GRANT USAGE ON SEQUENCE agent_query_log_log_id_seq TO {config.DB_LOG_USER}",
            ]:
                cursor.execute(statement)
    finally:
        connection.close()


def verify_log_role():
    """
    Prove the log role can insert and cannot read.

    The second half is the one worth checking. A role that could read the
    shop's data would quietly undo the point of having three credentials.
    """
    if not config.DB_LOG_PASSWORD:
        return True

    connection = psycopg2.connect(
        dbname=config.DB_NAME,
        user=config.DB_LOG_USER,
        password=config.DB_LOG_PASSWORD,
        host=config.DB_HOST,
        port=config.DB_PORT,
        sslmode=config.DB_SSLMODE,
    )
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO agent_query_log (question, mode, attempts_used) "
                "VALUES ('setup check', 'agent', 0)"
            )
            connection.rollback()
            print("  insert works")

            try:
                cursor.execute("SELECT COUNT(*) FROM bills")
            except psycopg2.Error as error:
                connection.rollback()
                print(f"  read refused: {str(error).strip().splitlines()[0]}")
                return True

            connection.rollback()
            print("  THE LOG ROLE CAN READ THE SHOP DATA. Fix the grants.")
            return False
    finally:
        connection.close()


def create_readonly_role():
    """
    Make the agent's SELECT only role.

    The password is taken from the .env and put into the statement here
    rather than being left in the .sql file, so the real password is
    never committed. CREATE ROLE will not accept a bound parameter for
    the password, so it is quoted with psycopg2's own quoting rather
    than by pasting a string together.

    If the role already exists this reports it and carries on. Rerunning
    setup should not fail on something that is already correct.
    """
    if not config.DB_RO_PASSWORD:
        sys.exit("DB_RO_PASSWORD is empty in backend/.env. Set it before running setup.")

    connection = admin_connection()
    connection.autocommit = True

    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (config.DB_RO_USER,))
            exists = cursor.fetchone() is not None

            if exists:
                print(f"  role {config.DB_RO_USER} already exists, resetting its password")
                cursor.execute(
                    f"ALTER ROLE {config.DB_RO_USER} WITH LOGIN PASSWORD %s",
                    (config.DB_RO_PASSWORD,),
                )
            else:
                cursor.execute(
                    f"CREATE ROLE {config.DB_RO_USER} LOGIN PASSWORD %s",
                    (config.DB_RO_PASSWORD,),
                )

            # Take everything away first, then grant back exactly two
            # things. Starting from nothing means an old wider grant
            # cannot survive a rerun.
            for statement in [
                f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {config.DB_RO_USER}",
                f"REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM {config.DB_RO_USER}",
                f"REVOKE ALL ON SCHEMA public FROM {config.DB_RO_USER}",
                f"GRANT CONNECT ON DATABASE {config.DB_NAME} TO {config.DB_RO_USER}",
                f"GRANT USAGE ON SCHEMA public TO {config.DB_RO_USER}",
                f"GRANT SELECT ON ALL TABLES IN SCHEMA public TO {config.DB_RO_USER}",
                f"ALTER DEFAULT PRIVILEGES IN SCHEMA public "
                f"GRANT SELECT ON TABLES TO {config.DB_RO_USER}",
                # Then take one table back. GRANT SELECT ON ALL TABLES
                # includes the monitoring log, which lives in the same
                # database, and the agent has no business reading the
                # record of every question anyone has ever asked it.
                # This has to come after the grant above, or the grant
                # would simply hand it back.
                f"REVOKE ALL ON agent_query_log FROM {config.DB_RO_USER}",
            ]:
                cursor.execute(statement)
    finally:
        connection.close()


def verify_readonly():
    """
    Prove the role cannot write, by trying to write.

    This is the check worth having. A grant that looks right in a script
    and is wrong in practice is exactly the kind of thing nobody notices
    for a month, so setup finishes by connecting as the agent and being
    refused.
    """
    connection = psycopg2.connect(
        dbname=config.DB_NAME,
        user=config.DB_RO_USER,
        password=config.DB_RO_PASSWORD,
        host=config.DB_HOST,
        port=config.DB_PORT,
        sslmode=config.DB_SSLMODE,
    )
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM bills")
            count = cursor.fetchone()[0]
            print(f"  read works: {count} bills")

            try:
                cursor.execute("DELETE FROM bills")
            except psycopg2.Error as error:
                connection.rollback()
                print(f"  write refused: {str(error).strip().splitlines()[0]}")
                return True

            connection.rollback()
            print("  WRITE WAS ALLOWED. The grant is wrong, do not use this setup.")
            return False
    finally:
        connection.close()


def main():
    print(f"database: {config.DB_NAME} on {config.DB_HOST}:{config.DB_PORT}\n")

    print("1. checking the database exists")
    ensure_database()

    print("\n2. creating tables")
    run_sql_file(DB_DIR / "schema.sql")
    run_sql_file(DB_DIR / "query_log.sql")

    print("3. loading demo data")
    seed()

    print("\n4. creating the read only role")
    create_readonly_role()

    print("\n5. checking the read only role really is read only")
    if not verify_readonly():
        sys.exit(1)

    print("\n6. creating the monitoring role")
    create_log_role()

    print("\n7. checking the log role can only insert")
    if not verify_log_role():
        sys.exit(1)

    print("\nSetup finished. Next: python -m scripts.build_schema_index")


if __name__ == "__main__":
    main()

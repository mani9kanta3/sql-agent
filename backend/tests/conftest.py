"""
Shared setup for the tests.

Most of these tests never touch a database and that is on purpose. The
safety layer is the thing that must never quietly regress, so its tests
have to be fast enough that there is no excuse for not running them. They
import a function and call it, and the whole file runs in under a second.

The ones that do need PostgreSQL are marked with @pytest.mark.database
and skip themselves if it is not reachable, so a fresh clone still gets a
green run before anything is set up.
"""

import pytest

# The tables the fake schema has. safety.check() takes the known table
# names as an argument rather than looking them up, which is exactly so
# that these tests can hand it a list and stay away from a database.
FAKE_TABLES = {
    "bills",
    "bill_items",
    "bill_archive",
    "products",
    "customers",
    "employees",
    "suppliers",
    "payments",
}


@pytest.fixture
def tables():
    return FAKE_TABLES


def database_is_up():
    """True if the read only role can connect and read."""
    try:
        from app import db

        return bool(db.list_table_names())
    except Exception:
        return False


@pytest.fixture(scope="session")
def live_db():
    """Skip the whole test if PostgreSQL is not set up yet."""
    if not database_is_up():
        pytest.skip("database not reachable, run scripts/setup_database.py first")
    return True

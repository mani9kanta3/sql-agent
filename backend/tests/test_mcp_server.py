"""
The MCP server, driven the way a real client drives it.

Everything else in the test suite imports app/tools.py and calls the
functions directly, which is fast and is how the guardrails are tested.
But the README claims the four tools work with any MCP client, and
importing a Python function proves nothing about that. The protocol
handshake, the tool schemas, the JSON encoding of the results and the
stdio transport are all untested by calling `tools.run_query()`.

So this one spawns the actual server as a subprocess, speaks MCP to it,
and asks it to do things. It is the slowest test in the project by a
wide margin, which is why there is exactly one of it and the detailed
checking lives in the fast tests.

The last assertion is the one worth having: a DELETE sent in through the
MCP path is refused, exactly as it is through the agent path. That is
the point of putting the safety in the tool layer rather than in the
agent, and this is the test that shows it holds for a caller I did not
write.

asyncio.run() inside a normal test rather than pytest-asyncio, because
one async test does not justify another dependency.
"""

import asyncio
import json
import sys

import pytest

pytestmark = pytest.mark.usefixtures("live_db")


async def _drive_the_server():
    """Start the server, call all four tools, and give back what it said."""
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    # sys.executable so it runs in this venv rather than whatever python
    # happens to be first on PATH.
    params = StdioServerParameters(command=sys.executable, args=["-m", "mcp_server.server"])

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            listed = await session.list_tools()
            said = {"tool_names": sorted(tool.name for tool in listed.tools)}

            async def call(name, arguments):
                # Results come back as JSON text, which is the encoding a
                # model on the other end would have to read.
                result = await session.call_tool(name, arguments)
                return json.loads(result.content[0].text)

            said["tables"] = await call("list_tables", {})
            said["described"] = await call("describe_table", {"name": "bills"})
            said["sample"] = await call("sample_rows", {"name": "bills", "n": 2})
            said["query"] = await call("run_query", {"sql": "SELECT COUNT(*) AS n FROM bills"})
            said["delete"] = await call("run_query", {"sql": "DELETE FROM bills"})
            said["injection"] = await call(
                "run_query", {"sql": "SELECT 1 FROM bills; DROP TABLE bills"}
            )

            return said


@pytest.fixture(scope="module")
def server():
    """Start the server once and reuse it. Spawning is the slow part."""
    return asyncio.run(_drive_the_server())


def test_all_four_tools_are_advertised(server):
    assert server["tool_names"] == [
        "describe_table",
        "list_tables",
        "run_query",
        "sample_rows",
    ]


def test_list_tables_comes_back_with_descriptions(server):
    tables = server["tables"]["tables"]
    assert len(tables) == 15
    # The description is the half that does the work. A client given only
    # names has to guess what bill_archive holds.
    assert all(table["description"] for table in tables)


def test_describe_table_returns_ddl_with_the_keys_on_it(server):
    ddl = server["described"]["ddl"]
    assert "CREATE TABLE bills" in ddl
    assert "REFERENCES customers(cust_id)" in ddl


def test_sample_rows_returns_real_rows(server):
    assert len(server["sample"]["rows"]) == 2


def test_a_select_runs(server):
    assert server["query"]["ok"] is True
    assert server["query"]["rows"][0]["n"] > 0


def test_a_delete_is_refused_through_the_protocol_too(server):
    """
    The reason the tool bodies are in app/tools.py rather than inline in
    the server: the safety holds for any caller, not just for my agent.
    """
    assert server["delete"]["ok"] is False
    assert server["delete"]["error_type"] == "rejected"


def test_a_second_statement_is_refused_through_the_protocol_too(server):
    assert server["injection"]["ok"] is False
    assert server["injection"]["error_type"] == "rejected"

"""
The MCP server. Four tools, and nothing else in the file.

MCP is the Model Context Protocol. It is a standard way to describe a
set of tools so that any client can discover and call them, instead of
every project inventing its own tool format. The practical result is
that these four tools work in Claude Desktop, in Claude Code, and in my
own agent, without being written three times.

This file is deliberately thin. Every tool body is one line calling
app/tools.py, which is where the checks and the envelope actually live.
I did it that way round for two reasons.

The first is testing. If the safety rules lived inside these decorated
functions, the only way to test them would be to start a server and
speak the protocol at it. Because they live in a plain module, the
guardrail tests import a function and call it, and they run in a second.

The second is the argument the guide makes, which I think is the right
one: the safety has to hold regardless of what calls the tool. It does.
My agent and an MCP client both end up inside the same run_query(), and
there is no other route to the database anywhere in the project.

Run it with:  python -m mcp_server.server
"""

from mcp.server.fastmcp import FastMCP

from app import tools

mcp = FastMCP("sql-agent-tools")


@mcp.tool()
def list_tables() -> dict:
    """
    List every table in the hardware store database with a short
    description of what it holds. Start here when you do not yet know
    which tables a question needs.
    """
    return tools.list_tables()


@mcp.tool()
def describe_table(name: str) -> dict:
    """
    Show the columns, data types, nullability, primary key and foreign
    keys of one table, written as a CREATE TABLE statement.

    Args:
        name: the exact table name, as returned by list_tables.
    """
    return tools.describe_table(name)


@mcp.tool()
def sample_rows(name: str, n: int = 3) -> dict:
    """
    Return a few real rows from one table, so you can see how values are
    actually stored before you filter on them. Statuses in this database
    are upper case, for example, which the column type does not tell you.

    Args:
        name: the exact table name.
        n: how many rows, at most 10.
    """
    return tools.sample_rows(name, n)


@mcp.tool()
def run_query(sql: str) -> dict:
    """
    Run one read-only SELECT against the hardware store database and
    return the rows.

    The connection holds SELECT permission only, there is a five second
    statement timeout, and a LIMIT is added if you leave one out. Only a
    single SELECT statement is accepted. Anything else is refused before
    it reaches the database.

    On failure this returns a structured error with an error_type such
    as syntax_error, unknown_column, ambiguous_column or timeout, plus
    the database's own hint where there is one. Read the error_type and
    fix the specific problem rather than guessing.

    Args:
        sql: one PostgreSQL SELECT statement.
    """
    return tools.run_query(sql)


if __name__ == "__main__":
    # stdio is what a desktop MCP client launches and speaks over. There
    # is no port and no network here, which is the right default for a
    # tool that holds a database credential.
    mcp.run(transport="stdio")

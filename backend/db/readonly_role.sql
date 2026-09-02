-- Layer 1 of the safety envelope, and the only one that really matters.
--
-- Everything else in this project is convenience. sqlglot can be wrong,
-- a prompt can be talked around, and my own Python can have a bug in it.
-- This cannot. The agent connects as a role that has no permission to
-- write anything, so a DELETE is refused by PostgreSQL itself.
--
-- When someone asks "what if the model writes a DROP TABLE", the answer
-- is not "I told it not to". The answer is that the credential it holds
-- cannot do it.
--
-- Run this as the postgres superuser, once, after schema.sql.
-- Change the password before you run it.

-- LOGIN because the agent connects with this role directly.
-- No SUPERUSER, no CREATEDB, no CREATEROLE. It gets nothing by default.
CREATE ROLE sql_agent_ro LOGIN PASSWORD 'change_me_before_running';

-- Start from nothing rather than trusting the defaults. If this role
-- ever existed before with wider rights, this takes them back.
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM sql_agent_ro;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM sql_agent_ro;
REVOKE ALL ON SCHEMA public FROM sql_agent_ro;
REVOKE ALL ON DATABASE sql_agent FROM sql_agent_ro;

-- Now hand back exactly two things: the right to see inside the schema,
-- and the right to read the rows.
GRANT CONNECT ON DATABASE sql_agent TO sql_agent_ro;
GRANT USAGE ON SCHEMA public TO sql_agent_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO sql_agent_ro;

-- Tables made after this line would not be covered by the grant above,
-- because GRANT applies to what exists at the moment it runs. This makes
-- the rule apply to future tables too, so re-seeding the database does
-- not quietly leave the agent unable to read half of it.
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO sql_agent_ro;

-- No sequence access on purpose. Reading a sequence is harmless, but
-- nextval() advances it, and there is no question the agent could
-- possibly need to answer by touching one.


-- A quick way to check it worked. Connect as sql_agent_ro and run:
--
--   SELECT COUNT(*) FROM bills;              -- works
--   DELETE FROM bills;                       -- ERROR: permission denied
--   CREATE TABLE oops (id INT);              -- ERROR: permission denied
--
-- The middle one is the test that matters, and tests/test_safety.py
-- runs it automatically so it can never quietly regress.

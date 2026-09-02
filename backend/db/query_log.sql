-- One row per answered question, for monitoring.
--
-- The evaluation says the agent worked before it shipped. This says when
-- it stopped, and they catch different things: the eval runs against a
-- schema frozen at the moment I wrote the questions, and production does
-- not.
--
-- The table is created at launch even though nothing reads it yet. A log
-- only becomes useful once it has history, and a dashboard bolted on a
-- month later starts from zero.
--
-- The signal to watch is attempts_used. If the mean drifts from about
-- 1.1 to 2.5, something changed underneath the agent, and it is almost
-- always schema drift: a migration renamed a column, so the descriptions
-- and the embeddings in data/schema_index.json are now describing a
-- database that no longer exists. An offline eval cannot catch that,
-- which is why scripts/check_schema_drift.py exists as well.

DROP TABLE IF EXISTS agent_query_log;

CREATE TABLE agent_query_log (
    log_id         BIGSERIAL PRIMARY KEY,
    asked_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    question       TEXT NOT NULL,
    mode           VARCHAR(10) NOT NULL,          -- 'agent' or 'baseline'

    final_sql      TEXT,
    attempts_used  SMALLINT NOT NULL DEFAULT 0,

    -- Which failures actually happen in the wild, as opposed to the ones
    -- I built questions for. An error type that never appears here is a
    -- repair strategy I am maintaining for nothing.
    error_types    TEXT[],
    tables_used    TEXT[],

    gave_up        BOOLEAN NOT NULL DEFAULT FALSE,
    refused        BOOLEAN NOT NULL DEFAULT FALSE,

    -- A sudden run of zero row answers is a signal on its own. It is what
    -- a wrong-cased filter looks like at scale, and what a renamed status
    -- value looks like the morning after a migration.
    rows_returned  INTEGER,

    latency_ms     INTEGER,
    tokens         INTEGER,
    cost_usd       NUMERIC(10, 6),

    model          VARCHAR(60),
    -- The Langfuse trace for this same question, so a bad row in here
    -- leads straight to the queries and errors behind it.
    trace_id       VARCHAR(64)
);

CREATE INDEX idx_query_log_asked_at ON agent_query_log(asked_at);
CREATE INDEX idx_query_log_gave_up ON agent_query_log(gave_up) WHERE gave_up;


-- A third role, and the reason it exists is worth stating.
--
-- Everything else in this project rests on the agent's credential being
-- unable to write. Logging needs a write. Rather than loosen the role
-- that answers questions, the log gets its own credential that can do
-- exactly one thing: INSERT into this one table.
--
-- It has no SELECT anywhere, so it cannot read the shop's data even
-- though it can connect. It has no UPDATE or DELETE, so it cannot alter
-- or erase its own history either, which is what you want from a log.
--
-- Run as the admin user. setup_database.py does this for you.
--
--   CREATE ROLE sql_agent_log LOGIN PASSWORD '...';
--   GRANT CONNECT ON DATABASE sql_agent TO sql_agent_log;
--   GRANT USAGE ON SCHEMA public TO sql_agent_log;
--   GRANT INSERT ON agent_query_log TO sql_agent_log;
--   GRANT USAGE ON SEQUENCE agent_query_log_log_id_seq TO sql_agent_log;
--
-- The sequence grant is needed and easy to forget: BIGSERIAL means every
-- INSERT calls nextval(), and without USAGE on the sequence the insert
-- fails with permission denied on a table the role was just granted.


-- What to look at once there is history:
--
--   mean attempts, by day
--     SELECT asked_at::DATE AS day,
--            ROUND(AVG(attempts_used), 2) AS mean_attempts,
--            COUNT(*) AS questions
--     FROM agent_query_log
--     WHERE NOT gave_up AND NOT refused
--     GROUP BY 1 ORDER BY 1;
--
--   which failures actually occur
--     SELECT UNNEST(error_types) AS error_type, COUNT(*)
--     FROM agent_query_log
--     GROUP BY 1 ORDER BY 2 DESC;
--
--   honest failure rate and cost per question
--     SELECT asked_at::DATE AS day,
--            ROUND(100.0 * COUNT(*) FILTER (WHERE gave_up) / COUNT(*), 1) AS gave_up_pct,
--            ROUND(AVG(cost_usd)::NUMERIC, 6) AS avg_cost
--     FROM agent_query_log
--     GROUP BY 1 ORDER BY 1;

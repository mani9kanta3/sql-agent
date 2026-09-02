import React, { useState } from "react";

import api from "./api";
import Attempts from "./Attempts";
import ResultTable from "./ResultTable";

/*
  The main screen. Type a question, get an answer, and see the SQL.

  The SQL is shown by default and not hidden behind a "show query"
  toggle. That is the point of the whole project: an answer with no query
  behind it has to be taken on faith. It also means a technical person
  can check the agent's work in front of me, which is a good position to
  be in.

  The mode switch runs the same question through the single shot version.
  It is there so the comparison in the README can be reproduced by
  anyone rather than taken on my word.
*/

const EXAMPLES = [
  "What was our total revenue in 2023?",
  "Which product sold the most units in 2026?",
  "What percentage of our bills are to walk-in customers?",
  "Which supplier has the best delivery rating?",
];

function Ask({ question, setQuestion }) {
  const [result, setResult] = useState(null);
  const [mode, setMode] = useState("agent");

  const [asking, setAsking] = useState(false);
  const [error, setError] = useState("");

  async function handleSubmit(e) {
    e.preventDefault();

    const asked = question.trim();
    if (asked.length < 3) {
      return;
    }

    setAsking(true);
    setError("");
    setResult(null);

    try {
      const response = await api.post("/ask", { question: asked, mode });
      setResult(response.data);
    } catch (err) {
      // The API only fails like this when something is genuinely broken.
      // A question the agent could not answer comes back as a normal
      // 200 with gave_up set, because that is an answer and not an error.
      setError(
        err.response?.data?.detail ||
          "The API did not answer. Check that it is running on port 8000."
      );
    } finally {
      setAsking(false);
    }
  }

  return (
    <>
      <form onSubmit={handleSubmit} className="card shadow-sm mb-3">
        <div className="card-body">
          <div className="input-group input-group-lg">
            <input
              type="text"
              className="form-control"
              placeholder="How much did we sell last month?"
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              autoFocus
            />
            <button className="btn btn-dark px-4" type="submit" disabled={asking}>
              {asking ? "Thinking..." : "Ask"}
            </button>
          </div>

          <div className="d-flex flex-wrap align-items-center gap-3 mt-3">
            <div className="form-check form-check-inline mb-0">
              <input
                className="form-check-input"
                type="radio"
                id="mode-agent"
                checked={mode === "agent"}
                onChange={() => setMode("agent")}
              />
              <label className="form-check-label small" htmlFor="mode-agent">
                Full agent
              </label>
            </div>
            <div className="form-check form-check-inline mb-0">
              <input
                className="form-check-input"
                type="radio"
                id="mode-baseline"
                checked={mode === "baseline"}
                onChange={() => setMode("baseline")}
              />
              <label className="form-check-label small" htmlFor="mode-baseline">
                Single shot, no repair
              </label>
            </div>
          </div>

          <div className="mt-3">
            {EXAMPLES.map((example) => (
              <button
                key={example}
                type="button"
                className="btn btn-sm btn-outline-secondary me-2 mb-2"
                onClick={() => setQuestion(example)}
              >
                {example}
              </button>
            ))}
          </div>
        </div>
      </form>

      {error && <div className="alert alert-danger">{error}</div>}

      {asking && (
        <div className="text-secondary small">
          Writing the query, running it, and checking what came back.
        </div>
      )}

      {result && <Answer result={result} />}
    </>
  );
}

function Answer({ result }) {
  // Three different things can come back and they should not look the
  // same. A refusal is a correct answer, giving up is a failure, and an
  // answer is an answer.
  const tone = result.refused
    ? "border-warning"
    : result.gave_up
      ? "border-danger"
      : "border-success";

  return (
    <>
      <div className={`card shadow-sm mb-3 border-2 ${tone}`}>
        <div className="card-body">
          {result.refused && (
            <span className="badge text-bg-warning mb-2">
              Not answerable from this database
            </span>
          )}
          {result.gave_up && (
            <span className="badge text-bg-danger mb-2">
              Gave up after {result.attempts} attempts
            </span>
          )}

          <p className="mb-0" style={{ whiteSpace: "pre-wrap" }}>
            {result.answer}
          </p>
        </div>
      </div>

      <div className="d-flex flex-wrap gap-2 mb-3 small text-secondary">
        <span className="badge text-bg-light border">
          {result.attempts} attempt{result.attempts === 1 ? "" : "s"}
        </span>
        <span className="badge text-bg-light border">{result.row_count} rows</span>
        <span className="badge text-bg-light border">{result.latency_ms} ms</span>
        <span className="badge text-bg-light border">{result.tokens} tokens</span>
        <span className="badge text-bg-light border">{result.model}</span>
        {result.trace_url && (
          <a
            className="badge text-bg-dark text-decoration-none"
            href={result.trace_url}
            target="_blank"
            rel="noreferrer"
          >
            Open the trace
          </a>
        )}
      </div>

      {/* Every attempt that failed on the way, which is the interesting
          half of a question that took two goes. */}
      <Attempts history={result.history} />

      {result.sql && (
        <div className="card shadow-sm mb-3">
          <div className="card-header bg-white fw-semibold small">
            The query it ran
          </div>
          <pre className="card-body mb-0 bg-light small" style={{ whiteSpace: "pre-wrap" }}>
            {result.sql}
          </pre>
        </div>
      )}

      <ResultTable columns={result.columns} rows={result.rows} />
    </>
  );
}

export default Ask;

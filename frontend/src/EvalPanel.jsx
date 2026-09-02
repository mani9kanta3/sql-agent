import React, { useEffect, useState } from "react";

import api from "./api";

/*
  My own evaluation numbers, served from the API rather than typed into
  a README.

  Numbers in a README go stale quietly. These come from the JSON file
  that run_eval.py wrote on its last run, so what is on the screen is
  what the harness actually measured, and anyone can rerun it and watch
  this change.
*/

function EvalPanel() {
  const [summary, setSummary] = useState(null);
  const [load, setLoad] = useState(true);

  useEffect(() => {
    api
      .get("/eval/latest")
      .then((response) => setSummary(response.data))
      .catch(() => setSummary(null))
      .finally(() => setLoad(false));
  }, []);

  if (load) {
    return null;
  }

  // ok is only present, and false, when no evaluation has been run yet.
  if (!summary || summary.ok === false) {
    return (
      <div className="card shadow-sm">
        <div className="card-body small text-secondary">
          No evaluation has been run yet. Run{" "}
          <code>python -m eval.run_eval --both</code> in the backend folder.
        </div>
      </div>
    );
  }

  return (
    <div className="card shadow-sm">
      <div className="card-header bg-white fw-semibold small">
        Measured on 40 questions
      </div>
      <div className="card-body">
        <Row label="Execution accuracy" value={percent(summary.execution_accuracy)} />

        {/* Questions that never reached the model are not wrong answers.
            Showing only the raw 60% would be quietly dishonest, and
            hiding the four would be dishonest the other way, so both
            numbers are on the page. */}
        <Row
          label="Correct refusals"
          value={
            summary.infrastructure_failures
              ? percent(summary.refusal_accuracy_completed)
              : percent(summary.refusal_accuracy)
          }
        />
        {summary.infrastructure_failures > 0 && (
          <div className="text-secondary mb-1" style={{ fontSize: "0.75rem" }}>
            {summary.infrastructure_failures} question
            {summary.infrastructure_failures === 1 ? "" : "s"} never ran (rate
            limited); raw score {percent(summary.refusal_accuracy)}
          </div>
        )}

        <Row
          label="Rescued by repair"
          value={
            summary.rescue_rate === null
              ? "nothing failed"
              : `${percent(summary.rescue_rate)} of ${summary.first_attempt_failures}`
          }
        />
        <Row label="Median latency" value={`${summary.median_latency_ms} ms`} />

        <hr className="my-3" />

        {Object.entries(summary.by_category || {}).map(([name, numbers]) => (
          <Row
            key={name}
            label={name}
            value={`${numbers.correct}/${numbers.n}`}
            muted
          />
        ))}

        <div className="text-secondary mt-3" style={{ fontSize: "0.75rem" }}>
          {summary.model} &middot; {summary.run_at}
        </div>
      </div>
    </div>
  );
}

function Row({ label, value, muted }) {
  return (
    <div className="d-flex justify-content-between small mb-1">
      <span className={muted ? "text-secondary text-capitalize" : ""}>{label}</span>
      <span className={muted ? "text-secondary" : "fw-semibold"}>{value}</span>
    </div>
  );
}

function percent(value) {
  if (value === null || value === undefined) {
    return "-";
  }
  return `${Math.round(value * 100)}%`;
}

export default EvalPanel;

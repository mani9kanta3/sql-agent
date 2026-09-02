import React, { useEffect, useState } from "react";

import api from "./api";

/*
  What is actually in the database.

  This is here so that a refusal makes sense. When the agent says it
  cannot tell you which supplier has the best delivery rating, being able
  to see that there is no rating anywhere in the schema turns that from a
  failure into a correct answer.
*/

function SchemaPanel({ onPickTable }) {
  const [tables, setTables] = useState([]);
  const [load, setLoad] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    loadSchema();
  }, []);

  async function loadSchema() {
    try {
      const response = await api.get("/schema");
      setTables(response.data.tables || []);
    } catch {
      setError("Could not load the schema.");
    } finally {
      setLoad(false);
    }
  }

  return (
    <div className="card shadow-sm mb-4">
      <div className="card-header bg-white fw-semibold small d-flex justify-content-between">
        <span>The database</span>
        {!load && <span className="text-secondary fw-normal">{tables.length} tables</span>}
      </div>

      <div className="list-group list-group-flush" style={{ maxHeight: "380px", overflowY: "auto" }}>
        {load && <div className="list-group-item small text-secondary">Loading...</div>}
        {error && <div className="list-group-item small text-danger">{error}</div>}

        {tables.map((table) => (
          <button
            key={table.name}
            type="button"
            className="list-group-item list-group-item-action text-start"
            onClick={() => onPickTable(table.name)}
          >
            <div className="d-flex justify-content-between">
              <code className="small">{table.name}</code>
              {table.approx_rows !== null && (
                <span className="text-secondary small">
                  ~{table.approx_rows.toLocaleString("en-IN")}
                </span>
              )}
            </div>
            <div className="text-secondary" style={{ fontSize: "0.78rem" }}>
              {table.description}
            </div>
          </button>
        ))}
      </div>
    </div>
  );
}

export default SchemaPanel;

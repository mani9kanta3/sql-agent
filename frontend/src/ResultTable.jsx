import React from "react";

/*
  The rows the query returned.

  Only the first 50 are drawn. The API already caps a result at 200, and
  a page that renders two hundred rows to answer "how much did we sell"
  is answering the wrong question.
*/

const SHOWN = 50;

function ResultTable({ columns, rows }) {
  if (!columns || columns.length === 0 || !rows || rows.length === 0) {
    return null;
  }

  const visible = rows.slice(0, SHOWN);

  return (
    <div className="card shadow-sm mb-4">
      <div className="card-header bg-white fw-semibold small d-flex justify-content-between">
        <span>Result</span>
        <span className="text-secondary fw-normal">
          {rows.length} row{rows.length === 1 ? "" : "s"}
        </span>
      </div>

      <div className="table-responsive" style={{ maxHeight: "420px" }}>
        <table className="table table-sm table-striped mb-0 small">
          <thead className="table-light position-sticky top-0">
            <tr>
              {columns.map((column) => (
                <th key={column}>{column}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {visible.map((row, index) => (
              <tr key={index}>
                {columns.map((column) => (
                  <td key={column}>{format(row[column])}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {rows.length > SHOWN && (
        <div className="card-footer bg-white small text-secondary">
          Showing the first {SHOWN} of {rows.length} rows.
        </div>
      )}
    </div>
  );
}

/*
  NULL has to look different from an empty string, because on this
  database the difference matters. A bill with no customer is a walk in
  sale, not a customer with a blank name, and that distinction is the
  point of several of the harder questions.
*/
function format(value) {
  if (value === null || value === undefined) {
    return <span className="text-secondary fst-italic">NULL</span>;
  }
  if (typeof value === "boolean") {
    return value ? "true" : "false";
  }
  return String(value);
}

export default ResultTable;

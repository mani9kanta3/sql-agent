import React from "react";

/*
  The failed attempts, shown in order.

  Most projects hide this. I show it because it is the most interesting
  thing the agent does, and because a question that succeeded on the
  second try is proof that the repair loop is doing something real. If
  this list is always empty, the loop is decoration.

  Collapsed by default so it does not push the answer down the page.
*/

function Attempts({ history }) {
  if (!history || history.length === 0) {
    return null;
  }

  return (
    <div className="accordion mb-3" id="attempts">
      <div className="accordion-item">
        <h2 className="accordion-header">
          <button
            className="accordion-button collapsed py-2 small"
            type="button"
            data-bs-toggle="collapse"
            data-bs-target="#attempts-body"
          >
            {history.length} earlier attempt{history.length === 1 ? "" : "s"} failed
          </button>
        </h2>
        <div id="attempts-body" className="accordion-collapse collapse" data-bs-parent="#attempts">
          <div className="accordion-body">
            {history.map((item, index) => (
              <div key={index} className="mb-3">
                <div className="small">
                  <span className="badge text-bg-secondary me-2">
                    Attempt {index + 1}
                  </span>
                  <span className="badge text-bg-danger-subtle text-danger-emphasis">
                    {item.error_type}
                  </span>
                </div>
                <div className="small text-secondary mt-1">{item.message}</div>
                <pre className="bg-light small p-2 mt-1 mb-0" style={{ whiteSpace: "pre-wrap" }}>
                  {item.sql}
                </pre>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

export default Attempts;

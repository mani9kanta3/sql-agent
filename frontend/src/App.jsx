import React, { useState } from "react";

import Ask from "./Ask";
import EvalPanel from "./EvalPanel";
import SchemaPanel from "./SchemaPanel";

/*
  The whole app is one page. The guide is clear that the frontend is not
  where the marks are, so there is no router, no state library and no
  login. A question box, the answer, and two panels of context.

  The two panels are not decoration. Someone who can see what is in the
  database asks better questions, and an honest refusal only makes sense
  if you can see that the table really is not there.
*/

function App() {
  // Kept up here because the schema panel writes into it when a table
  // name is clicked, and the ask box reads it.
  const [question, setQuestion] = useState("");

  return (
    <div className="container-fluid py-4 px-lg-5">
      <div className="row mb-4">
        <div className="col">
          <h4 className="mb-1">SQL Analyst Agent</h4>
          <p className="text-secondary mb-0">
            Ask about the hardware shop database in plain English. The query it
            wrote is shown with every answer.
          </p>
        </div>
      </div>

      <div className="row g-4">
        <div className="col-lg-8">
          <Ask question={question} setQuestion={setQuestion} />
        </div>

        <div className="col-lg-4">
          <SchemaPanel onPickTable={(name) => setQuestion(`Tell me about ${name}`)} />
          <EvalPanel />
        </div>
      </div>
    </div>
  );
}

export default App;

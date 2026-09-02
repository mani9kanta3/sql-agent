Screenshots of the running application, referenced from the main README.

Wanted here:

  answer.png       an answered question showing the SQL, the badges and the
                   result table. This is the one the README embeds
  refusal.png      an unanswerable question, refused, with the schema panel
                   visible beside it so the refusal is obviously correct
  repair.png       a question that took two attempts, with the failed attempts
                   accordion expanded
  trace.png        the Langfuse trace for that repaired question

Take them at a desktop width so the two column layout shows. Start the API with
"uvicorn app.main:app --reload" and the page with "npm run dev", then use the
example buttons on the page.

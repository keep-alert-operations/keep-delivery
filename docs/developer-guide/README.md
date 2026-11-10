# Keep developer onboarding guide

Open [the interactive guide](index.html) directly in a browser. It has thirteen
chapters, an eleven-repository explorer, six pipeline scenarios, an alert-state
playground, dependency failure explanations, learning tracks, quizzes and local
reading progress. No server, installation or network access is needed for the guide.

Source links require the existing sibling workspace layout. The guide's simulations
use synthetic local data and never call Keep or execute the displayed commands.

## Maintain the guide

Edit the HTML fragments in `parts/`, `guide.css` or `guide.js`, then regenerate:

```powershell
# Working directory: keep-delivery
node docs/developer-guide/build.mjs
node --check docs/developer-guide/guide.js
python qualification/verify_documents.py
```

`build.mjs` uses only Node built-ins and embeds the fragments, CSS and JavaScript
into `index.html`. Commit the generated edition with its source changes. Keep
behavior aligned with the current worker scope and linked specifications; this
guide is explanatory documentation, not a second requirements authority.

When behavior changes, exercise affected controls in a browser. Check the source
links relative to the generated file, chapter anchors, narrow layout, progress
reload/reset, quiz feedback and failure scenarios. The simulator deliberately
assumes a maintenance predicate unaffected by manual edits and models only selected
events; do not mistake it for the production domain evaluator.

Edition source context and limitations appear in the guide's final chapter.
Verification is recorded in [the delivery evidence](../../qualification/developer-guide-2026-10-11.md).

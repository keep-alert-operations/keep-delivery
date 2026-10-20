# Interactive developer guide verification — 2026-10-11

## Scope and source context

Created [the developer onboarding guide](../docs/developer-guide/index.html) and
its maintainable HTML/CSS/JavaScript sources under `docs/developer-guide/`.
Added a delivery README entry; preserved the pre-existing workspace changes and
client-guide work. No runtime service, contract, migration or deployment changed.

The guide records all eleven inspected base revisions. Delivery base HEAD was
`ee37546e771e435d1f07c2b88857f548dd05fbb0`; working trees contained existing local
changes. Current worker scope, normative specifications, setup scripts, service
source and delivery readiness informed the content. The guide is explanatory,
not a new specification or qualification of that working-tree combination.

## Executed checks

From `appchi-delivery`:

- `node docs/developer-guide/build.mjs`: generated thirteen chapters in a single
  self-contained HTML file with embedded styles and JavaScript.
- `node --check docs/developer-guide/guide.js` and
  `node --check docs/developer-guide/build.mjs`: passed.
- HTMLParser-based local inspection: 79 static links resolve; 45 static IDs are
  unique. Repository explorer inspection: all 38 starting source files and eleven
  READMEs exist. Literal control IDs, select values and state handlers match.
- `python qualification/verify_documents.py`: passed after the final additions.
- `git diff --check -- README.md`: passed. No existing numbered design decisions
  or original issue identifiers were changed.

Browser verification used the Codex in-app browser and a temporary loopback-only
HTTP preview, without starting Appchi:

- All eleven repository selections display their owner and source links.
- All six pipeline scenarios advance to their expected final frame; next is
  disabled at the end. Previous/restart return to the initial boundary.
- Assignment derives Acknowledged; resolution retains assignment; a new delivery
  repeating the previous firing payload reopens occurrence 2 and clears assignment.
- Overlapping source/maintenance causes remain after window end; a later repeat
  closes only maintenance; firing input clears source suspension. Manual reopen
  was also exercised at phone width.
- All five dependency explanations and three learning tracks update correctly.
- Quiz empty/wrong answers produce feedback; six correct selections yield 6/6.
- Topic search returns matches and a useful no-result state. Chapter completion
  survives reload and resets to 0/11.
- Desktop rendering and 390 × 844 layout were inspected. A long source-link
  overflow was corrected; final document width was 375 CSS pixels at a 390-pixel
  viewport. No browser JavaScript errors were reported.

Evidence: [desktop](developer-guide-2026-10-11/desktop.png),
[pipeline lab](developer-guide-2026-10-11/pipeline.png),
[phone layout](developer-guide-2026-10-11/mobile.png).

## Limits

These checks validate the guide and its learning models, not Appchi runtime
behavior. Displayed installation, migration, service and integration commands were
not executed. The guide's simulations are explicitly partial and synthetic.
Browser policy disallowed direct `file:` navigation, so interactions were tested
over loopback HTTP; source inspection confirms no external script/style assets or
network calls. Source links were checked against the local filesystem, not the
preview server's intentionally narrow document root. Clipboard permissions,
blocked-storage fallback, print pagination and a full accessibility audit were
not tested. No publishing or production qualification was performed.

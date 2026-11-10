# Daily user guide verification

The user requested a full sectioned client guide based on Markdown, with images and explanations of feature concepts and when/how to use them. The user selected **daily users only** as the audience.

Delivered source: [client guide](../docs/client-guide/README.md), seven chapters, seven images (three existing synthetic screenshots and four original SVG diagrams), a glossary, troubleshooting tables, a maintainer source map and a generated self-contained HTML reading edition. Delivery README links the guide. Application source, normative contracts and deployment configuration are unchanged by this task.

Source revision context and existing discrepancies are recorded in [Sources and maintenance](../docs/client-guide/sources-and-maintenance.md). Other people's pre-existing working changes were preserved. Documentation-only verification does not qualify runtime behavior, private services or a release.

## Checks

Executed from `keep-delivery`:

- `node docs/client-guide/build-guide.mjs <bundled-marked-module>`: passed. Generated seven chapters plus the contents article, with all images embedded. The supplied module was the existing bundled `marked/lib/marked.esm.js`; no dependencies were installed or locks changed.
- `python qualification/verify_documents.py`: passed across 136 workspace Markdown files and 566 ownership entries; no broken local links or unbalanced fences, and existing REV-01 through REV-38 numbering remained intact.
- A focused Python standard-library content audit passed: nine guide Markdown files, four parseable SVGs, 14 embedded HTML image elements (seven images plus their enlargement copies), 43 valid internal links, no duplicate HTML IDs, no external HTML image dependency, no missing alt text or trailing Markdown whitespace. SHA-256 comparison confirmed all three screenshot copies match their evidence originals.
- `git diff --check`: passed. Its normal Git scope does not include untracked guide files; the focused content audit covers their whitespace and the workspace document checker includes them.

Browser review used a temporary loopback-only static preview server and the Codex in-app browser. Observed default viewport width 1280 and document width 1265; at the 390 × 844 override, document width was 375 (scrollbar excluded), so neither showed page-wide horizontal overflow. All 14 image instances loaded. Inspected the contents, chapter typography, concept diagram and maintenance timeline; tested chapter links and the full-size illustration expander, which opens an internally scrollable 1100-pixel image. Mobile text wraps within the chapter. The viewport override was reset afterward.

An independent source review corrected the initial company sign-in entry step, moved Dashboard references inspection before entering the preset editor, and clarified that active incidents include both Open and Investigating. Those corrections were incorporated into the Markdown and regenerated HTML.

Print styles are included, but paper/PDF pagination was not separately rendered or certified. No application runtime, backend, browser product workflow or deployment tests were run for this documentation-only task. Existing product screenshots retain their original evidence scope; they are not fresh integration evidence.

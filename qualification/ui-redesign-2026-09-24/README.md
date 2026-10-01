# UI review evidence — 2026-09-24

These 1440 × 900 screenshots use synthetic records served by a temporary isolated local API. Names, counts and timestamps are review fixtures, not company operational evidence. The production frontend does not contain this API or its sample data. The degraded live indicator reflects the temporary API's incomplete stream implementation.

- [Alerts](alerts.png): compact CEL/query bar, quick views, severity and lifecycle indicators, ownership and incident metadata. Final table scrollWidth/clientWidth were both 1299 pixels.
- [Dashboard](dashboard.png): locally served Geist/Geist Mono, exact counts, threshold colors and labels, pause/fullscreen controls and provenance.
- [Saved preset, continuation review](preset-resume.png): compact filter above the alert table, matching the recovered S05 mock at 1440 × 900. The page measured 1440 pixels wide with no document overflow; the table occupied approximately 659 pixels of viewport height.
- [Preset editor, continuation review](preset-editor-resume.png): alert preview remains beside the definition editor, matching the recovered S06 layout. This capture shows an unchanged draft, so Save changes is disabled. A later correction makes custom column choices take effect; the default layout is retained.
- [Custom column draft, continuation review](preset-custom-columns-resume.png): removing columns updates the preview immediately while retaining the filter editor, unsaved marker and table scrolling. This temporary review draft was discarded without saving.

See [UI mapping](https://github.com/appchi-alert-operations/appchi-frontend/blob/main/docs/ui-design.md) for reference provenance and [implementation status](../../status/implementation-status.md#ui-reference-implementation--2026-09-24) for executed browser/build evidence. These captures are manual visual review, not a pixel-diff certification.

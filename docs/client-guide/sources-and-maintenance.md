# Guide sources and maintenance

This is a maintainer reference for the [daily user guide](README.md), separate from the client reading edition. The guide's audience is daily users with View or Edit access, as explicitly selected by the user. It excludes identity administration, operator/key management, deployment and infrastructure operations.

## Editorial acceptance criteria

- Explain each daily-user feature's purpose, when to use it, how to use it and the expected effect.
- Cover navigation/access, Feed/CEL, alert ownership/lifecycle/notes/history, incidents, shared presets/dashboards, maintenance/correlation, troubleshooting and terms.
- Use current UI labels where verified; distinguish temporary choices, drafts and shared saves.
- Include accessible diagrams and authentic product screenshots with synthetic-data captions.
- Apply current scope corrections over superseded timer, acknowledgement and dashboard guidance.
- Keep Markdown as the editable chapter sources and provide an offline HTML reading edition.
- Validate local links, images, Markdown fences and rendered desktop/narrow layouts. Documentation checks do not establish runtime or release qualification.

## Source precedence and revision context

Prepared on 11 October 2026 using these checked-out revisions **plus their existing uncommitted working changes**. The hashes alone do not reproduce the source snapshot. The delivery release manifest was inspected; it pins these HEADs, but is not a release qualification for the local edits.

| Repository | HEAD when inspected | Use |
|---|---|---|
| keep-design | `99c5def93c761386e7d5161c44f482ae4cbd7539` | Authoritative contracts and current corrections |
| keep-frontend | `5c1bfd052042b0df8538cd73f2395e2fd7f70a82` | Verified control labels and current screen behavior in source |
| keep-delivery | `ee37546e771e435d1f07c2b88857f548dd05fbb0` | Implementation/readiness evidence and screenshots |

**Revised 24 October 2026:** the Incidents and Troubleshooting chapters were updated after keep-frontend `42e91ce` (branch `codex/remove-stale-incident-deadlines`, based on `9ed49b0`) removed the incident inactivity and retention deadline labels. Other chapters were not re-inspected for that revision.

**Revised 27 October 2026:** the Feed and search, Presets and dashboards, and Troubleshooting and glossary chapters, the first-shift steps in the guide contents and the [shared-views diagram](assets/shared-views.svg) were updated for three UI changes in the [design register](../../../keep-design/decisions/design-register.md). REV-43 removed the Active quick view, moved row actions into a ⋯ menu, showed row times relative to now, removed revision numbers from the preset and dashboard subtitles, and quietened the live-status notices. REV-44 added Group by on Feed and saved-preset results, and an optional saved preset grouping whose results-toolbar change is view-only. REV-45 added Assigned to me, made pressing the selected quick view return to All, and renamed the toolbar's Reset to saved to Reset. Sources were keep-design `9d189981ee42ef2af32b7a86d9ac1c112e3d50ad` (REV-43–45 and [preset section 4](../../../keep-design/specs/spec-preset-operations.md#4-columns-and-sorting)) and keep-frontend `7d72ad94fbaad052cf616d5766a520947cfa43ce` (`alert-table.tsx`, `alert-groups.tsx`, `alerts.tsx`, `presets-pages.tsx`, `definition-editor.tsx` and `page.tsx`), both on the unmerged `codex/alert-grouping` branches; the frontend's only uncommitted change, in `administration.tsx`, does not affect these screens. Getting started, Working with alerts, Incidents and Maintenance and correlation were not fully re-inspected. The Feed and dashboard screenshots were not recaptured; their captions now say what they no longer show.

The guide does not replace the specifications. [Worker scope](../../../keep-design/specs/spec-worker-scope.md) overrides older automatic maintenance-release, inactivity, retention and assignment-cleanup algorithms. Current UI corrections override older acknowledgement and dashboard controls. [Delivery readiness](../../status/delivery-readiness.md) keeps production gates open; this guide does not close them.

## Chapter authority map

| Chapter | Primary Markdown sources |
|---|---|
| Getting started | [System design](../../../keep-design/specs/spec-alerts-management-system.md), [tenant access](../../../keep-design/specs/spec-tenant-access.md), [identity](../../../keep-design/specs/spec-identity-configuration.md) |
| Feed and working alerts | [Alert operations](../../../keep-design/specs/spec-alert-operations.md), [history/search](../../../keep-design/specs/spec-alert-history-technical.md), [UI design](../../../keep-frontend/docs/ui-design.md) |
| Incidents | [Incident operations](../../../keep-design/specs/spec-incident-operations.md), [worker scope](../../../keep-design/specs/spec-worker-scope.md) |
| Presets and dashboards | [Preset operations](../../../keep-design/specs/spec-preset-operations.md), [dashboard operations](../../../keep-design/specs/spec-dashboard-operations.md), [UI corrections](../../../keep-frontend/docs/ui-design.md) |
| Maintenance and correlation | [Maintenance](../../../keep-design/specs/spec-maintenance-operations.md), [correlation](../../../keep-design/specs/spec-correlation-rules.md), [Kafka pipeline](../../../keep-design/specs/spec-kafka-alert-pipeline.md), [worker scope](../../../keep-design/specs/spec-worker-scope.md) |
| Troubleshooting | The above and [shared system contracts](../../../keep-design/specs/spec-system-completion.md) |

Read current [implementation status](../../status/implementation-status.md) together with readiness before making environment-availability claims. Frontend components were inspected to verify labels and workflows; they do not override normative behavior.

## Screenshot and illustration provenance

The copied PNGs are unmodified evidence images using synthetic data. The SVGs are original, editable concept diagrams created for this guide; they are not product screenshots.

| Guide asset | Original / basis |
|---|---|
| [sign-in.png](assets/sign-in.png) | [Login-theme screenshot](../../qualification/login-theme-2026-09-27/login.png) |
| [feed-desktop.png](assets/feed-desktop.png) | [Feed screenshot of 11 October](../../qualification/feed-rename-2026-10-11/feed-desktop.png); out of date since REV-42–45: it shows the removed Active view, older colours, row source lines and the Times in label, and lacks Assigned to me and Group by, as the guide caption says |
| [dashboard.png](assets/dashboard.png) | [Dashboard toolbar evidence](../../qualification/dashboard-provenance-removal-2026-10-09/toolbar-dirty.png); its older sidebar still says Alerts, and it shows older colours and the revision subtitle that REV-43 removed, as the guide caption says |
| [concept-map.svg](assets/concept-map.svg) | System product model and tenant boundaries |
| [shared-views.svg](assets/shared-views.svg) | Preset saved definition versus transient filter contract |
| [incident-ownership.svg](assets/incident-ownership.svg) | Single active ownership and independent alert/incident lifecycle |
| [maintenance-timeline.svg](assets/maintenance-timeline.svg) | Current source-driven maintenance release scope |

Older preset editor screenshots were inspected and deliberately excluded because they show obsolete acknowledgement controls. The historical architecture PNG was not used because its pre-Kafka architecture is superseded and is not needed for daily users.

## Known source and interface discrepancies

- The incident membership search placeholder suggests text or CEL, while the current implementation submits its text as the plain-text query with CEL `true`. The guide only promises text search in that dialog.
- The preset specification permits viewer temporary exploration more broadly than the current editor screen, which requires Edit access. The guide directs viewers to Feed for ad hoc expressions and documents only the temporary controls actually offered on saved results.
- Some early dashboard prose still names Pause updates or Count provenance. Later explicit UI corrections remove those controls. The guide uses Discard changes, automatic refresh and Refresh counts.

No new product behavior or normative decision is introduced. These discrepancies are documented here to prevent the guide from propagating stale behavior; existing specifications and decision identifiers remain unchanged.

## Rebuild the reading edition

The Markdown chapters and SVGs are source files. PNGs preserve evidence. [build-guide.mjs](build-guide.mjs) generates [index.html](index.html) using the `marked` Markdown renderer; it embeds every image so the resulting HTML can be read offline or shared as one file. No external fonts, scripts or network image requests are required.

From this directory, use an available installation of `marked`:

```powershell
node build-guide.mjs <path-to-marked/lib/marked.esm.js>
```

If `marked` is resolvable by Node, omit the argument. The argument is an environment path, not a committed dependency override. Regenerate after editing a chapter or image. The build removes maintainer Reference sections from the reading edition and converts chapter links to internal navigation.

## Verification record

See [guide verification](../../qualification/client-guide-2026-10-11.md) for actual documentation and rendering results, and [incident deadline label removal](../../qualification/incident-deadline-labels-2026-10-24.md) for the 24 October revision. The 27 October revision was checked with delivery's `python qualification/verify_documents.py` and a rebuild of the reading edition; no separate qualification record was written. No application runtime suite is needed for this documentation-only addition; none is claimed.

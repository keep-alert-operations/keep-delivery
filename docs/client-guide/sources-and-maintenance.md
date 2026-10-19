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
| appchi-design | `99c5def93c761386e7d5161c44f482ae4cbd7539` | Authoritative contracts and current corrections |
| appchi-frontend | `5c1bfd052042b0df8538cd73f2395e2fd7f70a82` | Verified control labels and current screen behavior in source |
| appchi-delivery | `ee37546e771e435d1f07c2b88857f548dd05fbb0` | Implementation/readiness evidence and screenshots |

The guide does not replace the specifications. [Worker scope](../../../appchi-design/specs/spec-worker-scope.md) overrides older automatic maintenance-release, inactivity, retention and assignment-cleanup algorithms. Current UI corrections override older acknowledgement and dashboard controls. [Delivery readiness](../../status/delivery-readiness.md) keeps production gates open; this guide does not close them.

## Chapter authority map

| Chapter | Primary Markdown sources |
|---|---|
| Getting started | [System design](../../../appchi-design/specs/spec-alerts-management-system.md), [tenant access](../../../appchi-design/specs/spec-tenant-access.md), [identity](../../../appchi-design/specs/spec-identity-configuration.md) |
| Feed and working alerts | [Alert operations](../../../appchi-design/specs/spec-alert-operations.md), [history/search](../../../appchi-design/specs/spec-alert-history-technical.md), [UI design](../../../appchi-frontend/docs/ui-design.md) |
| Incidents | [Incident operations](../../../appchi-design/specs/spec-incident-operations.md), [worker scope](../../../appchi-design/specs/spec-worker-scope.md) |
| Presets and dashboards | [Preset operations](../../../appchi-design/specs/spec-preset-operations.md), [dashboard operations](../../../appchi-design/specs/spec-dashboard-operations.md), [UI corrections](../../../appchi-frontend/docs/ui-design.md) |
| Maintenance and correlation | [Maintenance](../../../appchi-design/specs/spec-maintenance-operations.md), [correlation](../../../appchi-design/specs/spec-correlation-rules.md), [Kafka pipeline](../../../appchi-design/specs/spec-kafka-alert-pipeline.md), [worker scope](../../../appchi-design/specs/spec-worker-scope.md) |
| Troubleshooting | The above and [shared system contracts](../../../appchi-design/specs/spec-system-completion.md) |

Read current [implementation status](../../status/implementation-status.md) together with readiness before making environment-availability claims. Frontend components were inspected to verify labels and workflows; they do not override normative behavior.

## Screenshot and illustration provenance

The copied PNGs are unmodified evidence images using synthetic data. The SVGs are original, editable concept diagrams created for this guide; they are not product screenshots.

| Guide asset | Original / basis |
|---|---|
| [sign-in.png](assets/sign-in.png) | [Login-theme screenshot](../../qualification/login-theme-2026-09-27/login.png) |
| [feed-desktop.png](assets/feed-desktop.png) | [Current Feed screenshot](../../qualification/feed-rename-2026-10-11/feed-desktop.png) |
| [dashboard.png](assets/dashboard.png) | [Current dashboard toolbar evidence](../../qualification/dashboard-provenance-removal-2026-10-09/toolbar-dirty.png); its older sidebar still says Alerts, explicitly called out in the guide caption |
| [concept-map.svg](assets/concept-map.svg) | System product model and tenant boundaries |
| [shared-views.svg](assets/shared-views.svg) | Preset saved definition versus transient filter contract |
| [incident-ownership.svg](assets/incident-ownership.svg) | Single active ownership and independent alert/incident lifecycle |
| [maintenance-timeline.svg](assets/maintenance-timeline.svg) | Current source-driven maintenance release scope |

Older preset editor screenshots were inspected and deliberately excluded because they show obsolete acknowledgement controls. The historical architecture PNG was not used because its pre-Kafka architecture is superseded and is not needed for daily users.

## Known source and interface discrepancies

- Incident creation/detail help still describes automatic inactivity resolution/retention deadlines. The guide follows the later worker-scope decision and tells clients not to rely on those labels. Product UI correction is outside this documentation change.
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

See [guide verification](../../qualification/client-guide-2026-10-11.md) for actual documentation and rendering results. No application runtime suite is needed for this documentation-only addition; none is claimed.

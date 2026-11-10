# Keep daily user guide

Keep brings alerts, ownership and incident investigation into one shared workspace. Use it to find problems that need attention, record who is handling them, connect related alerts and keep your team informed through shared views and notes.

This guide is for people who use Keep during their working day, with either **View** or **Edit** access. It covers daily operations, including shared configuration that editors can change. Ask your administrator for account access, integrations and system administration.

**Start here:** read Getting started, then follow the Feed and Working alerts chapters. Keep Troubleshooting nearby during your first shift. The [illustrated reading edition](index.html) brings all seven chapters together in one searchable, printable page.

## Sections

| Section | What you will learn |
|---|---|
| [1 Getting started](01-getting-started.md) | Sign in, choose a tenant, understand access and learn the core concepts. |
| [2 Feed and search](02-feed-and-search.md) | Find the right alerts using quick views, text search and expressions. |
| [3 Working alerts](03-working-alerts.md) | Take responsibility, investigate, add notes and resolve alerts. |
| [4 Incidents](04-incidents.md) | Coordinate related alerts without confusing incident status with alert status. |
| [5 Presets and dashboards](05-presets-and-dashboards.md) | Reuse searches, monitor counts and safely change shared views. |
| [6 Maintenance and correlation](06-maintenance-and-correlation.md) | Understand suppression and automatic grouping, and use editor controls carefully. |
| [7 Troubleshooting and glossary](07-troubleshooting-and-glossary.md) | Recover from common problems and look up unfamiliar terms. |

## Your first shift

1. Check the **tenant** and your access beside the page title.
2. Open **Feed**, or a team preset, and check whether the results are current.
3. Choose **Firing** for unassigned work that needs an owner, or **Assigned to me** for your own. To see every firing alert, assigned or not, select **All** and run `status == "firing"`.
4. Open a relevant alert. Read its details, suppression state, history and incident links before taking action.
5. With Edit access, use **Assign → Assign to me** when you take responsibility. Add a note describing your investigation and next step.
6. If related alerts need coordinated work, use an existing incident or create one. Confirm the responsible person and record the handoff.
7. Resolve only after checking the situation. Closing an incident and resolving its alerts are separate actions.

## Choose the right tool

| Your question | Go to |
|---|---|
| What needs attention right now? | Feed or a shared preset |
| What happened to this one alert? | Alert detail, activity and source history |
| Who is responsible? | Alert assignment or incident assignment; check both |
| Are these alerts part of the same investigation? | Incidents |
| How do I repeat this search next shift? | Presets |
| How many alerts match our team views? | Boards, the dashboard area |
| Why is an alert hidden or excluded from a count? | Include suppressed, then alert suppression details |
| Why did Keep group these alerts automatically? | Incident origin and Rules |

## Reading the examples

Screenshots use synthetic demonstration data. Names and counts are examples, not your tenant's data. Concept diagrams explain behavior rather than reproduce screens. The text uses current controls as of 11 October 2026.

A feature may be unavailable in your environment or to your role. An error or stale indicator is not an empty result. Follow the recovery guidance rather than treating a failed query as proof that there are no alerts.

This guide describes the current product scope; it does not announce a production release. Its Markdown sources, screenshot provenance and maintenance instructions are recorded separately in [Sources and maintenance](sources-and-maintenance.md).

**Begin:** [Getting started →](01-getting-started.md)

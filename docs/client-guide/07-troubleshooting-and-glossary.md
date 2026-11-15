# Troubleshooting and glossary

[Guide contents](README.md) · Previous: [Maintenance and correlation](06-maintenance-and-correlation.md)

## Find missing alerts and unexpected counts

| What you see | What to check or do |
|---|---|
| An expected alert is missing | Check tenant, applied CEL, Search text and quick view. Select All, clear the text filter and inspect Include suppressed. In grouped results, expand collapsed groups, load the rest of each group or choose No grouping. Check freshness and refresh before concluding the alert is absent. |
| Firing leaves out some firing alerts | Firing shows firing alerts without an assignee; Acknowledged shows those with one. For both, select All and run `status == "firing"`. There is no Active quick view. |
| A quick view switched itself off | Pressing the selected quick view again returns to All. Select the view once to apply it. |
| Assigned to me shows fewer alerts than expected | It shows only alerts currently assigned to you, combined with the applied CEL, Search text and Include suppressed. Alerts assigned to colleagues, or unassigned, are not included. |
| Group by shows one table and says there are too many values | The field has more than 1,000 distinct values in the result. Narrow the query or group by another field. |
| A preset opens with a different grouping than you chose | A Group by change in a preset's results toolbar is for your current view only. Reopening the preset or a newly saved revision returns to its saved grouping; use Reset to return yourself. Edit the preset to change the saved grouping. |
| A resolved alert still appears | All and `true` do not exclude resolved alerts. Use an appropriate quick view or lifecycle condition. |
| A query for suppressed alerts shows nothing | Turn on Include suppressed as well. The switch is applied in addition to the query. |
| Maintenance ended but suppression remains | The next qualifying source update can close expired coverage. Time passing alone does not release it; another maintenance or source cause may also remain. |
| A dashboard count differs from an open list | Check the preset, text/quick filters, suppression switch and freshness. A dashboard uses the saved query with suppression excluded; a list can have temporary filters and a newer snapshot. |
| A saved preset lost a filter you selected | Quick views, Search text, Include suppressed and a results-toolbar Group by change are temporary. Only the name, CEL, columns, sorting and optional grouping are saved. |
| A count is unavailable or marked stale | Treat it as unknown or older data. Refresh and check the service message; do not interpret it as zero. |
| An old next-page link stops working | Restart from the first page or refresh. Result snapshots expire, and configuration lists can change while you browse. |

## Recover from access and save problems

| What you see | What to do |
|---|---|
| Assign, Save or Create is missing | Check your tenant and role. View access permits investigation but not operational changes. Ask your tenant administrator if your duties require Edit access. |
| An assignee is missing from the picker | Use an eligible person with access to the tenant. Ask an administrator about access or identity updates; typing a name cannot grant access. |
| A tenant or shared link is unavailable | Choose an accessible tenant and check the link with its sender. Access may have changed, or the resource may have been deleted. |
| Session expired | Sign in again on Keep's sign-in page with your company username and password. A session lasts 24 hours from sign-in; activity does not extend it. |
| CEL validation failed | Read the marked field or expression diagnostic. Use the editor's supported field suggestions, correct the expression and rerun it. An invalid query does not mean no matches. |
| Someone changed the record before you saved | Reload/review the latest record and reconsider your action. For shared definitions, preserve your intended changes while comparing, or use Save as new if a separate shared view is appropriate. |
| A command timed out and the outcome is uncertain | Use **Retry original command** or the specific retry offered by the page. Do not repeatedly create a new record to guess whether the first request worked. Inspect the current state when it becomes available. |
| Preset deletion is blocked | Open Dashboard references. An editor must remove or replace active references before deleting that preset. |
| Dashboard Save is disabled | There may be no changes, a panel dialog may still be open, or another command may be in progress. Apply or cancel the dialog and check the feedback. |
| New panel has no count | Save the dashboard after choosing its saved preset. The panel dialog alone does not commit it. |

**Live updates degraded · reconnecting**, **Updates unavailable** or **Catching up · counts may lag** beside the page actions means the display may be catching up; use **Refresh** where it is offered. Keep the distinction between the latest loaded detail and the search result list. If the condition persists or affects a decision, use your organization's support route.

## Avoid common workflow mistakes

- Assignment records responsibility; it does not repair a service or send a notification.
- Resolving an incident does not resolve its linked alerts, and resolved alerts do not automatically resolve their incident.
- Suppression keeps alerts stored. It is neither deletion nor resolution.
- A new maintenance window does not sweep all existing matching alerts into suppression.
- A rule test or query preview does not perform the operation it describes.
- Shared Save changes what other tenant users see. Save as new is also shared.
- A source can update an alert again after your manual action. Read the new occurrence and activity rather than assuming the earlier command failed.
- There is no automatic inactivity resolution or retention sweep in the current release. An incident stays active until someone resolves it, so close neglected work explicitly.

## Give support a useful report

Include the tenant, page or record link, what you expected, what happened, and the approximate time with timezone. Include the exact visible error or request identifier if provided, the relevant filter, and whether the problem affects one record or many. For a save problem, say whether the page reported failure, conflict or an uncertain outcome.

Use your organization's approved channel. Keep passwords, API keys and session information out of the report. Review screenshots and alert contents before sharing beyond the people authorized to read them. Your tenant administrator handles access and shared configuration questions; the Keep support team handles persistent application failures.

## Glossary

| Term | Plain-language meaning |
|---|---|
| Active alert | An alert whose raw lifecycle is firing; it may be assigned or unassigned. CEL `status == "firing"` selects them. |
| Active incident owner | The one active incident (Open or Investigating) currently owning an alert's active membership. Historical links can remain elsewhere. |
| Acknowledged | A firing alert with an assignee. There is no separate acknowledgement button. |
| Application | The source application value used to identify and filter an alert. |
| Assignee | A person recorded as responsible. Alert and incident assignees are independent. |
| CEL | Common Expression Language, the expression syntax used to select alerts. Use supported fields and functions from Keep's editor. |
| Correlation | Automatic creation or linking of incidents from qualifying alert evidence. |
| Draft | Changes held in your browser tab that have not been saved to shared configuration. |
| Feed | The page for exploring and acting on alert records. |
| Firing | In visible status, a firing alert with no assignee. In raw lifecycle, a condition that is currently firing regardless of assignment. |
| Global | The fixed shared tenant that receives legacy alerts. |
| Grouping | Showing results as one table per value of one field, such as severity or application. A preset can save one grouping. |
| History | Retained source, activity, note or configuration records, depending on the resource. It is not an unlimited archive. |
| Incident | An investigation that groups related alerts and has its own lifecycle and owner. |
| Indexing lag | A delay before an accepted state change appears in search results or counts. |
| Logical alert | The continuing alert record; many source deliveries or occurrences may belong to it. |
| Maintenance window | A one-time interval and matching condition used to suppress qualifying alert activity. |
| Occurrence | One firing episode within an alert's continuing record. |
| Operator | A configured source identity for authenticated alert delivery. Its setup and keys are managed by administrators. |
| Preset | A shared saved alert query, columns, sort order and optional grouping. |
| Quick view | A one-click temporary narrowing of results, such as Critical or Assigned to me. It is never saved. |
| Revision | A version number for a saved resource, used to detect changes and avoid overwriting another user's work. |
| Severity | The seriousness recorded for an alert or incident; separate from lifecycle and suppression. |
| Snapshot | A captured view of search results used for consistent paging. It is not a permanent report. |
| Source update | A new input from the monitoring source; even a repeat can matter to maintenance release. |
| Stale | A retained older result whose currency is no longer assured. |
| Suppression | Exclusion from ordinary alert feeds/counts and automatic correlation while the record continues to exist. |
| Tenant | A shared workspace with its own data and access permissions. |
| Threshold | A count boundary that selects a dashboard panel color; it performs no operational action. |

## Quick reference for handoff

Before leaving an investigation, check the correct tenant, the current alert and incident owners, a note stating findings and next action, unresolved linked alerts, and any suppression that could hide continuing work. Confirm a shared definition was saved if you meant others to use it. Tell the next person through your normal team channel; Keep does not notify them automatically.

## Reference

[Current scope](../../../keep-design/specs/spec-worker-scope.md) · [Shared behavior and errors](../../../keep-design/specs/spec-system-completion.md) · [Access](../../../keep-design/specs/spec-tenant-access.md) · [Search and live updates](../../../keep-design/specs/spec-alert-history-technical.md)

[Return to guide contents](README.md)

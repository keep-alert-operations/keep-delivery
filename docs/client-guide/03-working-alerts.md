# Working alerts

[Guide contents](README.md) · Previous: [Feed and search](02-feed-and-search.md) · Next: [Incidents](04-incidents.md)

Use alert detail to understand the problem, take ownership, document your investigation and record resolution. Readers can inspect details and history. Changing assignment, status or notes requires edit access in the selected tenant; these everyday actions do not require an administrator role.

## Review before taking action

1. Open the alert from **Feed** or a preset.
2. Read its message, severity, status, assignee and any **Suppressed** badge.
3. Check **Details** for application, component, node, site, impact and receipt times. **First received** describes when this logical alert was first received; **Last received** describes its latest source receipt.
4. Read **Notes** and **History** to understand work already done. If a linked incident appears, open it for the wider investigation.
5. Use **Open runbook** when a runbook link is supplied.

**View alert** opens a read-only JSON view of the currently loaded alert. It is useful when support asks for an exact field value. **Source events → View source details** instead shows a particular received message. The current alert and an older source message can legitimately differ.

## Assign responsibility

Assignment is Appchi's acknowledgement mechanism. There is no separate acknowledgement button.

1. Select **Assign**.
2. Choose **Assign to me**, or enter at least two characters in **Assign alert to a user**, select **Search**, and choose an eligible person.
3. Check the assignee and resulting status. A firing alert becomes **Acknowledged** as soon as assignment succeeds; the chosen person does not have to accept it in Appchi.

To hand over, choose another person from the same menu and add a note describing the next action. To release ownership, select **Unassign**. An unassigned firing alert displays **Firing** again.

You can assign resolved and suppressed alerts too. A resolved alert continues to display **Resolved** even when assigned. Assignment does not give someone access to a tenant, and Appchi provides no product notification for the handover; use your team's agreed communication process when needed.

If a person is unavailable in the picker, do not substitute a different owner merely to make the action succeed. Check the intended person and raise the access issue with your support contact.

## Add notes that help the next person

1. In the **Notes** panel, enter a concise update in **Add a note**.
2. Include what you checked, the finding, the action taken and what remains.
3. Select **Add note** and confirm it appears.

For example: “Checked payments nodes A and B at 14:20. Only A reports connection timeouts. Restarted its connection pool; waiting for the next source update before resolving.”

Notes are plain text, up to 10,000 characters. They stay with the logical alert through later occurrences. A note is not a new occurrence, a status change or a message sent to another user.

Use **Edit note → Save note** to correct an entry, or **Discard note edit** to abandon your draft. Any tenant editor can edit or delete an alert note, including someone else's. **Delete note** asks for confirmation and hides its current text; retained revisions remain accessible through **Show deleted** and **View note history**. Deleting a note does not erase its audit history.

## Resolve or reopen deliberately

Choose **Resolve alert** when your investigation and team procedure justify marking the alert resolved. Confirm the status becomes **Resolved**. This records a product action; it does not fix the monitored system or stop the sender.

A later firing update from the source can reopen the same alert. If you already know it needs renewed attention, choose **Reopen alert** on a resolved alert. Either form of reopening begins another occurrence and clears the alert's assignment; assign responsibility again if appropriate.

Resolution preserves the assignee until a new occurrence begins. Ordinary repeated firing updates preserve assignment. Resolving or reopening does not automatically remove every suppression cause or unlink the alert from its incident. When resolving an alert leaves every alert linked to its active incident resolved, the incident resolves too; otherwise review the [incident](04-incidents.md) separately.

There is no alert Delete action, and you cannot edit source-owned fields such as message, severity, application or source creation time through alert detail. Use a note for your observations. The current release does not automatically resolve alerts merely because they have been inactive.

## Understand occurrences and history

An occurrence is one firing period of a logical alert. For example, occurrence 1 opens, receives ten repeated updates, and resolves; the next firing opens occurrence 2 on that same alert. Repeats do not each become new occurrences. An alert first seen already resolved can show occurrence 0 because Appchi did not observe a firing period.

| Detail tab | Use it to answer |
| --- | --- |
| **History** | Who changed assignment or status, and what changed over time? |
| **Notes** | What investigation context has been recorded? |
| **Occurrences** | How often has this alert reopened, and when did each firing period end? |
| **Source events** | What updates did the monitoring source send? |

Choose **Order → Newest first** for the latest activity or **Oldest first** to reconstruct a sequence. Source event details distinguish supplied creation time from receipt time. A delayed event can describe an earlier source time while appearing later in processing history. Avoid inferring a new occurrence just because two timestamps differ.

Older history may contain legacy acknowledgement actions. Current ownership is determined by the present assignee, as described above.

## Apply the same action to selected alerts

Use bulk actions only after confirming that the selected alerts need the same change.

1. In Feed or a preset result table, select the row checkboxes. The heading checkbox selects rows on the current page, up to the 100-alert selection limit.
2. Check the **selected** count. Even when 200 rows are displayed, one command cannot include more than 100 alerts.
3. Choose **Resolve**, or **Assign…** followed by **Assign to me**, **Unassign**, or a person from the picker.
4. Confirm the result. Use **Clear** to abandon a selection.

Selection applies to explicit rows, not every result matching the query. Changing pages or filters can clear it. The current bulk toolbar has no Reopen action; reopen alerts individually from their detail page.

Bulk changes succeed together. If one selected alert changed since you selected it, or another check fails, none of the selected alerts changes. Refresh, review and reselect the intended rows before submitting again.

## Handle conflicts and uncertain results

An alert can change while you are reading it because another user or the source updated it. A conflict protects those newer changes. Review the latest state and your draft before trying the intended action again.

If Appchi says the outcome is uncertain, the original action might already have succeeded. Use the provided **Retry original command** control when offered; it repeats that command safely. Do not assume an old Feed row proves the action failed: detail can update before search. See [Troubleshooting and glossary](07-troubleshooting-and-glossary.md) for access errors, stale views and information to include in a support report.

## Reference

The guide's source documents are [alert operations](../../../appchi-design/specs/spec-alert-operations.md), [alert history](../../../appchi-design/specs/spec-alert-history-technical.md), [current worker scope](../../../appchi-design/specs/spec-worker-scope.md), and [frontend UI design](../../../appchi-frontend/docs/ui-design.md).

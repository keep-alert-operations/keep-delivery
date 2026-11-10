# Operator diagnostics retention

Current scope: automated retention workers are deferred under REV-34. The
procedures below preserve historical implementation context and do not authorize
deploying those roles. See [current worker scope](https://github.com/keep-alert-operations/keep-design/blob/main/specs/spec-worker-scope.md).

Apply the finalized migration 0037 before starting `operator-retention`. This
role requires normal runtime Settings, the current deployment epoch, a worker
database identity and the qualified recovery capability. Do not enable recovery
just to run cleanup. Deployment remains subject to private nonproduction checks.

```powershell
.venv/Scripts/python.exe -m keep.worker operator-retention --once
```

`--once` processes one bounded page, not every eligible operation. Continuous
mode retains a scan cursor between pages and waits one hour after a full pass.
A restart safely begins a fresh scan. Each operation removes at most 100 attempt
rows per pass; scan turns stop between operations after their 100 ms work budget.
Verify backlog and supplied capacity before claiming the 24-hour cleanup target.
Use the existing OpenShift worker template with `WORKER_ROLE=operator-retention` and
its own database pool allocation. No Kong or Grafana credentials are needed.

Only explicitly completed, cancelled or superseded operations with a completion
instant at least 90 elapsed days old qualify. Unfinished work never expires by
age. Outstanding command receipts retain their original operation dependency
until the `command-retention` worker independently protects and compacts them.
Legacy receipt bodies are checked too. Pending cancellation, cleanup and key
replacement preserve the exact executor predecessor journals they may consume;
a reserved pending name also protects journals required by a future cancellation.

Cleanup locks the tenant coordinator, operator and operation in that order, then
rechecks eligibility. After bounded attempt-row deletion completes, it erases
progress, actor snapshots, errors, counters and leases. Immutable minimal
operation identity, generation, credential reference and completion time remain
to reject stale workers. Expired terminal diagnostics are hidden from the public
operation read even while dependency-held. Credentials, current remote bindings,
operator state and the separate 365-day management audit are unchanged.

Local evidence: 16 focused tests passed against disposable PostgreSQL, covering
bounded resume, protected receipt compaction, predecessor dependencies, concurrent
commands, restricted worker/API roles, stale-worker rejection and recovery gates.
This does not qualify the company's remote services or cleanup capacity.

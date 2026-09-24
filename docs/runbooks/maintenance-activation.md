# Maintenance proof and activation

Maintenance stays unavailable until retained alert proof is complete and its
capacity allocation has been qualified. The repository development profile is
explicitly unqualified and cannot activate this capability.

Supply the measured profile through `APPCHI_CAPACITY_PROFILE`. Besides the
existing application allocations, maintenance requires positive integer
`maintenance_max_work`, `maintenance_max_inputs`, and `maintenance_max_segments`,
`qualified: true`, a stable profile `id`, and an owner-supplied
`qualification_evidence` reference. These are configuration claims that must be
supported by the private qualification exercise, not results this command invents.

Warm every existing tenant using the maintenance-warmup worker and inspect its
maintenance-proof output. Repeat bounded sweeps until there are no missing
creation baselines, stale input heads, incomplete checkpoints or catalog changes.
The source path records proof even while the capability is disabled. Warmup never
backfills existing alerts into newly created maintenance coverage.

```sh
python -m appchi.worker maintenance-warmup --tenant '<tenant UUID>' --once
python -m appchi.worker maintenance-proof --tenant '<tenant UUID>' --once
python -m appchi.maintenance.activation --operation-reference '<deployment reference>'
```

Use the separately authorized deployment/worker database identity. The activation
command compiles retained catalogs outside write locks, then prevents concurrent
tenant creation and locks every tenant coordinator in UUID order. It rechecks
catalog generations, clock/order health and complete current proof before changing
the gate and appending one platform audit event atomically. Lock/statement timeout
or any mismatch leaves the capability closed. Review failure and repeat warmup;
never invent a missing baseline or set the gate directly.

A source unit prepared under the prior disabled gate must retry if it reaches
its transaction after activation. Repeating activation with the same complete
profile is a no-op. A different profile cannot silently replace an active binding.
This command is not a disable, upgrade, restore-reopen or performance-test tool.

Three local PostgreSQL tests passed: required qualification/proof and no-op audit,
a concurrent coordinator race through gate commit, and a missing-baseline refusal.
These use a synthetic profile and do not establish sustained capacity or the
five-second visibility objective. Include activation lock duration and all
runtime proof/render costs in the private qualification workload.

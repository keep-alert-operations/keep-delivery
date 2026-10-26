"""Enabled-feature fixtures for disposable local scalability runs only.

The profile's ``qualified`` value is a synthetic test input needed to exercise
the production feature branches. It is not a qualification result or a profile
that may be deployed. Configuration precedes every source arrival: no backfill.
Imports stay local so multiprocessing worker startup does not load native CEL.
"""


def synthetic_feature_profile(profile, *, max_alerts_per_tenant):
    if type(max_alerts_per_tenant) is not int or max_alerts_per_tenant < 1:
        raise ValueError("A positive synthetic per-tenant alert bound is required")
    from appchi.cel.compiler import COST_MODEL

    return {
        **profile,
        "qualified": True,
        "scope": "isolated-synthetic-fixture",
        "production_qualified": False,
        "qualification_notice": "Synthetic feature-gate input; not production capacity evidence",
        "correlation": {
            "cost_model": COST_MODEL,
            "enabled_rules_per_tenant": 1,
            "live_groups_per_rule": 2,
            "live_evidence_per_rule": max_alerts_per_tenant,
            "live_evidence_per_tenant": max_alerts_per_tenant,
            "condition_cost": 1_000_000_000,
            "rule_cost": 1_000_000_000,
            "tenant_cost": 1_000_000_000,
            "digest_bucket": 4,
        },
        "maintenance_max_work": 10000,
        "maintenance_max_inputs": 100,
        "maintenance_max_segments": 20,
        "maintenance": {"max_overlap": 20, "max_work_bytes": 1000000},
    }


def install_feature_fixture(db, tenant_ids, *, profile):
    """Create one rule per tenant and windows for the first twenty tenants.

    Call before any source messages or workers. Supply mixed warning/critical
    firing alerts: warning alerts are suppressed only in window tenants;
    other firing alerts qualify for automatic correlation incidents.
    """
    from datetime import timedelta
    from uuid import uuid4

    from appchi.cel import compile_expression
    from appchi.correlation import repository as correlation
    from appchi.maintenance import repository as maintenance
    from appchi.maintenance.models import Create
    from appchi.shared.transactions import coordinator

    if (
        profile.get("scope") != "isolated-synthetic-fixture"
        or profile.get("production_qualified") is not False
        or profile.get("qualified") is not True
    ):
        raise ValueError("Only the explicit synthetic feature profile is accepted")
    tenants = tuple(tenant_ids)
    if not tenants or len(tenants) != len(set(tenants)):
        raise ValueError("Supply distinct fixture tenants")
    with db.transaction() as conn:
        name = conn.execute("SELECT current_database() AS name").fetchone()["name"]
        if not name.startswith("appchi_scale_"):
            raise ValueError("Feature fixture requires a disposable appchi_scale_ database")
        if conn.execute("SELECT EXISTS(SELECT 1 FROM alerts) AS present").fetchone()["present"]:
            raise ValueError("Install features before seeding any alerts")
        actor = {"id": uuid4(), "display_name": "Synthetic scale feature fixture"}
        conn.execute(
            "INSERT INTO principals(id,directory_source,object_id,kind,display_name,eligible,"
            "evidence_generation,verified_at) VALUES(%s,'scale-feature-fixture',%s,'user',%s,true,1,clock_timestamp())",
            (actor["id"], str(actor["id"]), actor["display_name"]),
        )

    definition = {
        "name": "Synthetic firing correlation",
        "description": "Disposable scalability fixture, not production qualification",
        "severity": "critical",
        "window_minutes": 60,
        "group_by": [],
        "conditions": [{"id": str(uuid4()), "label": "Firing", "cel": 'alert.status == "firing"'}],
    }
    template = correlation.compile_conditions(definition)
    program = next(iter(template.values()))
    maintenance_program = compile_expression('alert.severity == "warning"', rule=True)
    for position, tenant in enumerate(tenants):
        condition_id = str(uuid4())
        body = {**definition, "conditions": [{**definition["conditions"][0], "id": condition_id}]}
        programs = {condition_id: program}
        with db.transaction(tenant) as conn:
            _, now = coordinator(conn, tenant)
            correlation.check_capacity(conn, tenant, body, programs, profile)
            correlation.persist(conn, tenant, actor, now, body, programs, str(uuid4()))
            if position < 20:
                request = Create(
                    command_id=str(uuid4()),
                    name="Synthetic warning maintenance",
                    reason="Disposable mixed-traffic scalability fixture",
                    cel='alert.severity == "warning"',
                    timezone="UTC",
                    start_local=(now - timedelta(minutes=1))
                    .replace(tzinfo=None)
                    .isoformat(timespec="seconds"),
                    end_local=(now + timedelta(hours=4))
                    .replace(tzinfo=None)
                    .isoformat(timespec="seconds"),
                )
                candidate = maintenance.candidate(request, now)
                maintenance.check_capacity(conn, tenant, candidate, profile, now)
                maintenance.persist(
                    conn, tenant, actor, now, candidate, maintenance_program, profile=profile
                )

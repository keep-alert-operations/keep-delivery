"""The daily directory refresh CronJob template (REV-75), checked as rendered.

Requirements: REV-75; worker scope 4 (daily scheduled refresh); identity configuration
12 (no bootstrap from the job); system completion 22. Plan A2.

There is no cluster here, so scheduling, Forbid, the deadline and environment
precedence are checked through the template and a local substitution only.
"""

import copy
import json
from pathlib import Path
import re

import pytest

DEPLOY = Path(__file__).resolve().parents[2] / "deploy/openshift"
TEMPLATE = json.loads((DEPLOY / "directory-refresh.template.json").read_text(encoding="utf-8"))
MIGRATION = json.loads((DEPLOY / "migration.template.json").read_text(encoding="utf-8"))
REQUIRED = {
    "PRODUCT_API_IMAGE": "registry.example/keep-api@sha256:" + "a" * 64,
    "CONFIG_SECRET": "keep-identity-env",
    "BINDING_SECRET": "keep-identity-bindings",
    "SERVICE_ACCOUNT": "keep-identity",
    "CPU_REQUEST": "100m",
    "CPU_LIMIT": "500m",
    "MEMORY_REQUEST": "128Mi",
    "MEMORY_LIMIT": "512Mi",
    "EPHEMERAL_STORAGE_REQUEST": "128Mi",
    "EPHEMERAL_STORAGE_LIMIT": "1Gi",
    "TMP_SIZE_LIMIT": "256Mi",
}
PARAMETER = re.compile(r"\$\{\{?([A-Z0-9_]+)\}\}?")


def defaults():
    return {p["name"]: p["value"] for p in TEMPLATE["parameters"] if "value" in p}


def process(template, values):
    """``oc process`` substitution: ``${X}`` inside strings, ``${{X}}`` as a JSON value."""
    params = {**defaults(), **values}
    missing = [p["name"] for p in template["parameters"] if p.get("required")
               and p["name"] not in params]
    assert not missing, missing

    def walk(node):
        if isinstance(node, dict):
            return {key: walk(value) for key, value in node.items()}
        if isinstance(node, list):
            return [walk(value) for value in node]
        if isinstance(node, str):
            whole = re.fullmatch(r"\$\{\{([A-Z0-9_]+)\}\}", node)
            if whole:
                return json.loads(params[whole.group(1)])
            return re.sub(r"\$\{([A-Z0-9_]+)\}", lambda m: params[m.group(1)], node)
        return node

    return walk(copy.deepcopy(template["objects"]))


def cronjob(values=None):
    (job,) = process(TEMPLATE, {**REQUIRED, **(values or {})})
    return job


def container(job):
    (only,) = job["spec"]["jobTemplate"]["spec"]["template"]["spec"]["containers"]
    return only


def test_every_parameter_is_declared_and_used():
    text = json.dumps(TEMPLATE["objects"])
    used = set(PARAMETER.findall(text))
    declared = {p["name"] for p in TEMPLATE["parameters"]}
    assert used == declared


def test_the_job_runs_the_refresh_command_with_its_budget_and_never_bootstraps():
    job = cronjob()
    command = container(job)["command"]
    assert command == ["python", "-m", "keep.identity.worker", "--once",
                       "--budget-seconds", "1800"]
    assert "--bootstrap" not in json.dumps(TEMPLATE)
    assert "args" not in container(job)


def test_schedule_overlap_retry_and_deadlines():
    job = cronjob()
    spec = job["spec"]
    assert job["kind"] == "CronJob" and job["apiVersion"] == "batch/v1"
    assert job["metadata"]["name"] == "keep-directory-refresh"
    assert spec["schedule"] == "0 2 * * *"
    assert spec["concurrencyPolicy"] == "Forbid"
    assert spec["startingDeadlineSeconds"] == 3600
    assert spec["suspend"] is False
    jobspec = spec["jobTemplate"]["spec"]
    assert jobspec["backoffLimit"] == 0
    assert jobspec["activeDeadlineSeconds"] == 2100
    assert jobspec["template"]["spec"]["restartPolicy"] == "Never"


def test_the_run_budget_ends_before_the_deadline():
    budget = int(defaults()["RUN_BUDGET_SECONDS"])
    deadline = int(defaults()["ACTIVE_DEADLINE_SECONDS"])
    assert (budget, deadline) == (1800, 2100)
    # The stop needs time for the call in flight (5 s), the publication and the summary.
    assert budget + 60 <= deadline


def test_suspend_and_schedule_are_parameters():
    job = cronjob({"SUSPEND": "true", "SCHEDULE": "30 3 * * *"})
    assert job["spec"]["suspend"] is True and job["spec"]["schedule"] == "30 3 * * *"


def test_the_pod_carries_its_label_for_network_policy():
    job = cronjob()
    pod = job["spec"]["jobTemplate"]["spec"]["template"]
    assert pod["metadata"]["labels"]["app"] == "keep-directory-refresh"
    assert job["spec"]["jobTemplate"]["metadata"]["labels"]["app"] == "keep-directory-refresh"


def test_explicit_environment_overrides_the_secret():
    """Kubernetes gives ``env`` precedence over ``envFrom`` for the same name."""
    c = container(cronjob())
    assert c["envFrom"] == [{"secretRef": {"name": "keep-identity-env"}}]
    explicit = {item["name"]: item["value"] for item in c["env"]}
    assert explicit == {"KEEP_DIRECTORY_BOOTSTRAP_QUALIFIED": "false", "KEEP_POOL_SIZE": "2"}


def test_the_security_context_matches_the_migration_job():
    (migration,) = MIGRATION["objects"]
    expected = migration["spec"]["template"]["spec"]["containers"][0]["securityContext"]
    pod = cronjob()["spec"]["jobTemplate"]["spec"]["template"]["spec"]
    assert container(cronjob())["securityContext"] == expected
    assert pod["automountServiceAccountToken"] is False
    assert pod["serviceAccountName"] == "keep-identity"
    assert {m["mountPath"]: m.get("readOnly", False) for m in container(cronjob())[
        "volumeMounts"]} == {"/tmp": False, "/etc/keep": True}


@pytest.mark.parametrize("name", sorted(REQUIRED))
def test_allocations_and_bindings_have_no_fabricated_defaults(name):
    (parameter,) = [p for p in TEMPLATE["parameters"] if p["name"] == name]
    assert parameter.get("required") is True and "value" not in parameter

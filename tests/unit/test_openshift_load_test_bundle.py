"""The isolated cluster bundle must fail closed without inventing service inputs."""

import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "deploy/openshift/load-test/render.py"
spec = importlib.util.spec_from_file_location("load_test_render", SOURCE)
bundle = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bundle)
bootstrap_spec = importlib.util.spec_from_file_location(
    "load_test_kong_bootstrap", SOURCE.parent / "bootstrap_kong.py")
kong_bootstrap = importlib.util.module_from_spec(bootstrap_spec)
bootstrap_spec.loader.exec_module(kong_bootstrap)


def config():
    return {
        "namespace": "isolated-test", "release_id": "test-1",
        "global_tenant_id": "95232c84-86f7-4835-902d-f1ec52593549",
        "search_bootstrap": {
            "physical_index": "keep-test-alerts-v1",
            "index_epoch": "e75634fd-eac7-4572-93fd-78267885597b",
            "operation_reference": "isolated-test-1",
        },
        "storage_class": "test-class", "image_pull_secret": "test-pull",
        "router_namespace_labels": {"kubernetes.io/metadata.name": "test-router"},
        "hosts": {key: f"{key}.example.test" for key in ("application", "v1", "v2")},
        "images": {key: f"registry.example.test/{key}@sha256:" + "a" * 64
                   for key in bundle.IMAGE_KEYS},
        "storage": {"postgres": "20Gi", "kong-db": "5Gi"},
        "resource_overrides": {},
    }


def rendered_items(path):
    return [item for file in path.glob("*.json") if file.name != "allocation.json"
            for item in json.loads(file.read_text())["items"]]


def test_example_cannot_be_rendered(tmp_path):
    example = json.loads((SOURCE.parent / "configuration.example.json").read_text())
    with pytest.raises(ValueError, match="placeholder"):
        bundle.render(example, tmp_path)
    assert not list(tmp_path.iterdir())


def test_global_must_be_observed_after_migration(tmp_path):
    values = config()
    values["global_tenant_id"] = None
    with pytest.raises(ValueError, match="Global"):
        bundle.render(values, tmp_path)
    bundle.render(values, tmp_path, bootstrap_only=True)
    assert (tmp_path / "migrations.params").is_file()
    assert not (tmp_path / "api.params").exists()
    assert not (tmp_path / "36-search-bootstrap.json").exists()
    assert not (tmp_path / "32-operator-proof.json").exists()
    assert "migration.template.json" in (tmp_path / "render-apps.sh").read_text()


@pytest.mark.parametrize("field", ["api", "postgres", "openldap", "adapi_mock", "kong"])
def test_mutable_image_tags_refused(field, tmp_path):
    values = config()
    values["images"][field] = "registry.example.test/thing:latest"
    with pytest.raises(ValueError, match="digest"):
        bundle.render(values, tmp_path)


def test_parameters_cover_canonical_templates_and_preserve_typed_replicas():
    for key, (filename, params) in bundle.application_parameters(config()).items():
        source = (bundle.TEMPLATES / filename).read_text()
        placeholders = set(re.findall(r"\$\{\{?([A-Z_]+)\}?\}", source))
        assert placeholders == params.keys(), (key, placeholders ^ params.keys())
        if filename != "frontend.template.yaml":
            template = json.loads(source)
            assert {p["name"] for p in template["parameters"]} == params.keys()
            for name, value in params.items():
                source = source.replace('"${{' + name + '}}"', value)
                source = source.replace("${" + name + "}", value)
            parsed = json.loads(source)
            for item in parsed["objects"]:
                if item["kind"] == "Deployment":
                    assert item["spec"]["replicas"] == 0


def test_no_fake_evidence_or_unintended_external_service(tmp_path):
    bundle.render(config(), tmp_path)
    items = rendered_items(tmp_path)
    assert all(item["metadata"]["namespace"] == "isolated-test" for item in items)
    workload_names = {item["metadata"]["name"] for item in items
                      if item["kind"] in {"StatefulSet", "Deployment"}}
    assert workload_names == {"keep-postgres", "keep-kong-db", "keep-redis",
                              "keep-openldap", "keep-adapi-mock",
                              "keep-directory-publication", "keep-kong",
                              "keep-operator-proof"}
    assert not any(item["metadata"]["name"] == "keep-clock-health" for item in items)
    for item in items:
        if item["kind"] == "Deployment" and item["metadata"]["name"] != "keep-redis":
            assert item["spec"]["replicas"] == 0
        if item["kind"] in {"StatefulSet", "Deployment", "Job"}:
            pod = item["spec"]["template"]["spec"]
            assert pod["automountServiceAccountToken"] is False
            assert "runAsUser" not in pod["securityContext"]
            assert "fsGroup" not in pod["securityContext"]
            for c in pod["containers"]:
                assert c["securityContext"]["runAsNonRoot"]
                assert c["securityContext"]["readOnlyRootFilesystem"]
                assert c["securityContext"]["capabilities"]["drop"] == ["ALL"]


def test_identity_tooling_uses_the_api_image_and_waits_for_explicit_execution(tmp_path):
    """Requirements: REV-73 (refresh only when run explicitly; no directory polling)."""
    bundle.render(config(), tmp_path)
    item = next(i for i in rendered_items(tmp_path)
                if i["kind"] == "Deployment"
                and i["metadata"]["name"] == "keep-directory-publication")
    assert item["spec"]["replicas"] == 0
    c = next(c for c in item["spec"]["template"]["spec"]["containers"]
             if c["name"] == "identity-publisher")
    assert c["command"] == ["python", "-c", "import threading; threading.Event().wait()"]
    assert {e["name"]: e["value"] for e in c["env"]}["KEEP_ENVIRONMENT"] == "development"
    assert c["envFrom"] == [{"secretRef": {"name": "keep-identity-env"}}]
    assert c["image"] == config()["images"]["api"]


def test_test_identity_is_reachable_only_from_its_consumers(tmp_path):
    """Requirements: REV-73; identity configuration 14 (LDAP bind for sign-in, ADAPI for
    the directory); no Keycloak workload, policy, Service or Route remains."""
    bundle.render(config(), tmp_path)
    items = rendered_items(tmp_path)
    policies = {i["metadata"]["name"]: i["spec"]["ingress"] for i in items
                if i["kind"] == "NetworkPolicy"}
    assert policies["keep-openldap"] == [
        bundle.rule([bundle.peer("keep-product-api")], bundle.OPENLDAP_PORT)]
    assert policies["keep-adapi-mock"] == [bundle.rule(
        [bundle.peer("keep-product-api"), bundle.peer("keep-directory-publication")],
        bundle.ADAPI_MOCK_PORT)]
    assert policies["keep-directory-publication"] == []
    postgres_peers = {p["podSelector"]["matchLabels"]["app"]
                      for p in policies["keep-postgres"][0]["from"]}
    assert "keep-directory-publication" in postgres_peers
    assert not {"keep-openldap", "keep-adapi-mock"} & postgres_peers
    assert "keycloak" not in json.dumps(policies).lower()
    assert not [i for i in items if "keycloak" in json.dumps(i).lower()]
    services = {i["metadata"]["name"]: i["spec"]["ports"] for i in items
                if i["kind"] == "Service"}
    assert services["keep-openldap"] == [
        {"name": "ldap", "port": 389, "targetPort": bundle.OPENLDAP_PORT}]
    assert services["keep-adapi-mock"] == [
        {"name": "http", "port": bundle.ADAPI_MOCK_PORT, "targetPort": bundle.ADAPI_MOCK_PORT}]
    assert not {r["spec"]["to"]["name"] for r in items if r["kind"] == "Route"} & {
        "keep-openldap", "keep-adapi-mock", "keep-directory-publication"}
    mock = next(i for i in items if i["kind"] == "Deployment"
                and i["metadata"]["name"] == "keep-adapi-mock")
    c = mock["spec"]["template"]["spec"]["containers"][0]
    assert c["image"] == config()["images"]["adapi_mock"]
    assert c["command"][:3] == ["python", "-I", "/opt/keep-adapi/local_adapi.py"]
    assert "--enable-hooks" not in c["command"]
    assert c["command"][c["command"].index("--client-id-env") + 1] == "KEEP_ADAPI_CLIENT_ID"
    ldap = next(i for i in items if i["kind"] == "Deployment"
                and i["metadata"]["name"] == "keep-openldap")
    c = ldap["spec"]["template"]["spec"]["containers"][0]
    assert c["ports"] == [{"name": "ldap", "containerPort": bundle.OPENLDAP_PORT}]
    assert {m["mountPath"]: m["readOnly"] for m in c["volumeMounts"]}["/run/openldap-seed"]


def test_hosts_are_exactly_application_v1_and_v2(tmp_path):
    """Requirements: REV-73 (no identity-provider host)."""
    values = config()
    values["hosts"]["keycloak"] = "keycloak.example.test"
    with pytest.raises(ValueError):
        bundle.render(values, tmp_path)


def test_routes_exclude_admin_and_private_ingestion_and_share_browser_origin(tmp_path):
    bundle.render(config(), tmp_path)
    routes = [i["spec"] for i in rendered_items(tmp_path) if i["kind"] == "Route"]
    assert len(routes) == 5
    assert {r["path"] for r in routes if r["host"] == "application.example.test"} == {
        "/", "/api", "/auth"}
    assert not any(r["to"]["name"] in {"keep-postgres", "keep-private-ingestion"}
                   or r["port"]["targetPort"] == "admin" for r in routes)
    private = next(i for i in rendered_items(tmp_path)
                   if i["kind"] == "NetworkPolicy"
                   and i["metadata"]["name"] == "keep-private-ingestion")
    assert private["spec"]["ingress"] == [bundle.rule([bundle.peer("keep-kong")], 8443)]


def test_database_is_tls_only_and_does_not_admit_ingestion(tmp_path):
    bundle.render(config(), tmp_path)
    items = rendered_items(tmp_path)
    policy = next(i for i in items if i["kind"] == "NetworkPolicy"
                  and i["metadata"]["name"] == "keep-postgres")
    assert "ingestion" not in json.dumps(policy)
    hba = next(i for i in items if i["kind"] == "ConfigMap")["data"]["pg_hba.conf"]
    assert "host all all 0.0.0.0/0 reject" in hba
    assert "hostssl all all 0.0.0.0/0 scram-sha-256" in hba
    states = [i for i in items if i["kind"] == "StatefulSet" and "volumeClaimTemplates" in i["spec"]]
    assert len(states) == 2
    assert all(i["spec"]["volumeClaimTemplates"][0]["spec"]["storageClassName"] == "test-class"
               for i in states)


def test_cli_migration_label_makes_db_policy_match(tmp_path):
    document = {"kind": "List", "items": [{"kind": "Job", "spec": {"template": {
        "spec": {"containers": []}}}}]}
    target = tmp_path / "job.json"
    target.write_text(json.dumps(document))
    subprocess.run([sys.executable, str(SOURCE), "--label-migration", str(target)], check=True)
    assert json.loads(target.read_text())["items"][0]["spec"]["template"]["metadata"][
        "labels"]["app"] == "keep-migration"


def test_cutover_inherits_processor_identity_but_has_no_worker_probes():
    pod = {"metadata": {"labels": {"app": "keep-processor"}}, "spec": {
        "serviceAccountName": "test-sa", "volumes": [{"name": "bindings"}],
        "containers": [{"name": "worker", "image": "test-digest",
                        "envFrom": [{"secretRef": {"name": "processor-env"}}],
                        "readinessProbe": {}, "livenessProbe": {}}]}}
    doc = {"items": [{"kind": "Deployment", "metadata": {"name": "keep-processor"},
                      "spec": {"template": pod}}]}
    result = bundle.cutover_job(doc, "test-1")
    actual = result["spec"]["template"]["spec"]
    assert actual["serviceAccountName"] == "test-sa"
    assert actual["volumes"] == pod["spec"]["volumes"]
    assert actual["restartPolicy"] == "Never"
    c = actual["containers"][0]
    assert c["envFrom"] == pod["spec"]["containers"][0]["envFrom"]
    assert c["command"][-1] == "--ingress-and-legacy-workers-stopped"
    assert "readinessProbe" not in c and "livenessProbe" not in c
    assert "readinessProbe" in pod["spec"]["containers"][0]


def test_kong_uses_real_clock_volume_and_bootstrap_waits_explicitly(tmp_path):
    bundle.render(config(), tmp_path)
    objects = rendered_items(tmp_path)
    d = next(i for i in objects if i["kind"] == "StatefulSet"
             and i["metadata"]["name"] == "keep-kong")
    pod = d["spec"]["template"]["spec"]
    assert any(v.get("configMap", {}).get("name") == "keep-clock-health"
               for v in pod["volumes"])
    assert any(v["mountPath"] == "/run/keep-clock"
               for v in pod["containers"][0]["volumeMounts"])
    j = next(i for i in objects if i["kind"] == "Job"
             and i["metadata"]["name"] == "keep-kong-bootstrap-test-1")
    assert not any(v["name"] == "clock-health" for v in j["spec"]["template"]["spec"]["volumes"])
    route_job = next(i for i in objects if i["kind"] == "Job"
                     and i["metadata"]["name"] == "keep-kong-routes-test-1")
    assert route_job["spec"]["backoffLimit"] == 0
    assert route_job["spec"]["template"]["metadata"]["labels"]["app"] == "keep-bootstrap"


def bootstrap_config():
    return json.loads((SOURCE.parent / "kong-bootstrap.example.json").read_text())


def test_bootstrap_refuses_nonempty_kong_before_any_mutation():
    attempts = []

    def request(method, path, body=None):
        attempts.append((method, path))
        return {"data": [{"id": "existing-service"}]}

    with pytest.raises(ValueError, match="not empty"):
        kong_bootstrap.bootstrap(bootstrap_config(), request, "s" * 40,
                                 "-----BEGIN CERTIFICATE-----\ntest")
    assert attempts == [("GET", "/services?size=1")]


def test_bootstrap_preserves_mtls_and_does_not_invent_operator_or_grafana_evidence():
    attempts = []

    def request(method, path, body=None):
        attempts.append((method, path, body))
        if method == "GET":
            return {"member_id": "observed-node"} if path == "/keep-policy-node" else {"data": []}
        return {"id": "actual-created-resource"}

    result = kong_bootstrap.bootstrap(bootstrap_config(), request, "s" * 40,
                                      "-----BEGIN CERTIFICATE-----\ntest")
    service = next(b for m, p, b in attempts if p == "/services/keep-test-v2")
    assert service["tls_verify"] is True and service["protocol"] == "https"
    assert service["host"] == "keep-private-ingestion"
    plugins = [b for m, p, b in attempts if m == "POST"]
    policies = [b["config"] for b in plugins if b["name"] == "keep-policy"]
    assert len(policies) == 4
    assert all(p["clock_health_file"] == "/run/keep-clock/health.json"
               and p["unqualified_local_clock"] is False for p in policies)
    assert result["qualification"] == "not established"
    assert result["grafana_modified"] is False
    assert not any(m != "GET" and ("consumers" in p or "policies" in p)
                   for m, p, b in attempts)


def test_kong_bootstrap_never_follows_redirects_with_credentials():
    handler = kong_bootstrap.NoRedirects()
    assert handler.redirect_request(None, None, 302, "redirect", {}, "https://other.test") is None


def test_proof_is_private_and_uses_api_image_with_separate_credentials(tmp_path):
    bundle.render(config(), tmp_path)
    objects = rendered_items(tmp_path)
    d = next(i for i in objects if i["kind"] == "Deployment"
             and i["metadata"]["name"] == "keep-operator-proof")
    assert d["spec"]["replicas"] == 0
    c = d["spec"]["template"]["spec"]["containers"][0]
    assert c["image"] == config()["images"]["api"]
    assert c["command"] == ["python", "-m", "keep.operators.proof"]
    assert c["envFrom"] == [{"secretRef": {"name": "keep-operator-proof-env"}}]
    assert not any(i["kind"] == "Route" and i["spec"]["to"]["name"] == "keep-operator-proof"
                   for i in objects)
    policy = next(i for i in objects if i["kind"] == "NetworkPolicy"
                  and i["metadata"]["name"] == "keep-operator-proof")
    assert policy["spec"]["ingress"] == [bundle.rule([bundle.peer("keep-product-api")], 8443)]


def test_kong_proof_addresses_one_ordinal_and_replacements_are_explicit(tmp_path):
    bundle.render(config(), tmp_path)
    objects = rendered_items(tmp_path)
    kong = next(i for i in objects if i["kind"] == "StatefulSet"
                and i["metadata"]["name"] == "keep-kong")
    assert kong["spec"]["serviceName"] == "keep-kong-members"
    assert kong["spec"]["replicas"] == 0
    assert kong["spec"]["updateStrategy"] == {"type": "OnDelete"}
    service = next(i for i in objects if i["kind"] == "Service"
                   and i["metadata"]["name"] == "keep-kong-members")
    assert service["spec"]["clusterIP"] == "None"
    assert service["spec"]["selector"] == kong["spec"]["selector"]["matchLabels"]
    assert bootstrap_config()["admin_url"] == "https://keep-kong-0.keep-kong-members:8444"
    policy = next(i for i in objects if i["kind"] == "NetworkPolicy"
                  and i["metadata"]["name"] == "keep-kong")
    assert bundle.peer("keep-operator-proof") in policy["spec"]["ingress"][0]["from"]


def test_search_bootstrap_has_explicit_retry_identity_and_no_runtime_worker(tmp_path):
    values = config()
    bundle.render(values, tmp_path)
    job = json.loads((tmp_path / "36-search-bootstrap.json").read_text())["items"][0]
    assert job["spec"]["backoffLimit"] == 0
    assert job["spec"]["activeDeadlineSeconds"] == 600
    c = job["spec"]["template"]["spec"]["containers"][0]
    assert c["image"] == values["images"]["indexer"]
    assert c["command"][-1] == "--ingestion-and-writers-stopped"
    for key in ("physical_index", "index_epoch", "operation_reference"):
        assert values["search_bootstrap"][key] in c["command"]
    assert c["envFrom"] == [{"secretRef": {"name": "keep-indexer-env"}}]
    assert {e["name"]: e["value"] for e in c["env"]}["KEEP_ROLE"] == "indexer"
    assert {e["name"]: e["value"] for e in c["env"]}["KEEP_DATABASE_CLOCK_HEALTH_FILE"] == \
        "/run/keep-clock/pg-health.json"
    assert "readinessProbe" not in c and "livenessProbe" not in c
    assert job["spec"]["template"]["metadata"]["labels"]["app"] == "keep-search-bootstrap"


@pytest.mark.parametrize("field,value", [
    ("index_epoch", "not-a-uuid"), ("physical_index", "*"),
    ("physical_index", "INDEX"), ("operation_reference", "bad\nreference"),
])
def test_search_bootstrap_rejects_ambiguous_identity(field, value, tmp_path):
    values = config()
    values["search_bootstrap"][field] = value
    with pytest.raises(ValueError):
        bundle.render(values, tmp_path)
    assert not list(tmp_path.iterdir())

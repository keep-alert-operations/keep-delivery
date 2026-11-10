"""Render an isolated test deployment; no secrets, cluster calls or readiness claims.

Canonical application Templates stay authoritative. JSON files are directly
applicable; render-apps.sh uses the user's oc to process the canonical Templates.
"""

import argparse
import copy
import json
from pathlib import Path
import re
import shlex
from uuid import UUID


HERE = Path(__file__).resolve().parent
TEMPLATES = HERE.parent
SA = "keep-load-test"
SECURITY = {
    "allowPrivilegeEscalation": False,
    "readOnlyRootFilesystem": True,
    "runAsNonRoot": True,
    "seccompProfile": {"type": "RuntimeDefault"},
    "capabilities": {"drop": ["ALL"]},
}
# Assistant-selected starting allocations, NOT qualified capacity profiles.
STARTING_RESOURCES = {
    "postgres": ("1", "2", "2Gi", "4Gi"),
    "keycloak-db": ("250m", "1", "256Mi", "1Gi"),
    "kong-db": ("250m", "1", "256Mi", "1Gi"),
    "redis": ("100m", "500m", "128Mi", "512Mi"),
    "keycloak": ("500m", "2", "768Mi", "2Gi"),
    "kong": ("250m", "1", "256Mi", "1Gi"),
    "api": ("500m", "2", "512Mi", "2Gi"),
    "ingestion": ("250m", "1", "256Mi", "1Gi"),
    "private-ingestion": ("250m", "1", "256Mi", "1Gi"),
    "processor": ("500m", "2", "512Mi", "2Gi"),
    "indexer": ("500m", "2", "512Mi", "2Gi"),
    "correlation": ("1", "2", "1Gi", "2Gi"),
    "frontend": ("250m", "1", "256Mi", "1Gi"),
    "migrations": ("250m", "1", "256Mi", "1Gi"),
    "search-bootstrap": ("250m", "1", "256Mi", "1Gi"),
    "operator-proof": ("100m", "500m", "128Mi", "512Mi"),
}
IMAGE_KEYS = {
    "api", "ingestion", "processor", "indexer", "correlation", "frontend", "migrations",
    "postgres", "keycloak_postgres", "kong_postgres", "redis", "keycloak", "kong",
}


def validate(config, bootstrap_only=False):
    if "REQUIRED" in json.dumps(config) or "<" in json.dumps(config):
        raise ValueError("Replace every REQUIRED placeholder in the private configuration")
    for key in ("namespace", "release_id", "image_pull_secret"):
        if not re.fullmatch(r"[a-z0-9]([-a-z0-9]*[a-z0-9])?", config[key]):
            raise ValueError(f"Invalid Kubernetes name: {key}")
    if len(config["release_id"]) > 35:
        raise ValueError("release_id must fit generated Job names (35 characters maximum)")
    if config["global_tenant_id"] is None:
        if not bootstrap_only:
            raise ValueError("Read Global's UUID after migration, or use --bootstrap-only")
    else:
        UUID(config["global_tenant_id"])
    if not config["storage_class"] or not config["router_namespace_labels"]:
        raise ValueError("Supply storage class and the actual router namespace selector")
    if set(config["images"]) != IMAGE_KEYS:
        raise ValueError("Supply exactly the documented image keys")
    for key, image in config["images"].items():
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._:/-]*@sha256:[a-f0-9]{64}", image):
            raise ValueError(f"Supply an immutable image digest for {key}")
    for name in ("application", "keycloak", "v1", "v2"):
        host = config["hosts"][name]
        if not re.fullmatch(r"[a-z0-9]([a-z0-9.-]*[a-z0-9])?", host) or "." not in host:
            raise ValueError(f"Supply the DNS hostname, without URL/path: {name}")
    if len(set(config["hosts"].values())) != 4:
        raise ValueError("Use four distinct route hostnames")
    search = config["search_bootstrap"]
    UUID(search["index_epoch"])
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,199}", search["physical_index"]):
        raise ValueError("Supply an exact lowercase search physical index")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,199}", search["operation_reference"]):
        raise ValueError("Supply a nonsecret search bootstrap operation reference")
    for key in ("postgres", "keycloak-db", "kong-db"):
        if not re.fullmatch(r"[1-9][0-9]*(Gi|Ti)", config["storage"][key]):
            raise ValueError(f"Supply a positive PVC capacity in Gi or Ti for {key}")
    for key, values in config.get("resource_overrides", {}).items():
        if key not in STARTING_RESOURCES or len(values) != 4:
            raise ValueError("Resource override: known service and four quantities required")
        for value in values:
            if not isinstance(value, str) or not re.fullmatch(r"[0-9]+(?:\.[0-9]+)?[a-zA-Z]*", value):
                raise ValueError("Resource override quantities must be Kubernetes quantity strings")


def metadata(name):
    return {"name": name, "labels": {"app.kubernetes.io/part-of": "keep"}}


def env(values):
    return [{"name": key, "value": value} for key, value in values.items()]


def resources(config, key):
    cr, cl, mr, ml = config.get("resource_overrides", {}).get(key, STARTING_RESOURCES[key])
    return {
        "requests": {"cpu": cr, "memory": mr, "ephemeral-storage": "128Mi"},
        "limits": {"cpu": cl, "memory": ml, "ephemeral-storage": "1Gi"},
    }


def container(config, key, name=None, resource_key=None):
    return {
        "name": name or key,
        "image": config["images"][key],
        "imagePullPolicy": "IfNotPresent",
        "securityContext": copy.deepcopy(SECURITY),
        "resources": resources(config, resource_key or key),
    }


def mount(name, path, readonly=False):
    return {"name": name, "mountPath": path, "readOnly": readonly}


def secret_volume(name, secret):
    return {"name": name, "secret": {"secretName": secret, "defaultMode": 288}}


def tmp_volume(name="tmp", size="256Mi"):
    return {"name": name, "emptyDir": {"sizeLimit": size}}


def pod(config, name, containers, volumes):
    return {
        "metadata": {"labels": {"app": name, "app.kubernetes.io/part-of": "keep"}},
        "spec": {
            "serviceAccountName": SA,
            "automountServiceAccountToken": False,
            "imagePullSecrets": [{"name": config["image_pull_secret"]}],
            "terminationGracePeriodSeconds": 60,
            "securityContext": {"runAsNonRoot": True, "fsGroupChangePolicy": "OnRootMismatch"},
            "containers": containers,
            "volumes": volumes,
        },
    }


def deployment(config, name, containers, volumes, replicas=0):
    return {
        "apiVersion": "apps/v1", "kind": "Deployment", "metadata": metadata(name),
        "spec": {"replicas": replicas, "selector": {"matchLabels": {"app": name}},
                 "template": pod(config, name, containers, volumes)},
    }


def service(name, ports):
    return {
        "apiVersion": "v1", "kind": "Service", "metadata": metadata(name),
        "spec": {"selector": {"app": name}, "ports": [
            {"name": key, "port": port, "targetPort": port} for key, port in ports.items()
        ]},
    }


def ingress_policy(name, allowed):
    return {
        "apiVersion": "networking.k8s.io/v1", "kind": "NetworkPolicy",
        "metadata": metadata(name),
        "spec": {"podSelector": {"matchLabels": {"app": name}},
                 "policyTypes": ["Ingress"], "ingress": allowed},
    }


def peer(app):
    return {"podSelector": {"matchLabels": {"app": app}}}


def rule(peers, port):
    return {"from": peers, "ports": [{"protocol": "TCP", "port": port}]}


def foundation(config):
    router = {"namespaceSelector": {"matchLabels": config["router_namespace_labels"]}}
    sa = {"apiVersion": "v1", "kind": "ServiceAccount", "metadata": metadata(SA),
          "automountServiceAccountToken": False,
          "imagePullSecrets": [{"name": config["image_pull_secret"]}]}
    policies = {
        "keep-postgres": [rule([peer(name) for name in (
            "keep-product-api", "keep-processor", "keep-indexer", "keep-correlation",
            "keep-bootstrap", "keep-migration", "keep-directory-publication", "keep-keycloak",
            "keep-search-bootstrap", "keep-operator-proof",
        )], 5432)],
        "keep-keycloak-db": [rule([peer("keep-keycloak")], 5432)],
        "keep-kong-db": [rule([peer("keep-kong"), peer("keep-kong-migration")], 5432)],
        "keep-redis": [rule([peer("keep-product-api")], 6379)],
        "keep-keycloak": [rule([router, peer("keep-product-api"),
                                   peer("keep-directory-publication")], 8443)],
        "keep-kong": [rule([router, peer("keep-operator-proof")], 8443),
                         rule([peer("keep-product-api"), peer("keep-bootstrap")], 8444)],
        "keep-operator-proof": [rule([peer("keep-product-api")], 8443)],
        "keep-private-ingestion": [rule([peer("keep-kong")], 8443)],
        "keep-ingestion-api": [rule([router], 8000)],
        "keep-product-api": [rule([router, peer("keep-frontend")], 8000)],
        "keep-frontend": [rule([router], 3000)],
        "keep-processor": [], "keep-indexer": [], "keep-correlation": [],
    }
    return [sa] + [ingress_policy(name, rules) for name, rules in policies.items()]


def postgres(config, key, image):
    name = f"keep-{key}"
    db = {"postgres": "keep", "keycloak-db": "keycloak", "kong-db": "kong"}[key]
    user = {"postgres": "keep_migrator", "keycloak-db": "keycloak", "kong-db": "kong"}[key]
    c = container(config, image, "postgres", resource_key=key)
    c["env"] = env({
        "PGDATA": "/var/lib/keep-postgres/data", "POSTGRES_DB": db, "POSTGRES_USER": user,
        "POSTGRES_PASSWORD_FILE": "/run/postgres-credentials/password",
        "POSTGRES_INITDB_ARGS": "--auth-host=scram-sha-256 --auth-local=trust",
    })
    c["ports"] = [{"name": "postgres", "containerPort": 5432}]
    c["volumeMounts"] = [mount("data", "/var/lib/keep-postgres"), mount("tmp", "/tmp"),
                          mount("socket", "/var/run/postgresql"),
                          mount("tls", "/run/postgres-tls", True),
                          mount("credentials", "/run/postgres-credentials", True),
                          mount("config", "/etc/postgres", True)]
    # initdb's temporary server is socket-only; require the final TCP listener.
    probe = {"exec": {"command": ["pg_isready", "-h", "127.0.0.1", "-U", user, "-d", db]},
             "timeoutSeconds": 5, "periodSeconds": 10, "failureThreshold": 3}
    c["readinessProbe"] = probe
    c["startupProbe"] = {**copy.deepcopy(probe), "failureThreshold": 60}
    template = pod(config, name, [c], [tmp_volume(), tmp_volume("socket", "8Mi"),
        secret_volume("tls", f"{name}-tls"),
        secret_volume("credentials", f"{name}-credentials"),
        {"name": "config", "configMap": {"name": "keep-postgres-hba"}}])
    stateful = {
        "apiVersion": "apps/v1", "kind": "StatefulSet", "metadata": metadata(name),
        "spec": {"serviceName": name, "replicas": 1,
                 "selector": {"matchLabels": {"app": name}}, "template": template,
                 "volumeClaimTemplates": [{"metadata": {"name": "data"}, "spec": {
                     "accessModes": ["ReadWriteOnce"], "storageClassName": config["storage_class"],
                     "resources": {"requests": {"storage": config["storage"][key]}}}}]},
    }
    return [stateful, service(name, {"postgres": 5432})]


def redis(config):
    c = container(config, "redis")
    c["ports"] = [{"name": "tls", "containerPort": 6379}]
    c["volumeMounts"] = [mount("config", "/etc/redis", True),
                          mount("tls", "/run/redis-tls", True), mount("data", "/data")]
    # TCP only checks the listener; verify authenticated Pub/Sub separately before load.
    c["readinessProbe"] = {"tcpSocket": {"port": "tls"}, "periodSeconds": 10}
    d = deployment(config, "keep-redis", [c], [tmp_volume("data", "128Mi"),
        secret_volume("config", "keep-redis-config"),
        secret_volume("tls", "keep-redis-tls")], replicas=1)
    return [d, service("keep-redis", {"tls": 6379})]


def keycloak(config):
    c = container(config, "keycloak")
    c["args"] = ["start", "--optimized", "--import-realm"]
    c["envFrom"] = [{"secretRef": {"name": "keep-keycloak-env"}}]
    c["env"] = env({
        "KC_HOSTNAME": f"https://{config['hosts']['keycloak']}", "KC_HTTP_ENABLED": "false",
        "KC_HTTPS_CERTIFICATE_FILE": "/run/keycloak-tls/tls.crt",
        "KC_HTTPS_CERTIFICATE_KEY_FILE": "/run/keycloak-tls/tls.key",
        "KC_DB": "postgres",
        "KC_DB_URL": "jdbc:postgresql://keep-keycloak-db:5432/keycloak"
                     "?sslmode=verify-full&sslrootcert=/run/keycloak-trust/postgres-ca.crt",
        "KC_DB_USERNAME": "keycloak", "KC_TRUSTSTORE_PATHS": "/run/keycloak-trust",
        "JAVA_OPTS_KC_HEAP": "-Xms256m -Xmx1024m",
    })
    c["ports"] = [{"name": "https", "containerPort": 8443},
                  {"name": "management", "containerPort": 9000}]
    # Management probes use TLS as inherited from the HTTPS listener.
    c["readinessProbe"] = {"httpGet": {"path": "/health/ready", "port": 9000,
                                      "scheme": "HTTPS"}, "periodSeconds": 10}
    c["livenessProbe"] = {"httpGet": {"path": "/health/live", "port": 9000,
                                     "scheme": "HTTPS"}, "periodSeconds": 10}
    c["startupProbe"] = {**copy.deepcopy(c["livenessProbe"]), "failureThreshold": 60}
    c["volumeMounts"] = [mount("tmp", "/tmp"), mount("data", "/opt/keycloak/data"),
        mount("realm", "/opt/keycloak/data/import", True),
        mount("tls", "/run/keycloak-tls", True), mount("trust", "/run/keycloak-trust", True)]
    publisher = container(config, "api", "identity-publisher")
    publisher["command"] = ["python", "-c",
                            "import threading; threading.Event().wait()"]
    publisher["envFrom"] = [{"secretRef": {"name": "keep-identity-env"}}]
    publisher["env"] = env({"KEEP_ENVIRONMENT": "development"})
    publisher["resources"] = {
        "requests": {"cpu": "100m", "memory": "128Mi", "ephemeral-storage": "128Mi"},
        "limits": {"cpu": "500m", "memory": "512Mi", "ephemeral-storage": "256Mi"},
    }
    publisher["volumeMounts"] = [mount("identity", "/etc/keep", True), mount("tmp", "/tmp")]
    d = deployment(config, "keep-keycloak", [c, publisher], [tmp_volume(), tmp_volume("data"),
        secret_volume("realm", "keep-keycloak-realm"),
        secret_volume("tls", "keep-keycloak-tls"),
        secret_volume("trust", "keep-keycloak-trust"),
        secret_volume("identity", "keep-identity-bindings")])
    return [d, service("keep-keycloak", {"https": 8443})]


def kong(config):
    c = container(config, "kong")
    c["envFrom"] = [{"secretRef": {"name": "keep-kong-env"}}]
    c["env"] = env({
        "KONG_DATABASE": "postgres", "KONG_PG_HOST": "keep-kong-db",
        "KONG_PG_DATABASE": "kong", "KONG_PG_USER": "kong", "KONG_PG_SSL": "on",
        "KONG_PG_SSL_VERIFY": "on", "KONG_PREFIX": "/tmp/kong",
        "KONG_PLUGINS": "bundled,keep-policy", "KONG_NGINX_WORKER_PROCESSES": "1",
        "KONG_PROXY_LISTEN": "0.0.0.0:8443 ssl", "KONG_ADMIN_LISTEN": "0.0.0.0:8444 ssl",
        "KONG_ADMIN_GUI_LISTEN": "off", "KONG_PROXY_ACCESS_LOG": "off",
        "KONG_ADMIN_ACCESS_LOG": "off", "KONG_PROXY_ERROR_LOG": "/dev/stderr",
        "KONG_ADMIN_ERROR_LOG": "/dev/stderr", "KONG_LOG_LEVEL": "warn",
        "KONG_SSL_CERT": "/run/kong-tls/server.crt", "KONG_SSL_CERT_KEY": "/run/kong-tls/server.key",
        "KONG_ADMIN_SSL_CERT": "/run/kong-tls/server.crt",
        "KONG_ADMIN_SSL_CERT_KEY": "/run/kong-tls/server.key",
        "KONG_LUA_SSL_TRUSTED_CERTIFICATE": "/run/kong-tls/ca-bundle.crt",
        "KONG_CLIENT_SSL": "on", "KONG_CLIENT_SSL_CERT": "/run/kong-tls/kong.crt",
        "KONG_CLIENT_SSL_CERT_KEY": "/run/kong-tls/kong.key",
    })
    c["ports"] = [{"name": "proxy", "containerPort": 8443},
                  {"name": "admin", "containerPort": 8444}]
    c["volumeMounts"] = [mount("tmp", "/tmp"), mount("tls", "/run/kong-tls", True),
                          mount("clock-health", "/run/keep-clock", True)]
    probe = {"exec": {"command": ["kong", "health", "-p", "/tmp/kong"]},
             "timeoutSeconds": 5, "periodSeconds": 10}
    c["readinessProbe"] = probe
    c["startupProbe"] = {**copy.deepcopy(probe), "failureThreshold": 60}
    volumes = [tmp_volume(), secret_volume("tls", "keep-kong-tls"),
               {"name": "clock-health", "configMap": {"name": "keep-clock-health"}}]
    # An ordinal DNS name addresses exactly one serving member. Do not use a
    # load-balanced proxy Service as a substitute for all-member proof.
    d = deployment(config, "keep-kong", [c], volumes)
    d["kind"] = "StatefulSet"
    d["spec"]["serviceName"] = "keep-kong-members"
    d["spec"]["updateStrategy"] = {"type": "OnDelete"}
    members = service("keep-kong-members", {"proxy": 8443, "admin": 8444})
    members["spec"]["selector"] = {"app": "keep-kong"}
    members["spec"]["clusterIP"] = "None"
    migration = copy.deepcopy(c)
    for key in ("ports", "readinessProbe", "startupProbe"):
        migration.pop(key)
    migration["command"] = ["kong", "migrations", "bootstrap"]
    migration["volumeMounts"] = [m for m in migration["volumeMounts"]
                                 if m["name"] != "clock-health"]
    migration_volumes = [copy.deepcopy(v) for v in volumes if v["name"] != "clock-health"]
    template = pod(config, "keep-kong-migration", [migration], migration_volumes)
    template["spec"]["restartPolicy"] = "Never"
    j = {"apiVersion": "batch/v1", "kind": "Job",
         "metadata": metadata(f"keep-kong-bootstrap-{config['release_id']}"),
         "spec": {"backoffLimit": 0, "template": template}}
    return [d, service("keep-kong", {"proxy": 8443, "admin": 8444}), members], j


def operator_proof(config):
    c = container(config, "api", "operator-proof", resource_key="operator-proof")
    c["command"] = ["python", "-m", "keep.operators.proof"]
    c["envFrom"] = [{"secretRef": {"name": "keep-operator-proof-env"}}]
    c["env"] = env({
        "KEEP_PROOF_CONFIG_FILE": "/etc/keep/configuration.json",
        "KEEP_OPERATOR_BINDING": "/etc/keep/operator-binding.json",
        "KEEP_PROOF_TOKEN_FILE": "/etc/keep/probe-token",
        "KEEP_PROOF_GATEWAY_TOKEN_FILE": "/etc/keep/gateway-probe-token",
        "KEEP_PROOF_CERT_FILE": "/run/proof-tls/tls.crt",
        "KEEP_PROOF_KEY_FILE": "/run/proof-tls/tls.key",
    })
    c["ports"] = [{"name": "https", "containerPort": 8443}]
    c["volumeMounts"] = [mount("tmp", "/tmp"), mount("bindings", "/etc/keep", True),
                          mount("tls", "/run/proof-tls", True)]
    c["readinessProbe"] = {"httpGet": {"path": "/readyz", "port": 8443, "scheme": "HTTPS"},
                           "timeoutSeconds": 5, "periodSeconds": 10}
    c["startupProbe"] = {**copy.deepcopy(c["readinessProbe"]), "failureThreshold": 30}
    volumes = [tmp_volume(), secret_volume("bindings", "keep-operator-proof-bindings"),
               secret_volume("tls", "keep-operator-proof-tls")]
    return [deployment(config, "keep-operator-proof", [c], volumes),
            service("keep-operator-proof", {"https": 8443})]


def search_bootstrap_job(config):
    search = config["search_bootstrap"]
    c = container(config, "indexer", "search-bootstrap", resource_key="search-bootstrap")
    c["command"] = ["python", "-m", "keep.search.bootstrap", "--binding",
                    "/etc/keep/search-binding.json", "--physical-index", search["physical_index"],
                    "--index-epoch", search["index_epoch"], "--operation-reference",
                    search["operation_reference"], "--ingestion-and-writers-stopped"]
    c["envFrom"] = [{"secretRef": {"name": "keep-indexer-env"}}]
    c["env"] = env({"KEEP_GLOBAL_TENANT_ID": config["global_tenant_id"],
                    "KEEP_ROLE": "indexer",
                    "KEEP_CLOCK_HEALTH_FILE": "/run/keep-clock/health.json",
                    "KEEP_DATABASE_CLOCK_HEALTH_FILE": "/run/keep-clock/pg-health.json"})
    c["volumeMounts"] = [mount("tmp", "/tmp"), mount("bindings", "/etc/keep", True),
                          mount("clock-health", "/run/keep-clock", True)]
    template = pod(config, "keep-search-bootstrap", [c], [tmp_volume(),
        secret_volume("bindings", "keep-indexer-bindings"),
        {"name": "clock-health", "configMap": {"name": "keep-clock-health"}}])
    template["spec"]["restartPolicy"] = "Never"
    return {"apiVersion": "batch/v1", "kind": "Job",
            "metadata": metadata(f"keep-search-bootstrap-{config['release_id']}"),
            "spec": {"backoffLimit": 0, "activeDeadlineSeconds": 600, "template": template}}


def kong_routes_job(config):
    c = container(config, "api", "configure-kong", resource_key="migrations")
    c["command"] = ["python", "/opt/keep-bootstrap/bootstrap_kong.py", "--config",
                    "/etc/keep/configuration.json", "--fresh-dedicated-test-kong"]
    c["volumeMounts"] = [mount("tmp", "/tmp"), mount("code", "/opt/keep-bootstrap", True),
                          mount("bindings", "/etc/keep", True)]
    template = pod(config, "keep-bootstrap", [c], [tmp_volume(),
        {"name": "code", "configMap": {"name": "keep-kong-bootstrap-code"}},
        secret_volume("bindings", "keep-kong-bootstrap-bindings")])
    template["spec"]["restartPolicy"] = "Never"
    return {"apiVersion": "batch/v1", "kind": "Job",
            "metadata": metadata(f"keep-kong-routes-{config['release_id']}"),
            "spec": {"backoffLimit": 0, "activeDeadlineSeconds": 600, "template": template}}


def route(name, host, target, port, path=None, passthrough=False):
    obj = {"apiVersion": "route.openshift.io/v1", "kind": "Route",
           "metadata": {**metadata(name), "annotations": {
               "haproxy.router.openshift.io/timeout": "110s"}},
           "spec": {"host": host, "to": {"kind": "Service", "name": target},
                    "port": {"targetPort": port}, "tls": {
                        "termination": "passthrough" if passthrough else "edge",
                        "insecureEdgeTerminationPolicy": "Redirect"}}}
    if path:
        obj["spec"]["path"] = path
    return obj


def routes(config):
    h = config["hosts"]
    return [
        route("keep-ui", h["application"], "keep-frontend", "http", "/"),
        route("keep-api", h["application"], "keep-product-api", "http", "/api"),
        route("keep-auth", h["application"], "keep-product-api", "http", "/auth"),
        route("keep-v1", h["v1"], "keep-ingestion-api", "http", "/"),
        route("keep-v2", h["v2"], "keep-kong", "proxy", passthrough=True),
        route("keep-keycloak", h["keycloak"], "keep-keycloak", "https", passthrough=True),
    ]


def application_parameters(config, bootstrap_only=False):
    result = {}
    definitions = {
        "api": ("application.template.yaml", "PRODUCT_API_IMAGE", "api", "keep-api"),
        "ingestion": ("ingestion-api.template.json", "INGESTION_IMAGE", "ingestion", "keep-ingestion"),
        "private-ingestion": ("private-ingestion.template.json", "INGESTION_IMAGE", "ingestion", "keep-private-ingestion"),
        "processor": ("processor.template.json", "PROCESSOR_IMAGE", "processor", "keep-processor"),
        "indexer": ("indexer.template.json", "INDEXER_IMAGE", "indexer", "keep-indexer"),
        "correlation": ("correlation.template.json", "CORRELATION_IMAGE", "correlation", "keep-correlation"),
        "migrations": ("migration.template.json", "MIGRATIONS_IMAGE", "migrations", "keep-migration"),
        "frontend": ("frontend.template.yaml", None, "frontend", "keep-frontend"),
    }
    for key, (filename, image_param, image_key, secret_prefix) in definitions.items():
        if bootstrap_only and key != "migrations":
            continue
        r = resources(config, key)
        p = {"SERVICE_ACCOUNT": SA, "CPU_REQUEST": r["requests"]["cpu"],
             "CPU_LIMIT": r["limits"]["cpu"], "MEMORY_REQUEST": r["requests"]["memory"],
             "MEMORY_LIMIT": r["limits"]["memory"], "EPHEMERAL_STORAGE_REQUEST": "128Mi",
             "EPHEMERAL_STORAGE_LIMIT": "1Gi", "TMP_SIZE_LIMIT": "256Mi"}
        if image_param:
            p[image_param] = config["images"][image_key]
        if key != "migrations":
            p["REPLICAS"] = "0"
        if key not in {"migrations", "frontend"}:
            p["CLOCK_HEALTH_CONFIGMAP"] = "keep-clock-health"
            if key != "api":
                p["GLOBAL_TENANT_ID"] = config["global_tenant_id"]
        if key in {"processor", "indexer", "correlation"}:
            p["HEARTBEAT_MAX_AGE_SECONDS"] = "120"
        if key in {"ingestion", "private-ingestion"}:
            p.update(INGRESS_CONFIG_SECRET=f"{secret_prefix}-env",
                     INGRESS_BINDING_SECRET=f"{secret_prefix}-bindings")
        elif key == "migrations":
            p.update(MIGRATION_SECRET="keep-migration-env", RELEASE_ID=config["release_id"],
                     BINDING_SECRET="keep-migration-bindings")
        elif key == "frontend":
            repo, digest = config["images"]["frontend"].split("@")
            p.update(FRONTEND_IMAGE_REPOSITORY=repo, FRONTEND_IMAGE_DIGEST=digest,
                     API_INTERNAL_URL="http://keep-product-api:8000", CACHE_SIZE_LIMIT="128Mi")
        else:
            p.update(CONFIG_SECRET=f"{secret_prefix}-env", BINDING_SECRET=f"{secret_prefix}-bindings")
        result[key] = (filename, p)
    return result


def write_list(out, filename, items, namespace):
    for item in items:
        item["metadata"]["namespace"] = namespace
    (out / filename).write_text(json.dumps({"apiVersion": "v1", "kind": "List", "items": items},
                                         indent=2) + "\n", encoding="utf-8")


def cutover_job(processor_document, release_id):
    """Reuse the canonical processor pod's identity, bindings and clock mounts."""
    workload = next(item for item in processor_document["items"]
                    if item["kind"] == "Deployment"
                    and item["metadata"]["name"] == "keep-processor")
    template = copy.deepcopy(workload["spec"]["template"])
    template["spec"]["restartPolicy"] = "Never"
    for c in template["spec"]["containers"]:
        c["command"] = ["python", "-m", "keep.kafka_cutover",
                        "--ingress-and-legacy-workers-stopped"]
        for field in ("readinessProbe", "livenessProbe", "startupProbe"):
            c.pop(field, None)
    return {"apiVersion": "batch/v1", "kind": "Job",
            "metadata": metadata(f"keep-kafka-cutover-{release_id}"),
            "spec": {"backoffLimit": 0, "activeDeadlineSeconds": 600, "template": template}}


def render(config, output, bootstrap_only=False):
    validate(config, bootstrap_only=bootstrap_only)
    output.mkdir(parents=True, exist_ok=True)
    namespace = config["namespace"]
    write_list(output, "00-foundation.json", foundation(config), namespace)
    hba = {"apiVersion": "v1", "kind": "ConfigMap", "metadata": metadata("keep-postgres-hba"),
           "data": {"pg_hba.conf": "local all all trust\n"
                    "hostssl all all 0.0.0.0/0 scram-sha-256\n"
                    "hostssl all all ::/0 scram-sha-256\n"
                    "host all all 0.0.0.0/0 reject\nhost all all ::/0 reject\n"}}
    infra = [hba]
    for key, image in (("postgres", "postgres"), ("keycloak-db", "keycloak_postgres"),
                       ("kong-db", "kong_postgres")):
        infra.extend(postgres(config, key, image))
    infra.extend(redis(config))
    write_list(output, "10-data-services.json", infra, namespace)
    kong_objects, kong_job = kong(config)
    write_list(output, "21-kong-bootstrap.json", [kong_job], namespace)
    write_list(output, "30-keycloak-kong.json", keycloak(config) + kong_objects, namespace)
    write_list(output, "31-kong-routes.json", [kong_routes_job(config)], namespace)
    if not bootstrap_only:
        write_list(output, "32-operator-proof.json", operator_proof(config), namespace)
        write_list(output, "36-search-bootstrap.json", [search_bootstrap_job(config)], namespace)
    write_list(output, "50-routes.json", routes(config), namespace)
    commands = ["#!/usr/bin/env bash", "set -euo pipefail",
                '# Run with bash; requires oc, Python 3 and the source checkout at this path.',
                'OUT="$(cd -- "$(dirname -- "$0")" && pwd)"']
    for key, (template, params) in application_parameters(config, bootstrap_only).items():
        (output / f"{key}.params").write_text(
            "\n".join(f"{k}={v}" for k, v in params.items()) + "\n", encoding="utf-8")
        target = "20-keep-migration.json" if key == "migrations" else f"40-{key}.json"
        commands.append(f'oc process --local -o json -f {shlex.quote(str(TEMPLATES / template))} '
                        f'--param-file="$OUT/{key}.params" > "$OUT/{target}"')
    # The migration template has no app label. Add one so its dedicated DB policy applies.
    commands.extend([
        f'python3 {shlex.quote(str(HERE / "render.py"))} --label-migration '
        '"$OUT/20-keep-migration.json"',
    ])
    if not bootstrap_only:
        commands.append(f'python3 {shlex.quote(str(HERE / "render.py"))} '
                        '--cutover-from "$OUT/40-processor.json" '
                        '--cutover-output "$OUT/35-kafka-cutover.json" '
                        f'--release-id {shlex.quote(config["release_id"])}')
    commands.append('echo "Rendered only. Review, server-dry-run, then apply in documented stages."')
    (output / "render-apps.sh").write_text("\n".join(commands) + "\n", encoding="utf-8")
    (output / "allocation.json").write_text(json.dumps({
        "classification": "assistant-selected starting allocation; not qualified",
        "resources": {key: resources(config, key) for key in STARTING_RESOURCES},
        "storage": config["storage"], "application_initial_replicas": 0,
    }, indent=2) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--label-migration", type=Path)
    parser.add_argument("--bootstrap-only", action="store_true")
    parser.add_argument("--cutover-from", type=Path)
    parser.add_argument("--cutover-output", type=Path)
    parser.add_argument("--release-id")
    args = parser.parse_args()
    if args.label_migration:
        document = json.loads(args.label_migration.read_text(encoding="utf-8"))
        for item in document["items"]:
            if item["kind"] == "Job":
                item["spec"]["template"].setdefault("metadata", {}).setdefault("labels", {})[
                    "app"] = "keep-migration"
        args.label_migration.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    elif args.cutover_from:
        if not args.cutover_output or not args.release_id:
            parser.error("--cutover-output and --release-id are required")
        document = json.loads(args.cutover_from.read_text(encoding="utf-8"))
        args.cutover_output.write_text(json.dumps(cutover_job(document, args.release_id),
                                                 indent=2) + "\n", encoding="utf-8")
    else:
        if not args.config or not args.output:
            parser.error("--config and --output are required")
        render(json.loads(args.config.read_text(encoding="utf-8")), args.output.resolve(),
               bootstrap_only=args.bootstrap_only)


if __name__ == "__main__":
    main()

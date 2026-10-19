"""Initialize routes/plugins in a FRESH dedicated Kong database with verified TLS.

This does not qualify remote contracts, create operators or modify Grafana.
Run only against the dedicated test Kong after its plugin migration succeeds.
"""

import argparse
import json
from pathlib import Path
import re
import ssl
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, ProxyHandler, Request, build_opener
from uuid import uuid4


HEADER_CLEANUP = """
for _, name in ipairs({'X-Consumer-Id', 'X-Credential-Identifier', 'X-Consumer-Custom-Id', 'X-Consumer-Username', 'X-Anonymous-Consumer'}) do
  kong.service.request.clear_header(name)
end
"""
GRAFANA_AUTH = """
local auth = kong.request.get_headers()['authorization']
if type(auth) ~= 'string' or kong.request.get_header('x-api-key') then
  return kong.response.exit(401, {message='One Bearer credential required'})
end
local key = auth:match('^[Bb][Ee][Aa][Rr][Ee][Rr] (vena_[%w_-]+)$')
if not key or #key ~= 48 then return kong.response.exit(401, {message='Invalid credential'}) end
ngx.req.set_header('X-API-Key', key)
ngx.req.clear_header('Authorization')
"""
NATIVE_AUTH = """
if kong.request.get_header('authorization') then
  return kong.response.exit(401, {message='Use X-API-Key'})
end
"""


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def validate(config):
    if "REQUIRED" in json.dumps(config):
        raise ValueError("Fill all REQUIRED private configuration values")
    parsed = urlsplit(config["admin_url"])
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment or parsed.path not in {"", "/"}):
        raise ValueError("Kong admin_url must be a trusted HTTPS origin without credentials")
    if not re.fullmatch(r"[a-z0-9]([a-z0-9.-]*[a-z0-9])?", config["upstream_host"]):
        raise ValueError("Supply the private-ingestion DNS hostname")
    if type(config["upstream_port"]) is not int or not 1 <= config["upstream_port"] <= 65535:
        raise ValueError("Supply the private-ingestion TLS port")
    if config["clock_health_file"] != "/run/appchi-clock/health.json":
        raise ValueError("Use the mounted, continuously refreshed clock evidence")


def routes(probe_token, member_id, clock_file):
    """Same credential transformations as the established gateway, with real clock evidence."""
    result = []
    for short, path in (("native", "/api/ingest/v2/"),
                        ("grafana", "/api/ingest/v2/grafana"),
                        ("probe", "/__appchi/operator-check"),
                        ("active-probe", "/__appchi/operator-auth-check")):
        pre = HEADER_CLEANUP + (GRAFANA_AUTH if short == "grafana" else NATIVE_AUTH)
        plugins = [{"name": "pre-function", "config": {"access": [pre]}}]
        if short != "probe":
            plugins.append({"name": "key-auth", "config": {
                "key_names": ["X-API-Key"], "hide_credentials": True,
                "key_in_header": True, "key_in_query": False, "key_in_body": False,
            }})
        plugins.append({"name": "appchi-policy", "config": {
            "member_id": member_id, "probe": short == "probe",
            "probe_active": short == "active-probe", "probe_token": probe_token,
            "clock_health_file": clock_file, "unqualified_local_clock": False,
        }})
        result.append((f"appchi-test-{short}", path, plugins))
    return result


def bootstrap(config, request, probe_token, upstream_ca):
    validate(config)
    if not 32 <= len(probe_token) <= 512 or not re.fullmatch(r"[A-Za-z0-9_-]+", probe_token):
        raise ValueError("Probe token must be a privately generated 32-512 character URL-safe secret")
    if "-----BEGIN CERTIFICATE-----" not in upstream_ca:
        raise ValueError("Supply the actual upstream CA certificate")
    # Refuse to overwrite another deployment or conceal a partial first attempt.
    for kind in ("services", "routes", "consumers", "plugins", "ca_certificates"):
        existing = request("GET", f"/{kind}?size=1")
        if existing.get("data") or existing.get("next"):
            raise ValueError(f"Kong {kind} are not empty; inspect partial/existing setup manually")
    node = request("GET", "/appchi-policy-node")
    member_id = node.get("member_id")
    if not isinstance(member_id, str) or not member_id:
        raise ValueError("Installed Appchi plugin did not return a serving member identity")
    ca_id = str(uuid4())
    request("PUT", "/ca_certificates/" + ca_id, {"cert": upstream_ca})
    service = request("PUT", "/services/appchi-test-v2", {
        "name": "appchi-test-v2", "protocol": "https", "host": config["upstream_host"],
        "port": config["upstream_port"], "tls_verify": True, "ca_certificates": [ca_id],
    })
    for name, path, plugins in routes(probe_token, member_id, config["clock_health_file"]):
        route = request("PUT", "/routes/" + name, {
            "name": name, "service": {"id": service["id"]}, "paths": [path],
            "strip_path": False, "protocols": ["https"],
        })
        for plugin in plugins:
            request("POST", "/routes/" + route["id"] + "/plugins", plugin)
    return {"configured": True, "serving_member_observed": member_id,
            "qualification": "not established", "grafana_modified": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--fresh-dedicated-test-kong", action="store_true", required=True)
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8"))
    validate(config)
    context = ssl.create_default_context(cafile=config["admin_ca_file"])
    opener = build_opener(ProxyHandler({}), NoRedirects(), HTTPSHandler(context=context))
    headers = {"Content-Type": "application/json"}
    if config.get("admin_token_file"):
        headers["Kong-Admin-Token"] = Path(config["admin_token_file"]).read_text().strip()

    def request(method, path, payload=None):
        req = Request(config["admin_url"].rstrip("/") + path,
                      data=json.dumps(payload).encode() if payload is not None else None,
                      headers=headers, method=method)
        try:
            with opener.open(req, timeout=30) as response:
                raw = response.read(1048577)
                if len(raw) > 1048576:
                    raise ValueError("Kong response exceeded bootstrap size limit")
                return json.loads(raw)
        except HTTPError as exc:
            # Never print response bodies or config: plugin responses can contain the probe token.
            raise RuntimeError(f"Kong {method} failed with HTTP {exc.code}; outcome may be partial") from None
        except URLError:
            raise RuntimeError("Kong transport failed; inspect for partial effects before retry") from None

    result = bootstrap(config, request,
                       Path(config["probe_token_file"]).read_text().strip(),
                       Path(config["upstream_ca_file"]).read_text())
    print(json.dumps(result))


if __name__ == "__main__":
    main()

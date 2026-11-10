"""Fake ticket counting service for local ServiceNow panels (REV-65).

Answers ``GET /count?team=...&state=new|in_progress|all[&system_failure=true|false]``
like the company's counting service, with deterministic synthetic counts so local
dashboards and journeys can exercise every panel state without ServiceNow:

- a team starting with ``unknown`` answers ``{"Team not found": "<team>"}``;
- a team starting with ``nocount`` answers ``{"status": "ok"}`` (valid JSON, no count);
- a team starting with ``slow`` waits longer than Keep's 5-second timeout;
- a team starting with ``error`` answers HTTP 500;
- any other team answers ``{"count": n}``, stable for the team and parameters.

``POST /__set`` with a JSON body ``{"team": "...", "count": n}`` pins a team's count
(``"count": null`` clears it), so a journey can push a count past a threshold.
Every request is printed as one JSON line without logging anything secret.

Run it next to the local API, which reads ``KEEP_TICKET_COUNT_URL``:

    .venv/Scripts/python.exe scripts/local_ticket_count.py --port 58090
"""

import argparse
import hashlib
import json
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

PINNED = {}


def synthetic(team, state, system_failure):
    digest = hashlib.sha256(f"{team.lower()}|{state}|{system_failure}".encode()).digest()
    return digest[0] % 40


class Handler(BaseHTTPRequestHandler):
    server_version = "keep-local-ticket-count"

    def _send(self, status, body):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        return

    def do_GET(self):
        parts = urlsplit(self.path)
        query = {key: values[-1] for key, values in parse_qs(parts.query).items()}
        print(json.dumps({"path": parts.path, "query": query}), flush=True)
        if parts.path != "/count":
            return self._send(404, {"error": "not found"})
        team = query.get("team", "")
        key = team.lower()
        if key.startswith("slow"):
            time.sleep(8)
        if key.startswith("error"):
            return self._send(500, {"error": "synthetic failure"})
        if key.startswith("unknown") or key == "__keep_connection_check__":
            return self._send(200, {"Team not found": team})
        if key.startswith("nocount"):
            return self._send(200, {"status": "ok"})
        state = query.get("state", "all")
        failure = query.get("system_failure", "")
        count = PINNED.get(key)
        if count is None:
            count = synthetic(team, state, failure)
        return self._send(200, {"count": count})

    def do_POST(self):
        if urlsplit(self.path).path != "/__set":
            return self._send(404, {"error": "not found"})
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        team = str(body.get("team", "")).lower()
        if body.get("count") is None:
            PINNED.pop(team, None)
        else:
            PINNED[team] = int(body["count"])
        return self._send(200, {"pinned": PINNED})


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=58090)
    args = parser.parse_args(argv)
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(json.dumps({"listening": f"http://{args.host}:{args.port}/count"}), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())

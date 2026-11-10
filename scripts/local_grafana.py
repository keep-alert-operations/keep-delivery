"""Fake Grafana for local Grafana panels (REV-68).

Serves single-panel pages at ``/d-solo/<uid>/<slug>?panelId=N&theme=light|dark`` so a
local dashboard can frame a "Grafana panel" without running Grafana. Each page draws a
small synthetic time series, follows the ``theme`` parameter and shows the panel number,
so a journey can check the frame's address and theme. ``/d/<uid>/<slug>`` serves a
plain "dashboard" page for the panel link. Any other path answers 404. Pages send no
``X-Frame-Options`` and no ``frame-ancestors``, like Grafana with
``allow_embedding = true``.

Run it next to the local API, whose ``KEEP_GRAFANA_PANEL_ORIGINS`` lists it:

    .venv/Scripts/python.exe scripts/local_grafana.py --port 58091
"""

import argparse
import html
import json
import math
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

THEMES = {
    "light": {"bg": "#ffffff", "fg": "#3a4553", "grid": "#e7eaee", "line": "#3d71d9"},
    "dark": {"bg": "#181b1f", "fg": "#c7d0d9", "grid": "#2c3138", "line": "#5794f2"},
}


def series(panel):
    seed = sum(ord(c) for c in panel) or 1
    points = []
    for i in range(40):
        value = 50 + 18 * math.sin(i / (3 + seed % 4)) + 8 * math.sin(i * 1.3 + seed)
        points.append(f"{i * 400 / 39:.1f},{100 - max(4, min(96, value)):.1f}")
    return " ".join(points)


def panel_page(path, query):
    theme = query.get("theme", ["light"])[0]
    colors = THEMES.get(theme, THEMES["light"])
    panel = query.get("panelId", ["1"])[0]
    title = f"Local panel {html.escape(panel)}"
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>{title}</title>
<style>
html,body{{margin:0;height:100%;background:{colors['bg']};color:{colors['fg']};font:12px system-ui,sans-serif}}
main{{box-sizing:border-box;height:100%;display:flex;flex-direction:column;gap:6px;padding:8px 10px}}
svg{{flex:1;min-height:0;width:100%}}
</style></head>
<body data-theme="{html.escape(theme)}" data-panel="{html.escape(panel)}">
<main><strong>{title}</strong>
<svg viewBox="0 0 400 100" preserveAspectRatio="none" role="img" aria-label="{title}">
<path d="M0 25H400M0 50H400M0 75H400" stroke="{colors['grid']}" vector-effect="non-scaling-stroke"/>
<polyline fill="none" stroke="{colors['line']}" stroke-width="1.6" vector-effect="non-scaling-stroke" points="{series(path + panel)}"/>
</svg>
<span>{html.escape(path)} · theme {html.escape(theme)}</span></main>
</body></html>"""


class Handler(BaseHTTPRequestHandler):
    server_version = "keep-local-grafana"

    def log_message(self, *args):
        return

    def _send(self, status, body, kind="text/html; charset=utf-8"):
        data = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        parts = urlsplit(self.path)
        query = parse_qs(parts.query)
        print(json.dumps({"path": parts.path, "theme": query.get("theme", [None])[0]}), flush=True)
        if parts.path.startswith("/d-solo/"):
            self._send(200, panel_page(parts.path, query))
        elif parts.path.startswith("/d/"):
            self._send(200, f"<!doctype html><title>Local dashboard</title><h1>Local dashboard {html.escape(parts.path)}</h1>")
        else:
            self._send(404, "Not found", "text/plain; charset=utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=58091)
    args = parser.parse_args(argv)
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(json.dumps({"listening": f"http://{args.host}:{args.port}"}), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())

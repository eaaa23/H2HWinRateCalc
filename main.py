#!/usr/bin/env python3
"""Magic Cube H2H Win Rate Calculator

Fetches historical results from WCA via the unofficial REST API for two
competitors, estimates head-to-head win rate using Monte Carlo simulation,
and serves results on a local web page.
"""

import json
from datetime import date
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

from api import search_cache
from computation import compute_h2h
from const import EVENT_MAP


# ---------------------------------------------------------------------------
# HTTP server
# ---------------------------------------------------------------------------

PORT = 8080

with open("main.html") as fp:
    HTML_PAGE = fp.read()


class H2HHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)

        if path == "/":
            self._send_html(HTML_PAGE)
        elif path == "/api/h2h":
            self._handle_api(params)
        elif path == "/api/search":
            self._handle_search(params)
        else:
            self._send_response(404, {"error": "Not found"})

    def _handle_api(self, params):
        id1 = params.get("id1", [""])[0]
        id2 = params.get("id2", [""])[0]
        event = params.get("event", ["333"])[0]
        value_type = params.get("type", ["single"])[0]
        rng_type = params.get("rng", ["history"])[0]
        timerange_str = params.get("timerange", ["-1"])[0]

        if not id1 or not id2:
            self._send_response(400, {"error": "Both id1 and id2 are required."})
            return
        if event not in EVENT_MAP:
            self._send_response(400, {"error": f"Invalid event. Supported: {', '.join(EVENT_MAP.keys())}"})
            return
        if value_type not in ("single", "average"):
            self._send_response(400, {"error": "type must be 'single' or 'average'."})
            return

        since_year = None
        try:
            timerange_int = int(timerange_str)
            if timerange_int >= 0:
                today = date.today()
                since_year = today.year - timerange_int
        except (ValueError, OverflowError):
            pass

        try:
            result = compute_h2h(id1, id2, event, value_type, rng_type, since_year)
            self._send_response(200, result)
        except Exception as e:
            self._send_response(500, {"error": str(e)})

    def _handle_search(self, params):
        query = params.get("q", [""])[0]
        if not query:
            self._send_response(400, {"error": "Query parameter 'q' is required."})
            return
        try:
            results = search_cache.search_wca_persons(query)
            self._send_response(200, {"results": results})
        except Exception as e:
            self._send_response(500, {"error": str(e)})

    def _send_html(self, html: str):
        data = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_response(self, code: int, obj: dict):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):
        pass  # quiet mode


def main():
    # 1. Load cached data from last run so search works immediately
    if loaded_length:=search_cache.load_local_cache() > 0:
        print(f"Loaded {loaded_length} players from local cache")

    # 2. Refresh from API in the background; the old cache stays available
    print("Refreshing search cache from API in background ...")
    search_cache.start_build_cache()

    server = HTTPServer(("127.0.0.1", PORT), H2HHandler)
    print(f"Cube H2H Calculator running at http://localhost:{PORT}")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.server_close()


if __name__ == "__main__":
    main()

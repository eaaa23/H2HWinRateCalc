#!/usr/bin/env python3
"""Magic Cube H2H Win Rate Calculator

Fetches historical results from WCA API for two competitors,
estimates head-to-head win rate using Monte Carlo simulation,
and serves results on a local web page.
"""

import json
import math
import random
import threading
from datetime import datetime, date
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import requests

WCA_API_BASE = "https://www.worldcubeassociation.org/api/v0"

EVENT_MAP = {
    "333": "3x3x3 Cube",
    "222": "2x2x2 Cube",
    "444": "4x4x4 Cube",
    "555": "5x5x5 Cube",
    "333bf": "3x3x3 Blindfolded",
    "333oh": "3x3x3 One-Handed",
}

PORT = 8080


def fetch_wca_person(wca_id: str) -> dict:
    """Fetch basic person info from WCA API.
    
    Returns the nested 'person' dict with name, country_iso2, etc.
    """
    resp = requests.get(f"{WCA_API_BASE}/persons/{wca_id}", timeout=15)
    resp.raise_for_status()
    data = resp.json()
    person = data.get("person", {})
    if not person:
        raise ValueError(f"Person '{wca_id}' not found on WCA.")
    return {
        "name": person.get("name", wca_id),
        "country_iso2": person.get("country_iso2", ""),
    }


def fetch_wca_results(wca_id: str, event_id: str) -> list:
    """Fetch competition results for a user, filtered by event."""
    resp = requests.get(
        f"{WCA_API_BASE}/persons/{wca_id}/results",
        params={"event_id": event_id},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def extract_times(results: list[dict], value_type: str, since_year: int = None) -> list:
    """Extract valid times for a given value type.
    
    value_type: 'single' or 'average'
    DNF (-1) and DNS (-2) are excluded.
    WCA stores times in centiseconds.
    since_year: optional year int; only include results from
                competitions on or after this year.
    """
    times = []
    for r in results:
        if since_year:
            competition_id = r.get("competition_id", "")
            competition_year = competition_id[-4:]
            if not competition_year.isdigit():
                continue
            if int(competition_year) < since_year:
                continue

        result_times = []
        if value_type == "average":
            result_times = [r.get("average", 0)]
        elif value_type == "single":
            result_times = r.get("attempts", [])

        for val in result_times:
            if val is not None and val >= 0:
                times.append(val)
    return times


def format_time(centiseconds: float) -> str:
    """Format centiseconds into human-readable time string."""
    if centiseconds is None:
        return "N/A"
    cs = int(centiseconds)
    if cs == 0:
        return "0.00"
    minutes, cs = divmod(cs, 6000)
    seconds, hundredths = divmod(cs, 100)
    if minutes > 0:
        return f"{minutes}:{seconds:02d}.{hundredths:02d}"
    return f"{seconds}.{hundredths:02d}"


def calc_stats(times: list) -> dict:
    """Calculate statistics from a list of times (in centiseconds)."""
    if not times:
        return None
    n = len(times)
    mean = sum(times) / n
    variance = sum((t - mean) ** 2 for t in times) / n
    std = math.sqrt(variance)
    best = min(times)
    worst = max(times)
    return {
        "count": n,
        "best": best,
        "worst": worst,
        "mean": mean,
        "std": std,
    }


def monte_carlo_winrate(
    times1: list, times2: list, simulations: int = 50000
) -> dict:
    """Estimate H2H win rate using Monte Carlo simulation.
    
    Models each competitor's performance as a normal distribution
    fitted to their historical results. Lower time = better.
    """
    stats1 = calc_stats(times1)
    stats2 = calc_stats(times2)
    if not stats1 or not stats2:
        return None

    mu1, sigma1 = stats1["mean"], stats1["std"]
    mu2, sigma2 = stats2["mean"], stats2["std"]

    wins1 = 0
    wins2 = 0
    draws = 0

    for _ in range(simulations):
        # Ensure sigma is at least a small positive value to avoid degenerate distributions
        s1 = max(sigma1, 0.5)
        s2 = max(sigma2, 0.5)
        t1 = random.gauss(mu1, s1)
        t2 = random.gauss(mu2, s2)
        # For normal events, lower time wins; for FMC (fewest moves), lower is also better
        # WCA FMC stores result as number of moves * 100, so lower still wins
        if t1 < t2:
            wins1 += 1
        elif t2 < t1:
            wins2 += 1
        else:
            draws += 1

    return {
        "player1_winrate": wins1 / simulations,
        "player2_winrate": wins2 / simulations,
        "draw_rate": draws / simulations,
        "stats1": {k: (format_time(v) if k != "count" else v) for k, v in stats1.items()},
        "stats2": {k: (format_time(v) if k != "count" else v) for k, v in stats2.items()},
    }


def search_wca_persons(query: str) -> list:
    """Search for WCA persons by name or ID."""
    resp = requests.get(
        f"{WCA_API_BASE}/persons",
        params={"search": query, "per_page": 10},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    results = []
    for item in data:
        p = item.get("person", item)  # search returns {"person": {...}}
        results.append({
            "wca_id": p.get("wca_id", ""),
            "name": p.get("name", ""),
            "country_iso2": p.get("country_iso2", ""),
        })
    return results


def compute_h2h(id1: str, id2: str, event_id: str, value_type: str, since_year: int = None) -> dict:
    """Main function: fetch data, compute, return result."""
    if id1 == id2:
        return {"error": "Please enter two different WCA IDs."}

    person1, person2 = {}, {}
    results1, results2 = [], []
    errors = []

    def fetch1():
        nonlocal person1, results1
        try:
            person1 = fetch_wca_person(id1)
            results1 = fetch_wca_results(id1, event_id)
        except Exception as e:
            errors.append(f"Player 1 ({id1}): {e}")

    def fetch2():
        nonlocal person2, results2
        try:
            person2 = fetch_wca_person(id2)
            results2 = fetch_wca_results(id2, event_id)
        except Exception as e:
            errors.append(f"Player 2 ({id2}): {e}")

    th1 = threading.Thread(target=fetch1)
    th2 = threading.Thread(target=fetch2)
    th1.start()
    th2.start()
    th1.join(timeout=20)
    th2.join(timeout=20)

    if errors:
        return {"error": " | ".join(errors)}

    name1 = person1.get("name", id1)
    name2 = person2.get("name", id2)
    country1 = person1.get("country_iso2", "")
    country2 = person2.get("country_iso2", "")

    times1 = extract_times(results1, value_type, since_year)
    times2 = extract_times(results2, value_type, since_year)

    if not times1:
        return {"error": f"{name1} has no valid results for {EVENT_MAP.get(event_id, event_id)} ({value_type})."}
    if not times2:
        return {"error": f"{name2} has no valid results for {EVENT_MAP.get(event_id, event_id)} ({value_type})."}

    result = monte_carlo_winrate(times1, times2)
    if not result:
        return {"error": "Could not calculate win rate."}

    return {
        "player1": {"id": id1, "name": name1, "country": country1},
        "player2": {"id": id2, "name": name2, "country": country2},
        "event": EVENT_MAP.get(event_id, event_id),
        "event_id": event_id,
        "type": value_type,
        "since_year": since_year,
        "winrate": result,
    }


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
            result = compute_h2h(id1, id2, event, value_type, since_year)
            self._send_response(200, result)
        except Exception as e:
            self._send_response(500, {"error": str(e)})

    def _handle_search(self, params):
        query = params.get("q", [""])[0]
        if not query:
            self._send_response(400, {"error": "Query parameter 'q' is required."})
            return
        try:
            results = search_wca_persons(query)
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
        # Quiet mode: only log errors
        pass


def main():
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

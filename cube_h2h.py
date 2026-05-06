#!/usr/bin/env python3
"""Magic Cube H2H Win Rate Calculator

Fetches historical results from WCA via the unofficial REST API for two
competitors, estimates head-to-head win rate using Monte Carlo simulation,
and serves results on a local web page.
"""

import concurrent.futures
import json
import math
import random
import threading
from datetime import date
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

import requests

WCA_API_BASE = "https://raw.githubusercontent.com/robiningelbrecht/wca-rest-api/refs/heads/v1"

EVENT_MAP = {
    "333": "3x3x3 Cube",
    "222": "2x2x2 Cube",
    "444": "4x4x4 Cube",
    "555": "5x5x5 Cube",
    "333bf": "3x3x3 Blindfolded",
    "333oh": "3x3x3 One-Handed",
}

PORT = 8080

# ---------------------------------------------------------------------------
# WCA data fetching (unofficial static-file API)
# ---------------------------------------------------------------------------

def fetch_wca_person(wca_id: str) -> dict:
    """Fetch full person info from the unofficial WCA REST API.

    Returns a dict with name, country, and the nested 'results' object
    keyed by competition_id -> event_id -> list of round results.
    """
    resp = requests.get(
        f"{WCA_API_BASE}/persons/{wca_id}.json",
        headers={"User-Agent": "cube-h2h-calculator/1.0"},
        timeout=30,
    )
    resp.raise_for_status()
    person = resp.json()  # flat object, no nested "person" key
    return {
        "name": person.get("name", wca_id),
        "country": person.get("country", ""),
        "results": person.get("results", {}),
    }


def extract_times(results: dict, event_id: str, value_type: str,
                  since_year: int = None) -> list:
    """Extract valid times from the nested results structure.

    results: dict  keyed by competition_id -> event_id -> list of round dicts
    event_id: e.g. "333"
    value_type: "single" or "average"
    since_year: optional year int; only include results from competitions
                on or after this year (inferred from competition ID suffix).

    DNF (-1) and DNS (-2) results are excluded.
    WCA stores times in centiseconds.
    """
    times = []
    for competition_id, events in results.items():
        if since_year:
            comp_year_str = competition_id[-4:]
            if not comp_year_str.isdigit():
                continue
            if int(comp_year_str) < since_year:
                continue

        round_list = events.get(event_id, [])
        for rd in round_list:
            if value_type == "average":
                val = rd.get("average", 0)
                if val is not None and val >= 0:
                    times.append(val)
            elif value_type == "single":
                for val in rd.get("solves", []):
                    if val is not None and val >= 0:
                        times.append(val)
    return times


# ---------------------------------------------------------------------------
# Formatting & statistics
# ---------------------------------------------------------------------------

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
    fitted to their historical results.  Lower time = better.
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
        s1 = max(sigma1, 0.5)
        s2 = max(sigma2, 0.5)
        t1 = random.gauss(mu1, s1)
        t2 = random.gauss(mu2, s2)
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
        "stats1": {k: (format_time(v) if k != "count" else v)
                   for k, v in stats1.items()},
        "stats2": {k: (format_time(v) if k != "count" else v)
                   for k, v in stats2.items()},
    }


# ---------------------------------------------------------------------------
# Local person search – built from top-100 rankings per event
# ---------------------------------------------------------------------------

_SEARCH_CACHE = {}              # {wca_id: {"name":..., "country":...}}
_SEARCH_CACHE_READY = threading.Event()
_SEARCH_SESSION = requests.Session()
_SEARCH_SESSION.headers["User-Agent"] = "cube-h2h-calculator/1.0"
_RANK_TOP_N = 100


def _build_search_cache():
    """Background: fetch top-N ranks for every event, then load their names."""
    global _SEARCH_CACHE

    # 1. Collect top-ranked person IDs from each event×type combination
    top_ids: set[str] = set()
    for event_id in EVENT_MAP:
        for rank_type in ("single", "average"):
            url = f"{WCA_API_BASE}/rank/world/{rank_type}/{event_id}.json"
            try:
                resp = _SEARCH_SESSION.get(url, timeout=30)
                resp.raise_for_status()
                for item in resp.json().get("items", [])[:_RANK_TOP_N]:
                    top_ids.add(item["personId"])
            except Exception as e:
                print(f"  (rank {rank_type}/{event_id} skipped: {e})")

    print(f"  top-ranked IDs collected: {len(top_ids)}")

    # 2. Fetch each person's name/country concurrently (only need basic info)
    fetched = 0
    fetched_lock = threading.Lock()

    def _fetch_one(pid: str):
        nonlocal fetched
        try:
            resp = _SEARCH_SESSION.get(
                f"{WCA_API_BASE}/persons/{pid}.json", timeout=30
            )
            resp.raise_for_status()
            data = resp.json()
            _SEARCH_CACHE[pid] = {
                "name": data.get("name", ""),
                "country": data.get("country", ""),
            }
        except Exception:
            pass
        with fetched_lock:
            fetched += 1
            if fetched % 50 == 0:
                print(f"  ... {fetched}/{len(top_ids)} persons indexed")

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(_fetch_one, top_ids))

    _SEARCH_CACHE_READY.set()
    print(f"  search cache ready: {len(_SEARCH_CACHE)} players")


def search_wca_persons(query: str) -> list:
    """Search cached top-ranked persons by name or WCA ID."""
    q = query.strip().lower()
    if not q or not _SEARCH_CACHE_READY.is_set():
        return []
    results = []
    for wca_id, info in _SEARCH_CACHE.items():
        if q in info["name"].lower() or q in wca_id.lower():
            results.append({
                "wca_id": wca_id,
                "name": info["name"],
                "country_iso2": info["country"],
            })
            if len(results) >= 10:
                break
    return results


# ---------------------------------------------------------------------------
# H2H computation
# ---------------------------------------------------------------------------

def compute_h2h(id1: str, id2: str, event_id: str, value_type: str,
                since_year: int = None) -> dict:
    """Main function: fetch data, compute, return result."""
    if id1 == id2:
        return {"error": "Please enter two different WCA IDs."}

    person1, person2 = {}, {}
    errors = []

    def fetch1():
        nonlocal person1
        try:
            person1 = fetch_wca_person(id1)
        except Exception as e:
            errors.append(f"Player 1 ({id1}): {e}")

    def fetch2():
        nonlocal person2
        try:
            person2 = fetch_wca_person(id2)
        except Exception as e:
            errors.append(f"Player 2 ({id2}): {e}")

    th1 = threading.Thread(target=fetch1)
    th2 = threading.Thread(target=fetch2)
    th1.start()
    th2.start()
    th1.join(timeout=30)
    th2.join(timeout=30)

    if errors:
        return {"error": " | ".join(errors)}

    name1 = person1.get("name", id1)
    name2 = person2.get("name", id2)
    country1 = person1.get("country", "")
    country2 = person2.get("country", "")

    times1 = extract_times(person1.get("results", {}), event_id,
                           value_type, since_year)
    times2 = extract_times(person2.get("results", {}), event_id,
                           value_type, since_year)

    if not times1:
        return {"error": f"{name1} has no valid results for "
                         f"{EVENT_MAP.get(event_id, event_id)} ({value_type})."}
    if not times2:
        return {"error": f"{name2} has no valid results for "
                         f"{EVENT_MAP.get(event_id, event_id)} ({value_type})."}

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


# ---------------------------------------------------------------------------
# HTTP server
# ---------------------------------------------------------------------------

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
        pass  # quiet mode


def main():
    print("Building search cache (top-100 per event) ...")
    threading.Thread(target=_build_search_cache, daemon=True).start()

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

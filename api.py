import concurrent.futures
import json
import threading

import requests

from const import EVENT_MAP

WCA_API_BASE = "https://raw.githubusercontent.com/robiningelbrecht/wca-rest-api/refs/heads/v1"


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
            vals = []
            if value_type == "average":
                vals = [rd.get("average", 0)]
            elif value_type == "single":
                vals = rd.get("solves", [])
            times.extend(filter(lambda val: val is not None and val >= 0, vals))
    return times


RANK_TOP_N = 100
_CACHE_FILE = "search_cache.json"


class _SearchCache:
    """
    Local person search – built from top-100 rankings per event
    """
    def __init__(self):
        self._search_cache = {}  # {wca_id: {"name":..., "country":...}}
        self._search_cache_ready = threading.Event()
        self._search_session = requests.Session()
        self._search_session.headers["User-Agent"] = "cube-h2h-calculator/1.0"

    def load_local_cache(self) -> int:
        """Load search cache from local JSON file. Returns length of loaded cache, -1 if failed."""
        try:
            with open(_CACHE_FILE) as f:
                data = json.load(f)
            if isinstance(data, dict) and len(data) > 0:
                self._search_cache.clear()
                self._search_cache.update(data)
                self._search_cache_ready.set()
                return len(self._search_cache)
        except Exception:
            pass
        return -1

    def _save_local_cache(self):
        """Persist the current search cache to a local JSON file."""
        try:
            with open(_CACHE_FILE, "w") as f:
                json.dump(self._search_cache, f, ensure_ascii=False)
        except Exception:
            pass

    def start_build_cache(self):
        threading.Thread(target=self._build_search_cache, daemon=True).start()

    def _build_search_cache(self):
        """Background: fetch top-N ranks for every event, then load their names.
        Then replace the global cache and persist to disk."""
        new_cache: dict[str, dict] = {}
        has_exception = False

        # 1. Collect top-ranked person IDs from each event×type combination
        top_ids: set[str] = set()
        for event_id in EVENT_MAP:
            for rank_type in ("single", "average"):
                url = f"{WCA_API_BASE}/rank/world/{rank_type}/{event_id}.json"
                try:
                    resp = self._search_session.get(url, timeout=30)
                    resp.raise_for_status()
                    for item in resp.json().get("items", [])[:RANK_TOP_N]:
                        top_ids.add(item["personId"])
                except Exception as e:
                    has_exception = True
                    print(f"  (rank {rank_type}/{event_id} skipped: {e})")

        print(f"  top-ranked IDs collected: {len(top_ids)}")

        # 2. Fetch each person's name/country concurrently
        fetched = 0
        fetched_lock = threading.Lock()

        def _fetch_one(wca_id: str):
            nonlocal fetched, has_exception
            try:
                resp = self._search_session.get(
                    f"{WCA_API_BASE}/persons/{wca_id}.json", timeout=30
                )
                resp.raise_for_status()
                data = resp.json()
                new_cache[wca_id] = {
                    "name": data.get("name", ""),
                    "country": data.get("country", ""),
                }
            except Exception:
                has_exception = True
            with fetched_lock:
                fetched += 1
                if fetched % 50 == 0:
                    print(f"  ... {fetched}/{len(top_ids)} persons indexed")

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(_fetch_one, top_ids))

        # 3. Merge new entries into existing cache (never remove old entries
        #    on partial failure) and persist
        old_count = len(self._search_cache)
        if not has_exception:
            self._search_cache.clear()
        self._search_cache.update(new_cache)
        self._search_cache_ready.set()
        self._save_local_cache()

        added = len(self._search_cache) - old_count
        updated = len(new_cache) - added if len(new_cache) > added else 0
        print(f"  search cache updated: {len(self._search_cache)} players "
              f"({added} added, {updated} refreshed)")

    def search_wca_persons(self, query: str) -> list:
        """Search cached top-ranked persons by name or WCA ID."""
        q = query.strip().lower()
        if not q or not self._search_cache_ready.is_set():
            return []
        results = []
        for wca_id, info in self._search_cache.items():
            if q in info["name"].lower() or q in wca_id.lower():
                results.append({
                    "wca_id": wca_id,
                    "name": info["name"],
                    "country_iso2": info["country"],
                })
                if len(results) >= 10:
                    break
        return results


search_cache = _SearchCache()

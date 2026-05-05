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


def extract_times(results: list, value_type: str) -> list:
    """Extract valid times for a given value type.
    
    value_type: 'single' or 'average'
    DNF (-1) and DNS (-2) are excluded.
    WCA stores times in centiseconds.
    """
    times = []
    for r in results:
        val = r.get("best" if value_type == "single" else "average")
        if val is None or val < 0:
            continue
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


def compute_h2h(id1: str, id2: str, event_id: str, value_type: str) -> dict:
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

    times1 = extract_times(results1, value_type)
    times2 = extract_times(results2, value_type)

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
        "winrate": result,
    }


HTML_PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Cube H2H Win Rate Calculator</title>
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
  background: #0f172a;
  color: #e2e8f0;
  min-height: 100vh;
  display: flex;
  flex-direction: column;
  align-items: center;
}
.header {
  text-align: center;
  padding: 40px 20px 20px;
}
.header h1 {
  font-size: 2rem;
  font-weight: 700;
  background: linear-gradient(135deg, #60a5fa, #a78bfa);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
}
.header p {
  color: #94a3b8;
  margin-top: 8px;
  font-size: 0.95rem;
}
.form-card {
  background: #1e293b;
  border-radius: 16px;
  padding: 32px;
  width: 90%;
  max-width: 700px;
  margin-top: 20px;
  box-shadow: 0 4px 24px rgba(0,0,0,0.3);
}
.vs-row {
  display: flex;
  gap: 16px;
  align-items: flex-end;
  flex-wrap: wrap;
}
.player-input {
  flex: 1;
  min-width: 180px;
  position: relative;
}
.player-input label {
  display: block;
  font-size: 0.85rem;
  color: #94a3b8;
  margin-bottom: 6px;
}
.player-input input {
  width: 100%;
  padding: 10px 14px;
  border-radius: 8px;
  border: 1px solid #334155;
  background: #0f172a;
  color: #e2e8f0;
  font-size: 1rem;
  outline: none;
  transition: border-color 0.2s;
}
.player-input input:focus {
  border-color: #60a5fa;
}
.search-dropdown {
  display: none;
  position: absolute;
  top: 100%;
  left: 0;
  right: 0;
  background: #0f172a;
  border: 1px solid #334155;
  border-radius: 8px;
  margin-top: 4px;
  max-height: 240px;
  overflow-y: auto;
  z-index: 100;
  box-shadow: 0 8px 24px rgba(0,0,0,0.4);
}
.search-dropdown.show { display: block; }
.search-item {
  padding: 8px 14px;
  cursor: pointer;
  font-size: 0.9rem;
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.search-item:hover, .search-item.active {
  background: #1e293b;
}
.search-item .name { color: #e2e8f0; }
.search-item .wca-id { color: #64748b; font-size: 0.8rem; margin-left: 8px; }
.options-row {
  display: flex;
  gap: 12px;
  margin-top: 16px;
  flex-wrap: wrap;
}
.options-row select {
  flex: 1;
  min-width: 140px;
  padding: 10px 14px;
  border-radius: 8px;
  border: 1px solid #334155;
  background: #0f172a;
  color: #e2e8f0;
  font-size: 0.95rem;
  outline: none;
}
.btn-calc {
  margin-top: 20px;
  width: 100%;
  padding: 12px;
  border: none;
  border-radius: 10px;
  background: linear-gradient(135deg, #3b82f6, #8b5cf6);
  color: #fff;
  font-size: 1.05rem;
  font-weight: 600;
  cursor: pointer;
  transition: opacity 0.2s;
}
.btn-calc:hover { opacity: 0.9; }
.btn-calc:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.loading {
  display: none;
  text-align: center;
  margin-top: 24px;
  color: #94a3b8;
}
.loading.show { display: block; }
.spinner {
  display: inline-block;
  width: 24px; height: 24px;
  border: 3px solid #334155;
  border-top-color: #60a5fa;
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
  vertical-align: middle;
  margin-right: 8px;
}
@keyframes spin { to { transform: rotate(360deg); } }

.error-msg {
  display: none;
  background: #451a1a;
  border: 1px solid #7f1d1d;
  border-radius: 8px;
  padding: 14px 18px;
  margin-top: 20px;
  color: #fca5a5;
  font-size: 0.9rem;
}
.error-msg.show { display: block; }

.result-card {
  display: none;
  background: #1e293b;
  border-radius: 16px;
  padding: 32px;
  width: 90%;
  max-width: 700px;
  margin-top: 24px;
  box-shadow: 0 4px 24px rgba(0,0,0,0.3);
}
.result-card.show { display: block; }

.result-header {
  text-align: center;
  margin-bottom: 24px;
}
.result-header h2 {
  font-size: 1.3rem;
  color: #cbd5e1;
}
.result-header .event-label {
  font-size: 0.85rem;
  color: #64748b;
  margin-top: 4px;
}

.players-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
}
.player-name {
  font-size: 1.1rem;
  font-weight: 600;
}
.player-flag {
  font-size: 1.3rem;
  margin-right: 6px;
}
.vs-label {
  font-size: 0.85rem;
  color: #64748b;
  font-weight: 700;
}

.winrate-bar {
  display: flex;
  height: 48px;
  border-radius: 12px;
  overflow: hidden;
  margin-bottom: 8px;
  background: #334155;
}
.wr-left {
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(90deg, #3b82f6, #6366f1);
  color: #fff;
  font-weight: 700;
  font-size: 1rem;
  transition: width 0.6s ease;
  min-width: 40px;
}
.wr-right {
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(90deg, #f59e0b, #ef4444);
  color: #fff;
  font-weight: 700;
  font-size: 1rem;
  transition: width 0.6s ease;
  min-width: 40px;
}

.draw-info {
  text-align: center;
  font-size: 0.8rem;
  color: #64748b;
  margin-bottom: 28px;
}

.stats-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
}
.stats-box {
  background: #0f172a;
  border-radius: 12px;
  padding: 20px;
}
.stats-box h3 {
  font-size: 0.95rem;
  color: #94a3b8;
  margin-bottom: 12px;
  padding-bottom: 8px;
  border-bottom: 1px solid #1e293b;
}
.stat-row {
  display: flex;
  justify-content: space-between;
  padding: 4px 0;
  font-size: 0.9rem;
}
.stat-row .label { color: #64748b; }
.stat-row .value { color: #e2e8f0; font-weight: 500; }

.footer {
  margin-top: 40px;
  padding: 20px;
  text-align: center;
  color: #475569;
  font-size: 0.8rem;
}
</style>
</head>
<body>

<div class="header">
  <h1>🎮 Cube H2H Calculator</h1>
  <p>Estimate head-to-head win rates between two speedcubers based on WCA results</p>
</div>

<div class="form-card">
  <div class="vs-row">
    <div class="player-input">
      <label>Player 1 — Name or WCA ID</label>
      <input type="text" id="id1" placeholder="e.g. Zemdegs or 2009ZEMD01" autocomplete="off" />
      <div class="search-dropdown" id="dropdown1"></div>
    </div>
    <span style="color:#64748b;font-weight:700;font-size:1.1rem;padding-bottom:6px;">VS</span>
    <div class="player-input">
      <label>Player 2 — Name or WCA ID</label>
      <input type="text" id="id2" placeholder="e.g. Tymon or 2015KOLM01" autocomplete="off" />
      <div class="search-dropdown" id="dropdown2"></div>
    </div>
  </div>
  <div class="options-row">
    <select id="event">
      <option value="333">3x3x3 Cube</option>
      <option value="222">2x2x2 Cube</option>
      <option value="444">4x4x4 Cube</option>
      <option value="555">5x5x5 Cube</option>
      <option value="333oh">3x3x3 One-Handed</option>
      <option value="333bf">3x3x3 Blindfolded</option>
    </select>
    <select id="valuetype">
      <option value="single">Single (best of round)</option>
      <option value="average">Average (mean of round)</option>
    </select>
  </div>
  <button class="btn-calc" id="btnCalc" onclick="doCalc()">Calculate Win Rate</button>
</div>

<div class="loading" id="loading">
  <span class="spinner"></span> Fetching WCA data & computing...
</div>
<div class="error-msg" id="errorMsg"></div>

<div class="result-card" id="resultCard">
  <div class="result-header">
    <h2 id="matchTitle"></h2>
    <div class="event-label" id="eventLabel"></div>
  </div>
  <div class="players-row">
    <div><span class="player-flag" id="flag1"></span><span class="player-name" id="pname1"></span></div>
    <span class="vs-label">VS</span>
    <div><span class="player-name" id="pname2"></span><span class="player-flag" id="flag2"></span></div>
  </div>
  <div class="winrate-bar">
    <div class="wr-left" id="wrLeft" style="width:50%"></div>
    <div class="wr-right" id="wrRight" style="width:50%"></div>
  </div>
  <div class="draw-info" id="drawInfo"></div>
  <div class="stats-grid">
    <div class="stats-box">
      <h3 id="statsTitle1"></h3>
      <div class="stat-row"><span class="label">Samples</span><span class="value" id="s1count"></span></div>
      <div class="stat-row"><span class="label">Best</span><span class="value" id="s1best"></span></div>
      <div class="stat-row"><span class="label">Worst</span><span class="value" id="s1worst"></span></div>
      <div class="stat-row"><span class="label">Mean</span><span class="value" id="s1mean"></span></div>
      <div class="stat-row"><span class="label">Std Dev</span><span class="value" id="s1std"></span></div>
    </div>
    <div class="stats-box">
      <h3 id="statsTitle2"></h3>
      <div class="stat-row"><span class="label">Samples</span><span class="value" id="s2count"></span></div>
      <div class="stat-row"><span class="label">Best</span><span class="value" id="s2best"></span></div>
      <div class="stat-row"><span class="label">Worst</span><span class="value" id="s2worst"></span></div>
      <div class="stat-row"><span class="label">Mean</span><span class="value" id="s2mean"></span></div>
      <div class="stat-row"><span class="label">Std Dev</span><span class="value" id="s2std"></span></div>
    </div>
  </div>
</div>

<div class="footer">
  Data sourced from <a href="https://www.worldcubeassociation.org" target="_blank" style="color:#60a5fa">WCA</a>.
  Win rates estimated via Monte Carlo simulation over 50,000 trials using normal distribution fit.
</div>

<script>
function flagEmoji(country) {
  if (!country || country.length !== 2) return '';
  const codePoints = country.toUpperCase().split('').map(c => 127397 + c.charCodeAt(0));
  return String.fromCodePoint(...codePoints);
}

// Search-as-you-type
let searchTimers = {};
function setupSearch(inputId, dropdownId) {
  const input = document.getElementById(inputId);
  const dropdown = document.getElementById(dropdownId);

  input.addEventListener('input', function() {
    const q = this.value.trim();
    if (searchTimers[inputId]) clearTimeout(searchTimers[inputId]);
    if (q.length < 2) { dropdown.classList.remove('show'); return; }
    searchTimers[inputId] = setTimeout(() => {
      fetch('/api/search?q=' + encodeURIComponent(q))
        .then(r => r.json())
        .then(data => {
          if (data.error) return;
          const results = data.results;
          if (!results.length) { dropdown.classList.remove('show'); return; }
          dropdown.innerHTML = results.map(p =>
            '<div class="search-item" data-id="' + p.wca_id + '">' +
            '<span>' + flagEmoji(p.country_iso2) + ' ' + p.name + '</span>' +
            '<span class="wca-id">' + p.wca_id + '</span></div>'
          ).join('');
          dropdown.classList.add('show');
          dropdown.querySelectorAll('.search-item').forEach(item => {
            item.addEventListener('click', function() {
              input.value = this.dataset.id;
              dropdown.classList.remove('show');
            });
          });
        })
        .catch(() => {});
    }, 350);
  });

  input.addEventListener('keydown', function(e) {
    const items = dropdown.querySelectorAll('.search-item');
    const active = dropdown.querySelector('.search-item.active');
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      if (active) active.classList.remove('active');
      const next = active ? active.nextElementSibling : items[0];
      if (next) next.classList.add('active');
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      if (active) active.classList.remove('active');
      const prev = active ? active.previousElementSibling : items[items.length - 1];
      if (prev) prev.classList.add('active');
    } else if (e.key === 'Enter' && active) {
      e.preventDefault();
      input.value = active.dataset.id;
      dropdown.classList.remove('show');
      doCalc();
    } else if (e.key === 'Escape') {
      dropdown.classList.remove('show');
    }
  });

  document.addEventListener('click', function(e) {
    if (!input.contains(e.target) && !dropdown.contains(e.target)) {
      dropdown.classList.remove('show');
    }
  });
}

setupSearch('id1', 'dropdown1');
setupSearch('id2', 'dropdown2');

function doCalc() {
  const id1 = document.getElementById('id1').value.trim();
  const id2 = document.getElementById('id2').value.trim();
  const event = document.getElementById('event').value;
  const valuetype = document.getElementById('valuetype').value;

  if (!id1 || !id2) {
    showError('Please enter both WCA IDs.');
    return;
  }

  document.getElementById('loading').classList.add('show');
  document.getElementById('errorMsg').classList.remove('show');
  document.getElementById('resultCard').classList.remove('show');
  document.getElementById('btnCalc').disabled = true;

  const params = new URLSearchParams({ id1, id2, event, type: valuetype });
  fetch('/api/h2h?' + params)
    .then(r => r.json())
    .then(data => {
      document.getElementById('loading').classList.remove('show');
      document.getElementById('btnCalc').disabled = false;
      if (data.error) {
        showError(data.error);
        return;
      }
      showResult(data);
    })
    .catch(err => {
      document.getElementById('loading').classList.remove('show');
      document.getElementById('btnCalc').disabled = false;
      showError('Request failed: ' + err.message);
    });
}

function showError(msg) {
  const el = document.getElementById('errorMsg');
  el.textContent = msg;
  el.classList.add('show');
}

function showResult(d) {
  const wr = d.winrate;
  const p1w = (wr.player1_winrate * 100).toFixed(1);
  const p2w = (wr.player2_winrate * 100).toFixed(1);
  const drawP = (wr.draw_rate * 100).toFixed(2);

  document.getElementById('matchTitle').textContent = d.player1.name + ' vs ' + d.player2.name;
  document.getElementById('eventLabel').textContent = d.event + ' — ' + (d.type === 'single' ? 'Single' : 'Average');
  document.getElementById('flag1').textContent = flagEmoji(d.player1.country);
  document.getElementById('flag2').textContent = flagEmoji(d.player2.country);
  document.getElementById('pname1').textContent = d.player1.name;
  document.getElementById('pname2').textContent = d.player2.name;

  // Minimum 5% width so label is visible
  const w1 = Math.max(5, parseFloat(p1w));
  const w2 = Math.max(5, parseFloat(p2w));
  const total = w1 + w2;
  document.getElementById('wrLeft').style.width = (w1 / total * 100) + '%';
  document.getElementById('wrLeft').textContent = p1w + '%';
  document.getElementById('wrRight').style.width = (w2 / total * 100) + '%';
  document.getElementById('wrRight').textContent = p2w + '%';
  document.getElementById('drawInfo').textContent = 'Draw: ' + drawP + '% (50,000 Monte Carlo simulations)';

  const s1 = wr.stats1;
  const s2 = wr.stats2;
  document.getElementById('statsTitle1').textContent = d.player1.name;
  document.getElementById('s1count').textContent = s1.count;
  document.getElementById('s1best').textContent = s1.best;
  document.getElementById('s1worst').textContent = s1.worst;
  document.getElementById('s1mean').textContent = s1.mean;
  document.getElementById('s1std').textContent = s1.std;

  document.getElementById('statsTitle2').textContent = d.player2.name;
  document.getElementById('s2count').textContent = s2.count;
  document.getElementById('s2best').textContent = s2.best;
  document.getElementById('s2worst').textContent = s2.worst;
  document.getElementById('s2mean').textContent = s2.mean;
  document.getElementById('s2std').textContent = s2.std;

  document.getElementById('resultCard').classList.add('show');
}

// Allow Enter key to trigger calculation
document.addEventListener('keydown', function(e) {
  if (e.key === 'Enter') doCalc();
});
</script>
</body>
</html>"""


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

        if not id1 or not id2:
            self._send_response(400, {"error": "Both id1 and id2 are required."})
            return
        if event not in EVENT_MAP:
            self._send_response(400, {"error": f"Invalid event. Supported: {', '.join(EVENT_MAP.keys())}"})
            return
        if value_type not in ("single", "average"):
            self._send_response(400, {"error": "type must be 'single' or 'average'."})
            return

        try:
            result = compute_h2h(id1, id2, event, value_type)
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

import math
import random
import threading

from api import fetch_wca_person, extract_times
from const import EVENT_MAP


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


def calc_stats(times: list) -> dict | None:
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

import math
import random
import time
import threading
from collections.abc import Callable

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


def calc_stats(times: list) -> dict:
    """Calculate statistics from a list of times (in centiseconds)."""
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


def format_stats(stats: dict) -> dict:
    return {k: (format_time(v) if k != "count" else v)
            for k, v in stats.items()}


def monte_carlo_winrate(rng1: Callable[[], int | float],
                        rng2: Callable[[], int | float],
                        simulations: int = 50000) -> dict:
    """Estimate H2H win rate using Monte Carlo simulation.

        Models each competitor's performance as a random number generator.
        It could be a normal distribution or random choice from historical results.
        """
    wins1 = 0
    wins2 = 0
    draws = 0
    for _ in range(simulations):
        t1 = rng1()
        t2 = rng2()
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
    }


def _compute_single_ao5(rng: Callable[[], int | float],
                        known: list) -> float | None:
    """Compute ao5 for one simulation run.

    known: list of 5 values — float (centiseconds), None (unknown), or
           float('inf') (DNF). Unknown slots are filled via rng().
    Returns ao5 in centiseconds, or None if DNF (2+ DNFs or DNF in middle 3).
    """
    times = []
    dnf_count = 0
    for k in known:
        if k is None:
            times.append(rng())
        elif k == float('inf'):
            times.append(float('inf'))
            dnf_count += 1
        else:
            times.append(k)

    if dnf_count >= 2:
        return None

    sorted_times = sorted(times)
    middle = sorted_times[1:4]
    if any(t == float('inf') for t in middle):
        return None

    return sum(middle) / 3.0


def simulate_ao5(rng1: Callable[[], int | float],
                 rng2: Callable[[], int | float],
                 p1_known: list,
                 p2_known: list,
                 simulations: int = 50000) -> dict:
    """Estimate AO5 win rate via Monte Carlo simulation.

    Each player has 5 attempt slots. Known values are fixed; unknown slots
    are filled by the RNG each iteration. The ao5 (avg of middle 3 after
    dropping best/worst) is compared. Lower ao5 wins.
    """
    wins1 = 0
    wins2 = 0
    draws = 0
    for _ in range(simulations):
        ao5_1 = _compute_single_ao5(rng1, p1_known)
        ao5_2 = _compute_single_ao5(rng2, p2_known)

        if ao5_1 is None and ao5_2 is None:
            draws += 1
        elif ao5_1 is None:
            wins2 += 1
        elif ao5_2 is None:
            wins1 += 1
        elif ao5_1 < ao5_2:
            wins1 += 1
        elif ao5_2 < ao5_1:
            wins2 += 1
        else:
            draws += 1

    return {
        "player1_winrate": wins1 / simulations,
        "player2_winrate": wins2 / simulations,
        "draw_rate": draws / simulations,
    }


def normal_distribution(stats: dict) -> Callable[[], int | float]:
    return lambda: random.gauss(stats["mean"], stats["std"])


def random_historical(times: list) -> Callable[[], int | float]:
    return lambda: random.choice(times)


def compute_h2h(id1: str, id2: str, event_id: str,
                value_type: str = "single",
                rng_type: str = "normal",
                since_year: int = None,
                mode: str = "h2h",
                p1_attempts: list = None,
                p2_attempts: list = None) -> dict:
    """Main function: fetch data, compute, return result.

    mode: "h2h" (traditional head-to-head) or "ao5" (average of 5).
    p1_attempts / p2_attempts: for ao5 mode, list of 5 strings like
      ["7.52", "", "DNF", "", ""]. Empty string = unknown.
    """
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

    # For AO5 mode, always use single solves for the RNG
    extract_type = "single" if mode == "ao5" else value_type

    times1 = extract_times(person1.get("results", {}), event_id,
                           extract_type, since_year)
    times2 = extract_times(person2.get("results", {}), event_id,
                           extract_type, since_year)

    if not times1:
        return {"error": f"{name1} has no valid results for "
                         f"{EVENT_MAP.get(event_id, event_id)} ({extract_type})."}
    if not times2:
        return {"error": f"{name2} has no valid results for "
                         f"{EVENT_MAP.get(event_id, event_id)} ({extract_type})."}

    stats1, stats2 = calc_stats(times1), calc_stats(times2)
    if rng_type == "normal":
        rng1, rng2 = normal_distribution(stats1), normal_distribution(stats2)
    elif rng_type == "history":
        rng1, rng2 = random_historical(times1), random_historical(times2)
    else:
        return {"error": f"{rng_type} is not a valid RNG type."}

    if mode == "ao5":
        # Parse attempt strings → centiseconds/None/inf
        def parse_attempts(raw):
            result = []
            for v in raw:
                v = v.strip()
                if v == "":
                    result.append(None)
                elif v.upper() == "DNF":
                    result.append(float('inf'))
                else:
                    try:
                        result.append(round(float(v) * 100))
                    except ValueError:
                        result.append(None)
            return result

        p1k = parse_attempts(p1_attempts) if p1_attempts else [None]*5
        p2k = parse_attempts(p2_attempts) if p2_attempts else [None]*5
        result = simulate_ao5(rng1, rng2, p1k, p2k)
    else:
        result = monte_carlo_winrate(rng1, rng2)

    if not result:
        return {"error": "Could not calculate win rate."}

    result["stats1"] = format_stats(stats1)
    result["stats2"] = format_stats(stats2)

    return {
        "player1": {"id": id1, "name": name1, "country": country1},
        "player2": {"id": id2, "name": name2, "country": country2},
        "event": EVENT_MAP.get(event_id, event_id),
        "event_id": event_id,
        "type": extract_type,
        "mode": mode,
        "since_year": since_year,
        "winrate": result,
    }

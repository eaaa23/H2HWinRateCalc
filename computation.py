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


def normal_distribution(stats: dict) -> Callable[[], int | float]:
    return lambda: random.gauss(stats["mean"], stats["std"])


def random_historical(times: list) -> Callable[[], int | float]:
    return lambda: random.choice(times)


def compute_h2h(id1: str, id2: str, event_id: str, value_type: str,
                rng_type: str,
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

    stats1, stats2 = calc_stats(times1), calc_stats(times2)
    if rng_type == "normal":
        rng1, rng2 = normal_distribution(stats1), normal_distribution(stats2)
    elif rng_type == "history":
        rng1, rng2 = random_historical(times1), random_historical(times2)
    else:
        return {"error": f"{rng_type} is not a valid RNG type."}

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
        "type": value_type,
        "since_year": since_year,
        "winrate": result,
    }

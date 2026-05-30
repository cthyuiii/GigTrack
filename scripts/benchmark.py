"""
GigTrack — cache performance benchmark (project brief Task 7).

Measures the latency of serving the trending-concerts payload two ways:

  1. UNCACHED — run the multi-join SQL against MySQL every time.
  2. CACHED   — serve the same payload from a Redis string (one GET + json.loads).

This is the demoable "Redis vs MySQL" speedup the README points at, turned
into a reproducible number.

Run (after the stack is up and seeded):

    PYTHONPATH=app python scripts/benchmark.py
    PYTHONPATH=app python scripts/benchmark.py --iterations 500 --city Singapore
"""
import argparse
import json
import statistics
import time

from db import query_all, redis_client

TRENDING_SQL = """
    SELECT c.concert_id, c.title, c.concert_date, c.view_count,
           v.name AS venue, v.city,
           a.name AS headliner, a.genre
    FROM   concerts c
    JOIN   venues  v ON v.venue_id  = c.venue_id
    JOIN   artists a ON a.artist_id = c.headline_artist_id
    WHERE  c.status = 'scheduled'
    ORDER  BY c.concert_date ASC
    LIMIT  20
"""

BENCH_KEY = "bench:trending"


def time_calls(fn, iterations):
    """Return per-call latencies in milliseconds."""
    samples = []
    for _ in range(iterations):
        t0 = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - t0) * 1000.0)
    return samples


def report(label, samples):
    avg = statistics.mean(samples)
    p95 = sorted(samples)[int(len(samples) * 0.95) - 1]
    print(f"{label:10s}  avg={avg:7.3f} ms   p95={p95:7.3f} ms   "
          f"min={min(samples):7.3f} ms   n={len(samples)}")
    return avg


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--iterations", type=int, default=200)
    ap.add_argument("--city", default=None, help="(informational label only)")
    args = ap.parse_args()

    # Warm both paths once.
    payload = query_all(TRENDING_SQL)
    redis_client.setex(BENCH_KEY, 300, json.dumps(payload, default=str))

    print(f"GigTrack cache benchmark — {len(payload)} rows, "
          f"{args.iterations} iterations\n")

    uncached = time_calls(lambda: query_all(TRENDING_SQL), args.iterations)
    cached = time_calls(
        lambda: json.loads(redis_client.get(BENCH_KEY)), args.iterations
    )

    avg_u = report("MySQL", uncached)
    avg_c = report("Redis", cached)

    print(f"\nSpeedup (avg): {avg_u / avg_c:6.1f}x faster from cache")
    redis_client.delete(BENCH_KEY)


if __name__ == "__main__":
    main()

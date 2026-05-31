"""
GigTrack — performance benchmark (project brief Task 7).

Measures several dimensions, not just one, and writes machine-readable output:

  1. MySQL trending query (uncached)      — the real multi-join home query
  2. Redis cached payload                 — GET + json.loads
  3. MySQL indexed filter (status)        — uses idx_concerts_status_date
  4. MySQL UN-indexed filter (base_price) — forces a full scan, for contrast
  5. MongoDB indexed find (concert_id)    — uses the seeded index
  6. MongoDB UN-indexed find (rating)     — no index, collection scan
  7. Payload scaling (LIMIT 5 / 20 / 50)  — latency vs result size

For each it reports avg / p50 / p95 / p99 latency and throughput (ops/sec),
writes scripts/benchmark_results.csv, and (if matplotlib is installed) a bar
chart scripts/benchmark_latency.png. Build a slide from the CSV with
scripts/make_benchmark_slide.py.

Run (stack up + seeded):
    PYTHONPATH=app python scripts/benchmark.py --iterations 300
"""
import argparse
import csv
import json
import os
import statistics
import time

from db import query_all, redis_client, mongo

HERE = os.path.dirname(__file__)
CSV_PATH = os.path.join(HERE, "benchmark_results.csv")
PNG_PATH = os.path.join(HERE, "benchmark_latency.png")

TRENDING_SQL = """
    SELECT c.concert_id, c.title, c.concert_date, c.view_count,
           v.name AS venue, v.city, a.name AS headliner, a.genre
    FROM   concerts c
    JOIN   venues  v ON v.venue_id  = c.venue_id
    JOIN   artists a ON a.artist_id = c.headline_artist_id
    WHERE  c.status = 'scheduled'
    ORDER  BY c.concert_date ASC
    LIMIT  {limit}
"""
BENCH_KEY = "bench:trending"


def measure(fn, iterations):
    samples = []
    for _ in range(iterations):
        t0 = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - t0) * 1000.0)
    samples.sort()
    n = len(samples)
    def pct(p):
        return samples[min(n - 1, int(n * p))]
    avg = statistics.mean(samples)
    return {
        "avg_ms":  round(avg, 4),
        "p50_ms":  round(pct(0.50), 4),
        "p95_ms":  round(pct(0.95), 4),
        "p99_ms":  round(pct(0.99), 4),
        "ops_per_sec": round(1000.0 / avg, 1) if avg else 0,
        "iterations": n,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--iterations", type=int, default=300)
    args = ap.parse_args()
    it = args.iterations

    # warm the cache path
    payload = query_all(TRENDING_SQL.format(limit=20))
    redis_client.setex(BENCH_KEY, 300, json.dumps(payload, default=str))

    scenarios = {}
    scenarios["MySQL trending (uncached)"] = measure(
        lambda: query_all(TRENDING_SQL.format(limit=20)), it)
    scenarios["Redis cached"] = measure(
        lambda: json.loads(redis_client.get(BENCH_KEY)), it)
    scenarios["MySQL indexed (status)"] = measure(
        lambda: query_all(
            "SELECT concert_id FROM concerts WHERE status='scheduled' "
            "ORDER BY concert_date LIMIT 20"), it)
    scenarios["MySQL unindexed (base_price)"] = measure(
        lambda: query_all(
            "SELECT concert_id FROM concerts WHERE base_price > 100"), it)
    scenarios["Mongo indexed (concert_id)"] = measure(
        lambda: list(mongo.reviews.find({"concert_id": 6}).limit(20)), it)
    scenarios["Mongo unindexed (rating)"] = measure(
        lambda: list(mongo.reviews.find({"rating": 5}).limit(20)), it)
    for lim in (5, 20, 50):
        scenarios[f"MySQL payload LIMIT {lim}"] = measure(
            lambda lim=lim: query_all(TRENDING_SQL.format(limit=lim)), it)

    redis_client.delete(BENCH_KEY)

    # ---- print ----
    print(f"\nGigTrack benchmark — {it} iterations each\n")
    hdr = f"{'scenario':32s} {'avg':>9s} {'p50':>9s} {'p95':>9s} {'p99':>9s} {'ops/s':>10s}"
    print(hdr); print("-" * len(hdr))
    for name, m in scenarios.items():
        print(f"{name:32s} {m['avg_ms']:9.3f} {m['p50_ms']:9.3f} "
              f"{m['p95_ms']:9.3f} {m['p99_ms']:9.3f} {m['ops_per_sec']:10.1f}")

    # ---- CSV ----
    with open(CSV_PATH, "w", newline="") as fh:
        wtr = csv.writer(fh)
        wtr.writerow(["scenario", "iterations", "avg_ms", "p50_ms",
                      "p95_ms", "p99_ms", "ops_per_sec"])
        for name, m in scenarios.items():
            wtr.writerow([name, m["iterations"], m["avg_ms"], m["p50_ms"],
                          m["p95_ms"], m["p99_ms"], m["ops_per_sec"]])
    print(f"\nWrote {CSV_PATH}")

    # ---- chart (optional) ----
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        names = list(scenarios.keys())
        avgs = [scenarios[n]["avg_ms"] for n in names]
        fig, ax = plt.subplots(figsize=(10, 5))
        ax.barh(names, avgs, color="#ff5266")
        ax.set_xlabel("Average latency (ms, log scale)")
        ax.set_xscale("log")
        ax.set_title("GigTrack — query latency by store / strategy")
        ax.invert_yaxis()
        fig.tight_layout()
        fig.savefig(PNG_PATH, dpi=130)
        print(f"Wrote {PNG_PATH}")
    except Exception as e:
        print(f"(chart skipped: {e})")


if __name__ == "__main__":
    main()

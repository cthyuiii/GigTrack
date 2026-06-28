"""
GigTrack - performance benchmark.

Measures SIX dimensions of database performance, grouped, and writes
machine-readable output. Every scenario reports avg / p50 / p95 / p99 latency,
throughput (ops/sec) and client CPU time per call; the run also reports peak
client memory.

  [reads]    1. MySQL trending query (uncached)   - the real 3-table join
             2. Redis cached payload              - GET + json.loads
             3. MySQL indexed filter (status)     - uses idx_concerts_status_date
             4. MySQL UN-indexed filter           - full scan, for contrast
             5. MongoDB indexed find (concert_id) - uses the seeded index
             6. MongoDB UN-indexed find (rating)  - collection scan
             7. Payload scaling (LIMIT 5/20/50)   - latency vs result size

  [point]    Access-time ladder - the same "fetch one thing by key" op on all
             three stores: MySQL PK lookup, Mongo unique-index find_one,
             Redis GET. Shows why sessions/cache live in Redis.

  [compute]  Server-side computation - work the DB engine does, not the client:
             MySQL GROUP BY revenue join, MySQL window function (RANK per
             city), Mongo $group aggregation, Mongo $lookup join, Mongo $facet
             multi-analytics. Compare cpu/call (client) vs avg (wall): a large
             gap means the server did the heavy lifting.

  [writes]   Write latency per store - Redis SET, Mongo insert_one, MySQL
             INSERT (scratch table), and MySQL INSERT into bookings where the
             BEFORE INSERT trigger also locks + decrements seat inventory.
             The bookings-vs-scratch delta ≈ the cost of the trigger.
             All writes are cleaned up afterwards (scratch table dropped,
             bench bookings deleted and seats restored).

  [txn]      Transaction batching - 50 INSERTs committed once vs 50 INSERTs
             committed individually. Shows per-commit (fsync/roundtrip)
             overhead; latency reported is per 50-row BATCH, not per row.

  [parallel] Concurrent throughput - the trending read hammered by N worker
             threads (default 8), cached vs uncached. ops/s here is aggregate
             across workers; compare with the single-threaded numbers.

Outputs: scripts/benchmark_results.csv and (if matplotlib is installed)
scripts/benchmark_latency.png.

Run (stack up + seeded):
    PYTHONPATH=app python scripts/benchmark.py --iterations 300
    PYTHONPATH=app python scripts/benchmark.py --iterations 300 --workers 16
    PYTHONPATH=app python scripts/benchmark.py --skip-writes   # read-only run
"""
import argparse
import csv
import json
import os
import statistics
import threading
import time

import pymysql

from db import query_all, get_mysql, redis_client, mongo, MYSQL_CONFIG

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

GROUP_ORDER = ["reads", "pool", "point", "compute", "writes", "txn", "parallel"]


# ---------------------------------------------------------------------------
# measurement helpers
# ---------------------------------------------------------------------------

def _stats(samples, wall_s=None):
    samples = sorted(samples)
    n = len(samples)
    def pct(p):
        return samples[min(n - 1, int(n * p))]
    avg = statistics.mean(samples)
    # ops/s: from total wall time when given (concurrent runs), else from avg.
    ops = (n / wall_s) if wall_s else (1000.0 / avg if avg else 0)
    return {
        "avg_ms": round(avg, 4), "p50_ms": round(pct(0.50), 4),
        "p95_ms": round(pct(0.95), 4), "p99_ms": round(pct(0.99), 4),
        "ops_per_sec": round(ops, 1), "iterations": n,
    }


def measure(fn, iterations):
    """Sequential: run fn `iterations` times, return latency + CPU stats."""
    samples = []
    cpu0 = time.process_time()        # CPU time consumed by THIS process
    for _ in range(iterations):
        t0 = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - t0) * 1000.0)
    cpu_total_ms = (time.process_time() - cpu0) * 1000.0
    out = _stats(samples)
    # CPU ms per call: processor time (not wall time) each call cost the
    # client. Wall >> CPU means the call mostly WAITED on the DB/network;
    # wall ≈ CPU means the client itself did the computing.
    out["cpu_ms"] = round(cpu_total_ms / len(samples), 4) if samples else 0
    return out


def measure_concurrent(fn, total_calls, workers):
    """Parallel: run fn `total_calls` times across `workers` threads.
    Latency percentiles are per-call; ops/s is aggregate throughput."""
    from concurrent.futures import ThreadPoolExecutor
    samples = []
    lock = threading.Lock()

    def task(_):
        t0 = time.perf_counter()
        fn()
        dt = (time.perf_counter() - t0) * 1000.0
        with lock:
            samples.append(dt)

    cpu0 = time.process_time()
    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(task, range(total_calls)))
    wall = time.perf_counter() - t0
    cpu_total_ms = (time.process_time() - cpu0) * 1000.0
    out = _stats(samples, wall_s=wall)
    out["cpu_ms"] = round(cpu_total_ms / len(samples), 4) if samples else 0
    return out


def _resource_usage():
    """(avg_cpu_percent_or_None, peak_rss_MB). Uses psutil if installed,
    otherwise falls back to the stdlib resource module (Unix)."""
    try:
        import psutil
        p = psutil.Process()
        p.cpu_percent(None)            # prime the measurement
        cpu = p.cpu_percent(0.3)
        return cpu, p.memory_info().rss / (1024 * 1024)
    except Exception:
        try:
            import resource, sys as _s
            ru = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            # ru_maxrss: bytes on macOS, kilobytes on Linux.
            rss_mb = ru / (1024 * 1024) if _s.platform == "darwin" else ru / 1024
            return None, rss_mb
        except Exception:
            return None, 0.0


# ---------------------------------------------------------------------------
# scenario groups
# ---------------------------------------------------------------------------

def read_scenarios(it):
    out = {}
    out["MySQL trending (uncached)"] = measure(
        lambda: query_all(TRENDING_SQL.format(limit=20)), it)
    out["Redis cached"] = measure(
        lambda: json.loads(redis_client.get(BENCH_KEY)), it)
    out["MySQL indexed (status)"] = measure(
        lambda: query_all(
            "SELECT concert_id FROM concerts WHERE status='scheduled' "
            "ORDER BY concert_date LIMIT 20"), it)
    out["MySQL unindexed (base_price)"] = measure(
        lambda: query_all(
            "SELECT concert_id FROM concerts WHERE base_price > 100"), it)
    out["Mongo indexed (concert_id)"] = measure(
        lambda: list(mongo.reviews.find({"concert_id": 6}).limit(20)), it)
    out["Mongo unindexed (rating)"] = measure(
        lambda: list(mongo.reviews.find({"rating": 5}).limit(20)), it)
    for lim in (5, 20, 50):
        out[f"MySQL payload LIMIT {lim}"] = measure(
            lambda lim=lim: query_all(TRENDING_SQL.format(limit=lim)), it)
    return out


def pool_scenarios(it):
    """Connection pooling impact: a fresh connection per call vs a pooled one.
    Same trending query both ways, so the delta is the per-call connect cost
    (TCP + auth handshake) that PooledDB removes."""
    sql = TRENDING_SQL.format(limit=20)

    def fresh():                       # the old behaviour: connect-per-call
        conn = pymysql.connect(**MYSQL_CONFIG)
        try:
            with conn.cursor() as cur:
                cur.execute(sql); cur.fetchall()
        finally:
            conn.close()
    out = {}
    out["MySQL read, new connection per call"] = measure(fresh, it)
    out["MySQL read, pooled connection"] = measure(lambda: query_all(sql), it)
    return out


def point_lookup_scenarios(it):
    """Same logical op - fetch ONE record by its key - on each store."""
    redis_client.setex("bench:point", 300, "42")
    out = {}
    out["MySQL point (PRIMARY KEY)"] = measure(
        lambda: query_all("SELECT * FROM concerts WHERE concert_id = 6"), it)
    out["Mongo point (unique index)"] = measure(
        lambda: mongo.artist_bios.find_one({"artist_id": 6}), it)
    out["Redis point (GET)"] = measure(
        lambda: redis_client.get("bench:point"), it)
    redis_client.delete("bench:point")
    return out


def compute_scenarios(it):
    """Server-side computation: aggregation / window / document joins.
    cpu/call vs avg shows the work happened in the DB engine, not the client."""
    out = {}
    out["MySQL GROUP BY (revenue join)"] = measure(
        lambda: query_all("""
            SELECT c.concert_id, COALESCE(SUM(b.total_price), 0) AS revenue
            FROM   concerts c
            LEFT JOIN tickets  t ON t.concert_id = c.concert_id
            LEFT JOIN bookings b ON b.ticket_id  = t.ticket_id
                                AND b.status = 'confirmed'
            GROUP  BY c.concert_id"""), it)
    out["MySQL window (RANK per city)"] = measure(
        lambda: query_all("""
            SELECT * FROM (
                SELECT v.city, c.concert_id,
                       COALESCE(SUM(b.total_price), 0) AS revenue,
                       RANK() OVER (PARTITION BY v.city
                                    ORDER BY COALESCE(SUM(b.total_price), 0) DESC) rk
                FROM concerts c
                JOIN venues v ON v.venue_id = c.venue_id
                LEFT JOIN tickets  t ON t.concert_id = c.concert_id
                LEFT JOIN bookings b ON b.ticket_id  = t.ticket_id
                                    AND b.status = 'confirmed'
                GROUP BY v.city, c.concert_id) r
            WHERE rk <= 3"""), it)
    out["Mongo $group (avg rating)"] = measure(
        lambda: list(mongo.reviews.aggregate([
            {"$group": {"_id": "$concert_id",
                        "avg_rating": {"$avg": "$rating"},
                        "n": {"$sum": 1}}}])), it)
    out["Mongo $lookup (reviews+setlists)"] = measure(
        lambda: list(mongo.reviews.aggregate([
            {"$match": {"rating": {"$gte": 4}}},
            {"$lookup": {"from": "setlists", "localField": "concert_id",
                         "foreignField": "concert_id", "as": "setlist"}},
            {"$limit": 20}])), it)
    out["Mongo $facet (dashboard)"] = measure(
        lambda: list(mongo.reviews.aggregate([
            {"$facet": {
                "by_rating": [{"$group": {"_id": "$rating", "n": {"$sum": 1}}}],
                "top_tags":  [{"$unwind": "$tags"},
                              {"$group": {"_id": "$tags", "n": {"$sum": 1}}},
                              {"$sort": {"n": -1}}, {"$limit": 5}]}}])), it)
    return out


def write_scenarios(it):
    """Write latency per store + MySQL trigger overhead. Cleans up after
    itself: scratch table dropped, bench bookings removed, seats restored."""
    wit = min(it, 200)                 # cap write volume
    out = {}

    # Redis SET - in-memory write.
    out["Redis write (SET)"] = measure(
        lambda: redis_client.set("bench:w", "x" * 64), wit)
    redis_client.delete("bench:w")

    # Mongo insert_one - journalled document write into a scratch collection.
    bench_coll = mongo["bench_writes"]
    out["Mongo write (insert_one)"] = measure(
        lambda: bench_coll.insert_one({"payload": "x" * 64}), wit)
    bench_coll.drop()

    # MySQL writes share ONE connection (like Redis/Mongo reuse their client),
    # so we time the statement + commit, not TCP/auth setup.
    with get_mysql() as conn, conn.cursor() as cur:
        cur.execute("""CREATE TABLE IF NOT EXISTS bench_writes (
                           id INT AUTO_INCREMENT PRIMARY KEY,
                           payload VARCHAR(64) NOT NULL)""")
        conn.commit()

        def mysql_plain_insert():
            cur.execute("INSERT INTO bench_writes (payload) VALUES (%s)",
                        ("x" * 64,))
            conn.commit()
        out["MySQL write (plain INSERT)"] = measure(mysql_plain_insert, wit)

        # INSERT into bookings: the BEFORE INSERT trigger locks the ticket
        # row, checks availability, and decrements inventory - the delta vs
        # the plain INSERT above approximates the trigger's cost.
        cur.execute("SELECT ticket_id, available_seats FROM tickets "
                    "ORDER BY available_seats DESC LIMIT 1")
        tk = cur.fetchone()
        if tk and tk["available_seats"] > wit + 5:
            inserted = []

            def mysql_trigger_insert():
                cur.execute(
                    "INSERT INTO bookings (user_id, ticket_id, quantity, "
                    "total_price) VALUES (2, %s, 1, 0.00)", (tk["ticket_id"],))
                inserted.append(cur.lastrowid)
                conn.commit()
            out["MySQL write (INSERT + trigger)"] = measure(
                mysql_trigger_insert, wit)

            # Cleanup: remove bench bookings and hand the seats back.
            cur.execute("DELETE FROM bookings WHERE booking_id IN ({})".format(
                ",".join(["%s"] * len(inserted))), inserted)
            cur.execute("UPDATE tickets SET available_seats = "
                        "available_seats + %s WHERE ticket_id = %s",
                        (len(inserted), tk["ticket_id"]))
            conn.commit()
        else:
            print("(skipped trigger-write scenario: not enough free seats)")

        cur.execute("DROP TABLE IF EXISTS bench_writes")
        conn.commit()
    return out


def txn_scenarios(it):
    """Commit batching: 50 INSERTs per commit vs a commit per INSERT.
    NOTE: latency shown is per 50-row BATCH, not per row."""
    batches = max(5, min(30, it // 10))
    out = {}
    with get_mysql() as conn, conn.cursor() as cur:
        cur.execute("""CREATE TABLE IF NOT EXISTS bench_writes (
                           id INT AUTO_INCREMENT PRIMARY KEY,
                           payload VARCHAR(64) NOT NULL)""")
        conn.commit()

        def batched():               # 50 inserts, ONE fsync/commit
            for _ in range(50):
                cur.execute("INSERT INTO bench_writes (payload) VALUES ('x')")
            conn.commit()

        def per_statement():         # 50 inserts, 50 commits
            for _ in range(50):
                cur.execute("INSERT INTO bench_writes (payload) VALUES ('x')")
                conn.commit()

        out["MySQL 50 INSERTs / 1 commit"] = measure(batched, batches)
        out["MySQL 50 INSERTs / 50 commits"] = measure(per_statement, batches)

        cur.execute("DROP TABLE IF EXISTS bench_writes")
        conn.commit()
    return out


def parallel_scenarios(it, workers):
    """Aggregate throughput under concurrency - ops/s across all workers."""
    out = {}
    out[f"Redis cached x{workers} workers"] = measure_concurrent(
        lambda: json.loads(redis_client.get(BENCH_KEY)), it, workers)
    out[f"MySQL trending x{workers} workers"] = measure_concurrent(
        lambda: query_all(TRENDING_SQL.format(limit=20)), it, workers)
    return out


# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--iterations", type=int, default=300)
    ap.add_argument("--workers", type=int, default=8,
                    help="threads for the concurrency scenarios")
    ap.add_argument("--skip-writes", action="store_true",
                    help="skip the write/txn groups (read-only benchmark)")
    args = ap.parse_args()
    it = args.iterations

    # warm the cache path
    payload = query_all(TRENDING_SQL.format(limit=20))
    redis_client.setex(BENCH_KEY, 600, json.dumps(payload, default=str))

    groups = {"reads": read_scenarios(it),
              "pool": pool_scenarios(it),
              "point": point_lookup_scenarios(it),
              "compute": compute_scenarios(it)}
    if not args.skip_writes:
        groups["writes"] = write_scenarios(it)
        groups["txn"] = txn_scenarios(it)
    groups["parallel"] = parallel_scenarios(it, args.workers)

    redis_client.delete(BENCH_KEY)

    # ---- resource usage snapshot ----
    cpu_pct, rss_mb = _resource_usage()

    # ---- print ----
    print(f"\nGigTrack benchmark - {it} iterations each "
          f"({args.workers} workers for [parallel])\n")
    hdr = (f"{'scenario':34s} {'avg':>9s} {'p50':>9s} {'p95':>9s} "
           f"{'p99':>9s} {'cpu/call':>9s} {'ops/s':>10s}")
    for gname in GROUP_ORDER:
        if gname not in groups:
            continue
        print(f"[{gname}]")
        print(hdr); print("-" * len(hdr))
        for name, m in groups[gname].items():
            print(f"{name:34s} {m['avg_ms']:9.3f} {m['p50_ms']:9.3f} "
                  f"{m['p95_ms']:9.3f} {m['p99_ms']:9.3f} {m['cpu_ms']:9.4f} "
                  f"{m['ops_per_sec']:10.1f}")
        print()
    print("[txn] latencies are per 50-row batch; [parallel] ops/s is "
          "aggregate across workers.")
    print(f"Client process: peak memory ≈ {rss_mb:.1f} MB"
          + (f", avg CPU ≈ {cpu_pct:.1f}%" if cpu_pct is not None else ""))

    # ---- CSV ----
    with open(CSV_PATH, "w", newline="") as fh:
        wtr = csv.writer(fh)
        wtr.writerow(["group", "scenario", "iterations", "avg_ms", "p50_ms",
                      "p95_ms", "p99_ms", "cpu_ms", "ops_per_sec"])
        for gname in GROUP_ORDER:
            for name, m in groups.get(gname, {}).items():
                wtr.writerow([gname, name, m["iterations"], m["avg_ms"],
                              m["p50_ms"], m["p95_ms"], m["p99_ms"],
                              m["cpu_ms"], m["ops_per_sec"]])
        wtr.writerow([])
        wtr.writerow(["peak_rss_mb", round(rss_mb, 1)])
        if cpu_pct is not None:
            wtr.writerow(["avg_cpu_percent", round(cpu_pct, 1)])
    print(f"Wrote {CSV_PATH}")

    # ---- chart (optional) ----
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        colors = {"reads": "#ff5266", "pool": "#1f6feb", "point": "#ffb02e",
                  "compute": "#7c5cff", "writes": "#2ec4b6", "txn": "#3a86ff",
                  "parallel": "#8d99ae"}
        names, avgs, cols = [], [], []
        for gname in GROUP_ORDER:
            for name, m in groups.get(gname, {}).items():
                names.append(name); avgs.append(m["avg_ms"])
                cols.append(colors[gname])
        fig, ax = plt.subplots(figsize=(10, max(5, 0.32 * len(names))))
        ax.barh(names, avgs, color=cols)
        ax.set_xlabel("Average latency (ms, log scale) - txn rows are per 50-row batch")
        ax.set_xscale("log")
        ax.set_title("GigTrack - latency by store / strategy")
        ax.invert_yaxis()
        from matplotlib.patches import Patch
        ax.legend(handles=[Patch(color=c, label=g) for g, c in colors.items()
                           if g in groups], loc="lower right", fontsize=8)
        fig.tight_layout()
        fig.savefig(PNG_PATH, dpi=130)
        print(f"Wrote {PNG_PATH}")
    except Exception as e:
        print(f"(chart skipped: {e})")


if __name__ == "__main__":
    main()

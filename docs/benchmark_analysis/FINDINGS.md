# GigTrack - Benchmark Findings

Analysis of `scripts/benchmark_results.csv` (300 iterations/scenario, 8 worker
threads for `[parallel]`; client peak memory ≈ **52.4 MB**). Charts in this
folder: `bench_overview.png` (all six groups), `bench_point_ladder.png`,
`bench_txn_batching.png`.

> Note: `avg_cpu_percent = 0.0` in the CSV is a sampling artifact (psutil takes
> a 0.3 s reading on the idle process *after* the run); use the per-call `cpu_ms`
> column for CPU, and `peak_rss_mb` for memory.

## Headline numbers

| Group | Result | Take-away |
|---|---|---|
| reads | Redis cached **0.134 ms** vs MySQL uncached join **8.94 ms** | Cache is **~67× faster** (7,460 vs 112 ops/s) |
| reads | MySQL indexed 8.58 ms vs full-scan 8.32 ms; Mongo idx 0.246 vs scan 0.254 ms | **No measurable index benefit at seed scale** |
| reads | LIMIT 5 / 20 / 50 = 8.52 / 8.83 / 8.90 ms | Latency is **per-roundtrip, not per-row** (10× rows → +4%) |
| point | Redis 0.102 ms · Mongo 0.235 ms · MySQL PK **8.34 ms** | Redis **81×**, Mongo **35×** faster than the MySQL PK lookup |
| compute | Mongo `$facet` cpu 0.13 ms ≪ wall 0.44 ms; `$lookup` 0.76 ms (priciest Mongo op) | Mongo: the **engine** did the work, not the client |
| writes | plain INSERT 2.30 ms → INSERT+trigger **2.83 ms** | Seat trigger costs **+0.53 ms (+23%)** per booking |
| txn (MySQL only) | 1 commit/50 rows 12.9 ms (0.258 ms/row) vs 50 commits 144.7 ms (2.893 ms/row) | Batching is **~11× cheaper**; ~2.69 ms per extra fsync |
| parallel | Redis 7,460 → 3,021 ops/s; MySQL 112 → 382 ops/s (1→8 threads) | MySQL **scales ~3.4×**; Redis **drops** under Python threads |

## The interesting / surprising findings

**1. "MySQL is slow" is really a connection-pooling story, not a query story.**
The 3-table trending join (8.94 ms) and a single-row primary-key lookup
(8.34 ms) cost almost the same - and both match the indexed/unindexed/payload
reads (~8.3–8.9 ms). The actual query work is sub-millisecond; the ~8 ms floor
is **opening a fresh MySQL connection on every call** (`get_mysql()` connects
per request - no pool). Proof: the write scenarios reuse one connection and a
plain INSERT is only **2.30 ms**, so ≈ 6 ms of every read is connection setup.
A connection pool would close most of the gap to Redis/Mongo. *(This is the
strongest discussion point - and an easy, honest "future work" item.)*

**2. The cache win is much bigger than we'd claimed (~67×, not ~16–30×).**
With both stores warm and on this machine, Redis serves the trending payload
0.134 ms vs 8.94 ms uncached. Because part of that gap is MySQL's
connect-per-call (point 1), the cache is doing two jobs at once: skipping the
join *and* skipping a connection.

**3. Indexing shows no measurable benefit at seed volume - on either store.**
At 50 concerts / 220 reviews the optimiser scans either way, so indexed and
full-scan times are within noise (and the scan is even marginally faster on
MySQL). This is expected and worth stating honestly: to *demonstrate* the index
value, either enlarge the dataset or show the `EXPLAIN` plan difference rather
than wall-clock time.

**4. Concurrency is counter-intuitive: Redis throughput falls, MySQL rises.**
From 1 → 8 threads, MySQL aggregate throughput scales ~3.4× (each thread opens
its own connection, so DB I/O-wait overlaps), while Redis **drops** from 7,460
to 3,021 ops/s. The Redis path is bottlenecked on the Python **GIL** (JSON
parsing) and a single shared client, which threads can't parallelise - they add
contention instead. Redis is still 7.9× MySQL's throughput and 8.5× lower
per-call latency under load, but it doesn't scale with Python threads. *(A
process pool or pipelining, not threads, is the way to scale the cache path.)*

**5. The seat trigger is cheap and now precisely priced.** Integrity (lock the
ticket row, check availability, decrement inventory) costs **+0.53 ms / +23%**
on top of a plain INSERT - a clear, defensible trade-off for correctness.

**6. Commit batching is the classic 11× durability result.** This compares two
strategies on the **same MySQL table** (not two different databases): fifty
individual commits cost 144.7 ms vs 12.9 ms for one batched commit - each extra
commit ≈ 2.69 ms of fsync/roundtrip. Justifies the multi-row INSERTs in
`generate_seed.py`.

## Caveats for the write-up
- All numbers are single-machine, warm-cache, localhost - relative ratios are
  the story, not absolute ms.
- The `[parallel]` GIL effect is a property of the Python client, not Redis.
- Indexing/“payload scaling” conclusions are scale-dependent (seed data is small).

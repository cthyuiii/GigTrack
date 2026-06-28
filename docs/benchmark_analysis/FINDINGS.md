# GigTrack - Benchmark Findings

Analysis of `scripts/benchmark_results.csv` (300 iterations/scenario, 8 worker
threads for `[parallel]`; client peak memory ≈ **69.7 MB**). This run was taken
**after enabling MySQL connection pooling** (DBUtils `PooledDB` in `app/db.py`).
Charts in this folder: `bench_overview.png` (main groups),
`bench_point_ladder.png`, `bench_txn_batching.png`, and the pooling visuals
`bench_pool.png` and `pooling_flow.png`.

> Note: `avg_cpu_percent = 0.0` in the CSV is a sampling artifact (psutil takes
> a 0.3 s reading on the idle process *after* the run); use the per-call `cpu_ms`
> column for CPU, and `peak_rss_mb` for memory.

## Headline numbers

| Group | Result | Take-away |
|---|---|---|
| reads | Redis cached **1.14 ms** vs MySQL uncached join **3.99 ms** | Cache is **~3.5x faster** (881 vs 250 ops/s) |
| pool | MySQL read, new connection **17.24 ms** vs pooled **5.00 ms** | Pooling is **~3.4x faster**, ~12 ms saved per call |
| reads | MySQL indexed 4.23 ms vs full-scan 3.95 ms; Mongo idx 1.14 vs scan 1.49 ms | **No measurable index benefit at seed scale** |
| reads | LIMIT 5 / 20 / 50 = 4.03 / 4.37 / 4.60 ms | Latency is **per-roundtrip, not per-row** (10x rows -> +14%) |
| point | Redis 1.16 ms . Mongo 1.29 ms . MySQL PK **4.64 ms** | Redis **~4x**, Mongo **~3.6x** faster than the MySQL PK lookup |
| compute | Mongo `$facet` 1.54 ms, `$lookup` 2.41 ms (priciest Mongo op); MySQL GROUP BY 5.02 ms, window 4.71 ms | Server-side compute is cheap on both stores |
| writes | plain INSERT 4.45 ms -> INSERT+trigger **4.98 ms** | Seat trigger costs **+0.53 ms (+12%)** per booking |
| txn (MySQL only) | 1 commit/50 rows 67.26 ms (1.35 ms/row) vs 50 commits 231.35 ms (4.63 ms/row) | Batching is **~3.4x cheaper**; ~3.3 ms per extra fsync |
| parallel | Redis 881 -> 2,016 ops/s; MySQL 250 -> 222 ops/s (1->8 threads) | Redis **scales ~2.3x**; pooled MySQL stays **flat** |

## The interesting / surprising findings

**1. Connection pooling was the single highest-impact change.** The `[pool]`
group isolates the connection layer by running the *same* read with a fresh
`pymysql.connect` on every call versus a connection borrowed from the pool:
**17.24 ms drops to 5.00 ms (~3.4x), about 12 ms saved per call**. That ~12 ms
was pure TCP + authentication handshake. We implemented the pool (DBUtils
`PooledDB` wrapping PyMySQL, `app/db.py`) and re-ran the suite to prove it; this
is why every relational read in this run is far quicker than in our earlier,
unpooled numbers. See `pooling_flow.png` / `bench_pool.png`.

**2. With pooling on, the cache win is real but moderate (~3.5x, not ~67x).**
Once the per-call connection cost is removed, the warm Redis read (1.14 ms) is
about 3.5x faster than the uncached three-table MySQL join (3.99 ms) - the cache
now buys you the *join work*, not a hidden connection. The earlier "~67x"
headline was mostly the connect-per-call penalty, which pooling has eliminated.

**3. Indexing shows no measurable benefit at seed volume - on either store.**
At 50 concerts / 220 reviews the optimiser scans either way, so indexed and
full-scan times are within noise (MySQL indexed 4.23 vs scan 3.95 ms). To
*demonstrate* index value, enlarge the dataset or show the `EXPLAIN` plan
difference rather than wall-clock time.

**4. Concurrency: Redis scales, pooled MySQL stays flat.** From 1 -> 8 threads,
Redis aggregate throughput rises ~2.3x (881 -> 2,016 ops/s) while pooled MySQL
is essentially flat (250 -> 222 ops/s): the pool caps the number of concurrent
connections and Python's **GIL** serialises client-side parsing, so neither
path scales linearly with threads. Redis is still far faster per call. *(A
process pool or pipelining, not threads, is the way to scale further.)*

**5. The seat trigger is cheap and precisely priced.** Integrity (lock the
ticket row, check availability, decrement inventory) costs **+0.53 ms / +12%**
on top of a plain INSERT - a clear, defensible trade-off for correctness.

**6. Commit batching is a classic durability result.** Comparing two strategies
on the **same MySQL table** (not two databases): fifty individual commits cost
231.35 ms (4.63 ms/row) vs 67.26 ms (1.35 ms/row) for one batched commit -
about **3.4x cheaper**, each extra commit ~3.3 ms of fsync/roundtrip. Justifies
the multi-row INSERTs in `generate_seed.py`.

## Caveats for the write-up
- All numbers are single-machine, warm-cache, localhost - relative ratios are
  the story, not absolute ms.
- The `[parallel]` GIL effect is a property of the Python client, not Redis.
- Indexing / payload-scaling conclusions are scale-dependent (seed data is small).

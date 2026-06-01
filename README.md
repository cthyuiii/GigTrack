# GigTrack

INF2003 group project scaffold — a live-music & concert companion that uses
three datastores intentionally:

- **MySQL** for transactional data (users, artists, venues, concerts, tickets,
  bookings, follows) — clear relational integrity, referential constraints,
  and a trigger that decrements seat inventory atomically on booking.
- **MongoDB** for variable-shape data (setlists, reviews with embedded photos
  and tags, artist bios) — natural fit for documents.
- **Redis** for caching trending lists, search autocomplete, view counters,
  and session tokens.
- **MinIO** (S3-compatible object storage) for review photo *blobs* — the
  database keeps only a pointer (`photos[].url` / `key`); the bytes live in a
  bucket. Swap the endpoint for AWS S3 / Cloudflare R2 with no code change.

## Ports (host → container)

`docker-compose` deliberately remaps the host ports so they don't clash with
anything already running locally. **The app is on `5001`, not 5000.**

| Service | In browser / from host | Inside the compose network |
|---|---|---|
| **App (Flask)** | <http://localhost:5001> | `app:5000` |
| MySQL | `localhost:3307` | `mysql:3306` |
| MongoDB | `localhost:27018` | `mongo:27017` |
| Redis | `localhost:6380` | `redis:6379` |
| MinIO API | `localhost:9000` | `minio:9000` |
| MinIO Console | <http://localhost:9001> (`minioadmin`/`minioadmin`) | `minio:9001` |

## Quick start — all in Docker (recommended)

No `.env` needed: `docker-compose.yml` injects every variable into the app
container.

```bash
docker compose up --build
# First boot takes ~1–2 min: MySQL seeds, Mongo seeds, and artist images
# are generated. Then open:
open http://localhost:5001
```

> **Artist photos.** On boot `scripts/fetch_artist_images.py` downloads a real
> placeholder portrait for each of the 30 artists (from Pravatar — a free set of
> ~70 portrait avatars; no API key, no rate limit) and uploads it to MinIO, so
> the bytes are served from object storage. It finishes in a couple of seconds.
> If an artist's photo can't be fetched it just keeps the gradient placeholder.
> Re-run any time without a reboot:
> `docker compose exec app python scripts/fetch_artist_images.py --force`

> **Changed the schema or seed?** MySQL only runs `schema.sql`/`seed.sql` on a
> *fresh* data volume, so a plain `--build` keeps the OLD data (this is what
> caused stale concerts + login errors). Reset the volumes to reseed:
>
> ```bash
> docker compose down -v && docker compose up --build
> ```

Sample logins (password is `password` for everyone):

- **Admin:** `macc@example.com` / `password` → Admin dashboard at `/admin`
- Regular users: `user02@example.com` … `user21@example.com`

## Quick start — local Flask, datastores in Docker

Run the four datastores in Docker (they auto-seed), but run Flask on your
machine for faster iteration.

```bash
# 1. Start ONLY the datastores (MySQL auto-loads schema.sql + seed.sql).
docker compose up -d mysql mongo redis minio

# 2. Create your .env pointing at the remapped host ports above.
cp .env.example .env
```

Edit `.env` so the host ports match the table above:

```ini
MYSQL_HOST=localhost
MYSQL_PORT=3307
MYSQL_USER=gigtrack
MYSQL_PASSWORD=gigtrack_pw
MYSQL_DB=gigtrack
MONGO_URI=mongodb://localhost:27018
REDIS_URL=redis://localhost:6380/0
S3_ENDPOINT=http://localhost:9000
S3_PUBLIC_URL=http://localhost:9000
S3_ACCESS_KEY=minioadmin
S3_SECRET_KEY=minioadmin
S3_BUCKET=gigtrack-media
FLASK_SECRET=dev-secret-change-me
```

```bash
# 3. Install deps and seed Mongo + bucket + artist photos (reads .env automatically).
pip install -r requirements.txt
export PYTHONPATH=$PWD/app
python mongo/seed.py
python app/storage.py               # creates the MinIO bucket
python scripts/fetch_artist_images.py   # real portraits → MinIO
# 4. Run the app (local Flask defaults to port 5000):
flask --app app/app.py run          # → http://localhost:5000
```

> `.env` is loaded automatically by `app/db.py` via python-dotenv, so every
> script and the app pick up the same config. `.env` is gitignored — never
> commit real secrets.
>
> If instead you run MySQL/Mongo/Redis **natively** on their default ports,
> use `.env.example` as-is (3306 / 27017 / 6379) and load the SQL yourself:
> `mysql -uroot -p gigtrack < sql/schema.sql && mysql -uroot -p gigtrack < sql/seed.sql`.

## Project layout

```
gigtrack/
├── docs/
│   ├── er_diagram.mermaid     ER diagram (Mermaid syntax)
│   └── schema_design.md       Rationale for all three datastores
├── sql/
│   ├── schema.sql             8 tables, FKs, indexes, 2 triggers
│   ├── seed.sql               GENERATED seed (scripts/generate_seed.py)
│   └── queries.sql            CRUD + joins + nested + window/CTE/txn + triggers
├── mongo/
│   ├── seed.py                Seeds setlists, reviews, artist_bios (idempotent)
│   └── queries.py             Sample queries inc. $facet / $lookup aggregation
├── app/
│   ├── app.py                 Flask routes (incl. /admin dashboard)
│   ├── db.py                  MySQL / Mongo / Redis connection helpers
│   ├── storage.py             S3/MinIO object-storage helper (media blobs)
│   ├── templates/             Jinja templates (incl. templates/admin/)
│   └── static/style.css
├── scripts/
│   ├── generate_seed.py       Writes the larger synthetic sql/seed.sql
│   ├── fetch_artist_images.py Real portrait photos → MinIO → artists.image_url
│   └── benchmark.py           Multi-dimension perf benchmark → CSV + chart
├── docker-compose.yml
├── Dockerfile
└── requirements.txt
```

## Demo script (for video / slides)

1. Show the landing page (`/`) — hero + trending concerts (with artist photos
   served from MinIO) + headline stats. Click "Concerts" for the full list (`/concerts`).
2. Filter `/concerts?city=Singapore` — second load is served from Redis (note the
   cache key in `redis-cli MONITOR`).
3. Open a concert detail page — the setlist & reviews come from MongoDB;
   the view counter `concert:{id}:views` increments in Redis.
4. Log in as `macc@example.com` / `password`.
5. Book a ticket → BEFORE INSERT trigger decrements `tickets.available_seats`.
6. Try to book more seats than are available → trigger raises, transaction
   rolls back, app shows a safe error.
7. Cancel a booking from `/my/bookings` → AFTER UPDATE trigger restores the
   seats to `tickets.available_seats`.
8. Post a review with a photo → text + photo metadata go into `gigtrack.reviews`
   in Mongo; the image file goes to the MinIO `gigtrack-media` bucket. Browse it
   in the MinIO console at `http://localhost:9001` (minioadmin / minioadmin).
9. Follow / unfollow an artist → M:N row inserted into `follows`.
10. Like a review (👍) → toggles your id in the review's `liked_by` set; liking
    twice is a no-op (no duplicate counts). Delete your own review → its photos
    are removed from MinIO too.
11. **Admin** (log in as `macc`) → `/admin`: create/edit/delete concerts and
    ticket tiers, and view/edit/disable/delete user accounts.
12. Run the performance benchmark (see below).

## Running the benchmark

The benchmark times Redis-cached vs uncached MySQL, indexed vs full-scan (MySQL
and Mongo), latency percentiles (p50/p95/p99), throughput, **CPU time per call**
and **peak memory**, then writes `scripts/benchmark_results.csv` and
`scripts/benchmark_latency.png`.

It needs the datastores running and the same env the app uses.

**Inside the running stack (easiest):**

```bash
docker compose exec app python scripts/benchmark.py --iterations 300
# copy the results out of the container if you want them on the host:
docker compose cp app:/app/scripts/benchmark_results.csv ./scripts/
docker compose cp app:/app/scripts/benchmark_latency.png ./scripts/
```

**Locally (datastores in Docker, see "local Flask" setup above):**

```bash
docker compose up -d mysql mongo redis minio   # if not already up
pip install -r requirements.txt                # brings in matplotlib + psutil
export PYTHONPATH=$PWD/app                      # so 'import db' resolves
# .env must point at the remapped host ports (3307/27018/6380) — see above
python scripts/benchmark.py --iterations 300
```

`--iterations` sets how many calls per scenario (higher = smoother numbers,
default 300). Sample output:

```
GigTrack benchmark — 300 iterations each

scenario                               avg       p50       p95       p99  cpu/call      ops/s
---------------------------------------------------------------------------------------------
MySQL trending (uncached)            1.842     1.701     2.910     4.220    0.4100      542.8
Redis cached                         0.071     0.064     0.118     0.190    0.0300    14084.5
MySQL indexed (status)               0.610     0.560     0.980     1.510    0.1800     1639.3
MySQL unindexed (base_price)         1.430     1.330     2.210     3.020    0.2600      699.3
Mongo indexed (concert_id)           0.540     0.500     0.860     1.220    0.1500     1851.9
Mongo unindexed (rating)             1.190     1.110     1.880     2.540    0.2200      840.3
MySQL payload LIMIT 5                 1.220     1.150     1.910     2.610    0.3200      819.7
MySQL payload LIMIT 20               1.840     1.700     2.880     4.010    0.4100      543.5
MySQL payload LIMIT 50               2.910     2.700     4.520     6.330    0.5800      343.6

Client process: peak memory ≈ 78.4 MB, avg CPU ≈ 12.0%
```

(Numbers are illustrative — they depend on your machine.) The headline result
is the Redis-cached row being ~20–30× faster than the uncached MySQL join, plus
indexed beating full-scan on both stores. The same data is written to
`scripts/benchmark_results.csv` and charted in `scripts/benchmark_latency.png`.

Outputs land in `scripts/`. `--iterations` controls how many calls per scenario
(higher = smoother numbers).

## Security notes

- All SQL uses **parameterized queries** (no string interpolation) → safe from injection.
- Passwords are **bcrypt**-hashed (unique salt per user). Sessions are random tokens in Redis.
- **CSRF tokens** on every state-changing form; **SameSite/HttpOnly** cookies; `Secure` cookie when `FLASK_ENV=production`.
- `debug` is off unless `FLASK_DEBUG=1`; set a real `FLASK_SECRET` in production.
- Uploaded images are type-checked, size-capped (5 MB) and re-encoded/downscaled before storage.

## Mapping to the project brief

| Brief item | Where to find it |
|---|---|
| Task 1 — application | This README + slides |
| Task 2 — dataset      | Larger synthetic seed (`scripts/generate_seed.py`); optional Kaggle import |
| Task 3 — ER + NoSQL schema | `docs/er_diagram.mermaid`, `docs/schema_design.md` |
| Task 4 — CRUD (SQL + NoSQL, all wired into the app) | SQL: signup/booking/profile/admin + `sql/queries.sql` (A); Mongo: review create/read/like/delete + `mongo/queries.py` |
| Task 5 — complex / triggers / SQL-vs-NoSQL | `sql/queries.sql` (D nested, G window, H CTE, I transaction, E triggers); Mongo `$facet` + `$lookup` in `mongo/queries.py` |
| Task 6 — GenAI reflection | Add to final report |
| Task 7 — perf analysis | `scripts/benchmark.py` (cache, index-vs-scan, percentiles, throughput, payload scaling) → CSV + chart |
| Task 8 — web UI + admin | `app/` + `/admin` dashboard |
| Data organization & security | `docs/schema_design.md`; Security notes above |
| Object storage | `app/storage.py` + MinIO; blobs in storage, pointers in Mongo (`docs/schema_design.md` §4) |

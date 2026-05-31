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

## Quick start (docker-compose)

```bash
docker compose up --build
# wait ~30s for MySQL to seed and Mongo seeder to run
open http://localhost:5000
```

> **Changed the schema or seed?** MySQL only runs `schema.sql`/`seed.sql` on a
> *fresh* data volume. To force a reseed (and pick up new columns), reset the
> volumes first:
>
> ```bash
> docker compose down -v && docker compose up --build
> ```

Sample logins (from `sql/seed.sql`, password is `password` for everyone):

- **Admin:** `macc@example.com` / `password` → has the Admin dashboard at `/admin`
- Regular user: `user02@example.com` … `user21@example.com`

On boot the app also generates a branded poster for each artist and uploads it
to MinIO (`scripts/generate_images.py`), so the concert cards show images served
from object storage.

## Quick start (manual)

```bash
# 1. start the three datastores however you like (local installs, or just `docker compose up mysql mongo redis`)
# 2. load relational schema + seeds
mysql -u root -p gigtrack < sql/schema.sql
mysql -u root -p gigtrack < sql/seed.sql

# 3. seed Mongo
python mongo/seed.py

# 4. start Flask
pip install -r requirements.txt
export PYTHONPATH=$PWD/app
flask --app app/app.py run
```

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
│   ├── generate_images.py     PIL posters → MinIO → artists.image_url
│   ├── benchmark.py           Multi-dimension perf benchmark → CSV + chart
│   └── make_benchmark_slide.py  Builds a .pptx slide from the benchmark CSV
├── docker-compose.yml
├── Dockerfile
└── requirements.txt
```

## Demo script (for video / slides)

1. Show the landing page (`/`) — hero + trending concerts (with poster images
   from MinIO) + headline stats. Click "Concerts" for the full list (`/concerts`).
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
12. Run `PYTHONPATH=app python scripts/benchmark.py` → writes
    `benchmark_results.csv` + `benchmark_latency.png`; then
    `python scripts/make_benchmark_slide.py` builds `benchmark_slide.pptx`.

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
| Task 7 — perf analysis | `scripts/benchmark.py` (cache, index-vs-scan, percentiles, throughput, payload scaling) → CSV + chart + slide |
| Task 8 — web UI + admin | `app/` + `/admin` dashboard |
| Data organization & security | `docs/schema_design.md`; Security notes above |
| Object storage | `app/storage.py` + MinIO; blobs in storage, pointers in Mongo (`docs/schema_design.md` §4) |

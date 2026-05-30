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

Sample login (from `sql/seed.sql`):

- email: `macc@example.com`
- password: `password`

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
│   ├── seed.sql               Realistic seed data
│   └── queries.sql            CRUD + joins + nested + trigger demos
├── mongo/
│   ├── seed.py                Seeds setlists, reviews, artist_bios
│   └── queries.py             Sample queries inc. aggregation
├── app/
│   ├── app.py                 Flask routes
│   ├── db.py                  MySQL / Mongo / Redis connection helpers
│   ├── storage.py             S3/MinIO object-storage helper (media blobs)
│   ├── templates/             Jinja templates
│   └── static/style.css
├── scripts/
│   └── benchmark.py           Cached-vs-uncached latency benchmark (Task 7)
├── docker-compose.yml
├── Dockerfile
└── requirements.txt
```

## Demo script (for video / slides)

1. Show home page (`/`) — list of upcoming concerts from MySQL.
2. Filter by `?city=Singapore` — second load is served from Redis (note the
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
10. Run `PYTHONPATH=app python scripts/benchmark.py` → prints the cached-vs-
    uncached latency speedup (Task 7).

## Mapping to the project brief

| Brief item | Where to find it |
|---|---|
| Task 1 — application | This README + slides |
| Task 2 — dataset      | Seed data + plan to import Setlist.fm/Kaggle data |
| Task 3 — ER + NoSQL schema | `docs/er_diagram.mermaid`, `docs/schema_design.md` |
| Task 4 — CRUD         | `sql/queries.sql` (A), `mongo/queries.py` (CRUD section) |
| Task 5 — complex / triggers / SQL-vs-NoSQL discussion | `sql/queries.sql` (D, E), `docs/schema_design.md` §5 |
| Task 6 — GenAI reflection | Add to final report |
| Task 7 — perf analysis (optional) | `scripts/benchmark.py` — measures cached (Redis) vs uncached (MySQL) latency and prints the speedup |
| Task 8 — web UI (optional) | `app/` |
| Bonus — object storage | `app/storage.py` + MinIO service; blobs in S3-compatible storage, pointers in Mongo (`docs/schema_design.md` §4) |

# GigTrack

A live-music & concert companion (INF2003 Database Systems group project). It
uses four datastores, each for what it's best at:

- **MySQL** — transactional, relational data (users, artists, venues, concerts,
  tickets, bookings, follows): foreign keys, CHECK constraints, and triggers
  that keep seat inventory and VIP pricing correct.
- **MongoDB** — variable-shape documents (setlists, reviews with tags/photos,
  artist bios) that would be painful to normalise.
- **Redis** — cached concert listings, distinct city/genre lists, live view
  counters, and session tokens.
- **MinIO** — S3-compatible object storage for image **blobs** (artist photos,
  review photos). The database stores only the URL pointer; bytes live in a
  bucket. Swap the endpoint for AWS S3 / Cloudflare R2 with no code change.

## Features

- Ticketmaster-style landing page: full-width hero, featured grid, category
  tiles (from DB genres), and a "more shows" rail.
- Browse + filter concerts by **city and/or genre** (Redis-cached).
- Concert detail with lineup, tiered tickets, MongoDB setlist & reviews.
- Customer flow: sign up, log in, book tickets (**max 6 per concert**, enforced
  by a DB query), review with photos, like/unlike, follow artists.
- **Admin dashboard**: create/edit/delete concerts and ticket tiers (add new
  artists/venues on the fly), manage bookings (resize/cancel), manage users
  (edit/disable/delete), with search on every list. Admins can't buy tickets.
- Security: parameterised SQL, bcrypt passwords, CSRF tokens, hardened cookies.

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

## Prerequisites & installation

You need **Docker** (it provides the four datastores). For the
all-in-Docker run that's the only requirement; for the local-Flask run you also
need **Python 3.11+**.

**Install Docker Desktop** (includes Docker Compose):

- macOS: <https://docs.docker.com/desktop/install/mac-install/> — or `brew install --cask docker`
- Windows (WSL2): <https://docs.docker.com/desktop/install/windows-install/>
- Linux: Docker Engine + Compose plugin — <https://docs.docker.com/engine/install/>

Verify: `docker --version` and `docker compose version`.

**Python (only for the local-Flask option):** 3.11+ from <https://www.python.org/downloads/>
(macOS: `brew install python`). Verify: `python3 --version`.

## Quick start — all in Docker (recommended)

The only prerequisite is Docker. No `.env` needed — `docker-compose.yml`
injects every variable into the app container.

```bash
docker compose up --build
# First boot takes ~1 min: MySQL + Mongo seed, and artist photos download.
open http://localhost:5001          # Linux: xdg-open, Windows: start
```

Sample logins (password is `password` for everyone):

- **Admin:** `macc@example.com` / `password` → Admin dashboard at `/admin`
- Regular users: `user02@example.com` … `user21@example.com`

> **Artist photos.** On boot `scripts/fetch_artist_images.py` downloads a real
> placeholder portrait per artist (from Pravatar — free, no API key, no rate
> limit) and uploads it to MinIO. If a photo can't be fetched the card keeps a
> gradient placeholder. Re-run without a reboot:
> `docker compose exec app python scripts/fetch_artist_images.py --force`

> **Changed the schema or seed?** MySQL only runs `schema.sql`/`seed.sql` on a
> *fresh* data volume, so a plain `--build` keeps the OLD data. Reset volumes to
> reseed:
>
> ```bash
> docker compose down -v && docker compose up --build
> ```

## Quick start — local Flask, datastores in Docker

Run the datastores in Docker (they auto-seed) but run Flask on your machine for
faster iteration. `.env.example` is already set to the remapped host ports, so
just copy it — no edits needed.

```bash
# 1. Start ONLY the datastores (MySQL auto-loads schema.sql + seed.sql).
docker compose up -d mysql mongo redis minio

# 2. Copy the env file (no editing required — it matches the ports above).
cp .env.example .env

# 3. (Recommended) create a virtualenv, then install deps.
python3 -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# 4. Seed Mongo + create the MinIO bucket + fetch artist photos.
#    Every script loads .env automatically (python-dotenv).
export PYTHONPATH=$PWD/app                            # Windows (PowerShell): $env:PYTHONPATH="$PWD/app"
python mongo/seed.py
python app/storage.py                  # creates the MinIO bucket
python scripts/fetch_artist_images.py  # real portraits → MinIO

# 5. Run the app (local Flask defaults to port 5000).
flask --app app/app.py run             # → http://localhost:5000
```

## Run fully without Docker (native install)

Install the four datastores natively, then run Flask against them. Commands are
shown for **macOS (Homebrew)** and **Ubuntu/Debian (apt)**; Windows users can
use the official installers linked at each step (or WSL2 with the apt commands).

### 1. MySQL 8

```bash
# macOS
brew install mysql && brew services start mysql

# Ubuntu/Debian
sudo apt update && sudo apt install -y mysql-server && sudo systemctl enable --now mysql
```
Windows: MySQL Installer — <https://dev.mysql.com/downloads/installer/>

Create the database + app user, then load the schema and seed:

```bash
# Opens a root shell (use the root password you set during install; on a fresh
# Homebrew install root often has no password, so omit -p).
mysql -uroot -p <<'SQL'
CREATE DATABASE IF NOT EXISTS gigtrack CHARACTER SET utf8mb4;
CREATE USER IF NOT EXISTS 'gigtrack'@'localhost' IDENTIFIED BY 'gigtrack_pw';
GRANT ALL PRIVILEGES ON gigtrack.* TO 'gigtrack'@'localhost';
FLUSH PRIVILEGES;
SQL

# Load schema (tables + triggers) then the seed data:
mysql -ugigtrack -pgigtrack_pw gigtrack < sql/schema.sql
mysql -ugigtrack -pgigtrack_pw gigtrack < sql/seed.sql
```

### 2. MongoDB 7

```bash
# macOS
brew tap mongodb/brew && brew install mongodb-community@7.0
brew services start mongodb-community@7.0

# Ubuntu/Debian — follow the official repo steps, then:
sudo systemctl enable --now mongod
```
Install guide / Windows: <https://www.mongodb.com/docs/manual/installation/>
(No manual seeding here — `mongo/seed.py` in step 5 populates it.)

### 3. Redis 7

```bash
# macOS
brew install redis && brew services start redis

# Ubuntu/Debian
sudo apt install -y redis-server && sudo systemctl enable --now redis-server
```
Windows: use WSL2, or Memurai (<https://www.memurai.com/>) as a Redis-compatible service.

### 4. MinIO (object storage)

```bash
# macOS
brew install minio/stable/minio
MINIO_ROOT_USER=minioadmin MINIO_ROOT_PASSWORD=minioadmin minio server ~/minio-data --console-address ":9001"

# Linux
wget https://dl.min.io/server/minio/release/linux-amd64/minio -O minio && chmod +x minio
MINIO_ROOT_USER=minioadmin MINIO_ROOT_PASSWORD=minioadmin ./minio server ~/minio-data --console-address ":9001"
```
This serves the S3 API on `:9000` and the console on `:9001`. Leave it running.
(Optional: skip MinIO and set `S3_ENDPOINT=` empty in `.env` — reviews still
post, just without photos, and artist cards show the gradient placeholder.)

### 5. Point the app at the native services and run

Native services use their **default ports**, which differ from the Docker-mapped
ones, so set `.env` accordingly:

```bash
cp .env.example .env
```
Edit `.env` to the native defaults:

```ini
MYSQL_HOST=localhost
MYSQL_PORT=3306
MYSQL_USER=gigtrack
MYSQL_PASSWORD=gigtrack_pw
MYSQL_DB=gigtrack
MONGO_URI=mongodb://localhost:27017
REDIS_URL=redis://localhost:6379/0
S3_ENDPOINT=http://localhost:9000
S3_PUBLIC_URL=http://localhost:9000
S3_ACCESS_KEY=minioadmin
S3_SECRET_KEY=minioadmin
S3_BUCKET=gigtrack-media
FLASK_SECRET=dev-secret-change-me
```

Then install Python deps, seed Mongo + MinIO, and start the app:

```bash
python3 -m venv .venv && source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -r requirements.txt
export PYTHONPATH=$PWD/app                             # PowerShell: $env:PYTHONPATH="$PWD/app"
python mongo/seed.py                    # seeds MongoDB collections
python app/storage.py                   # creates the MinIO bucket
python scripts/fetch_artist_images.py   # real portraits → MinIO
flask --app app/app.py run              # → http://localhost:5000
```

`.env` is loaded automatically by every entry point (python-dotenv) and is
gitignored — never commit real secrets.

## Project layout

```
gigtrack/
├── docs/
│   ├── er_diagram.mermaid       ER diagram (Mermaid)
│   ├── data_flow.svg            Sequence diagram across the 4 datastores
│   ├── schema_design.md         Datastore rationale + SQL↔NoSQL discussion
│   ├── demo_cli.md              CLI cheat-sheet + per-store demo scenarios
│   ├── presentation_demo.md     10-min video script (6 presenters)
│   ├── report_structure.md      Proposal + final report outlines
│   └── project_reference.md     File-by-file + dependency reference
├── sql/
│   ├── schema.sql               8 tables, FKs, CHECK, indexes, 4 triggers
│   ├── seed.sql                 GENERATED seed (scripts/generate_seed.py)
│   └── queries.sql              CRUD + joins + nested + window/CTE/txn + triggers
├── mongo/
│   ├── seed.py                  Seeds setlists, reviews, artist_bios (idempotent)
│   └── queries.py               Sample queries inc. $facet / $lookup
├── app/
│   ├── app.py                   Flask routes (incl. /admin dashboard)
│   ├── db.py                    MySQL / Mongo / Redis helpers (+ retry)
│   ├── storage.py               S3/MinIO object-storage helper
│   ├── templates/               Jinja templates (incl. templates/admin/)
│   └── static/style.css
├── scripts/
│   ├── generate_seed.py         Writes the synthetic sql/seed.sql
│   ├── fetch_artist_images.py   Real portraits → MinIO → artists.image_url
│   └── benchmark.py             Perf benchmark → CSV + chart
├── docker-compose.yml
├── Dockerfile
└── requirements.txt
```

## Demo script (short version)

A full 10-minute video script for 6 presenters is in
[`docs/presentation_demo.md`](docs/presentation_demo.md); live CLI scenarios for
all four datastores are in [`docs/demo_cli.md`](docs/demo_cli.md).

1. Landing page (`/`) — hero + category tiles + featured rail (artist photos from MinIO).
2. Browse `/concerts?genre=Pop&city=Singapore` — filtered listing; second load served from Redis cache.
3. Concert detail — lineup + tiers from MySQL; setlist & reviews from MongoDB; Redis view counter ticks.
4. Log in as a customer → book tickets with the **+/- stepper**; try to exceed **6 per concert** → blocked with an error.
5. Oversell attempt → `BEFORE INSERT` trigger rolls back; cancel a booking → `AFTER UPDATE` trigger restores seats.
6. Post a review with a photo → text/metadata in Mongo, image blob in MinIO (show the MinIO console).
7. Like a review twice → count doesn't double (per-user `liked_by` set).
8. Log in as **admin** (`macc`) → `/admin`: add a concert with a new artist/venue + tiers (VIP-pricing rule enforced); search bookings; resize/cancel a customer booking.
9. Run `scripts/benchmark.py` → cached vs uncached, indexed vs scan, CPU/memory.

## Running the benchmark

Times Redis-cached vs uncached MySQL, indexed vs full-scan (MySQL & Mongo),
latency percentiles (p50/p95/p99), throughput, **CPU time per call** and
**peak memory** → `scripts/benchmark_results.csv` + `scripts/benchmark_latency.png`.

```bash
# Inside the running stack (easiest):
docker compose exec app python scripts/benchmark.py --iterations 300
docker compose cp app:/app/scripts/benchmark_results.csv ./scripts/
docker compose cp app:/app/scripts/benchmark_latency.png ./scripts/

# Or locally (datastores in Docker, .env on the remapped ports):
export PYTHONPATH=$PWD/app
python scripts/benchmark.py --iterations 300
```

Sample output:

```
scenario                               avg       p50       p95       p99  cpu/call      ops/s
---------------------------------------------------------------------------------------------
MySQL trending (uncached)            1.842     1.701     2.910     4.220    0.4100      542.8
Redis cached                         0.071     0.064     0.118     0.190    0.0300    14084.5
MySQL indexed (status)               0.610     0.560     0.980     1.510    0.1800     1639.3
MySQL unindexed (base_price)         1.430     1.330     2.210     3.020    0.2600      699.3
Mongo indexed (concert_id)           0.540     0.500     0.860     1.220    0.1500     1851.9
Mongo unindexed (rating)             1.190     1.110     1.880     2.540    0.2200      840.3

Client process: peak memory ≈ 78.4 MB, avg CPU ≈ 12.0%
```

(Numbers depend on your machine.) Headline: Redis cache ~20–30× faster than the
uncached join; indexed lookups beat full scans on both stores.

## Security notes

- All SQL uses **parameterised queries** (no string interpolation) → injection-safe.
- Passwords are **bcrypt**-hashed (unique salt per user); sessions are random tokens in Redis.
- **CSRF tokens** on every state-changing form; **HttpOnly/SameSite** cookies; `Secure` when `FLASK_ENV=production`.
- `debug` is off unless `FLASK_DEBUG=1`; set a real `FLASK_SECRET` outside dev.
- Uploaded images are type-checked, size-capped (5 MB) and downscaled before storage.

## Mapping to the project brief

| Brief item | Where |
|---|---|
| Task 1 — application | This README + `docs/presentation_demo.md` |
| Task 2 — dataset | Synthetic seed (`scripts/generate_seed.py`); optional Kaggle import |
| Task 3 — ER + NoSQL schema | `docs/er_diagram.mermaid`, `docs/schema_design.md` |
| Task 4 — CRUD (SQL + NoSQL, wired into the app) | signup/booking/profile/admin + `sql/queries.sql`; review create/read/like/delete + `mongo/queries.py` |
| Task 5 — complex / triggers / SQL-vs-NoSQL | `sql/queries.sql` (nested, window, CTE, transaction, triggers); Mongo `$facet` + `$lookup` |
| Task 6 — GenAI reflection | Final report (see `docs/report_structure.md`) |
| Task 7 — performance | `scripts/benchmark.py` → CSV + chart |
| Task 8 — web UI + admin | `app/` + `/admin` |
| Data organisation & security | `docs/schema_design.md`; Security notes above |
| Object storage | `app/storage.py` + MinIO |
```

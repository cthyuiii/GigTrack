# GigTrack

A live-music & concert companion (INF2003 Database Systems group project). It
uses four datastores, each for what it's best at:

- **MySQL** - transactional, relational data (users, artists, venues, concerts,
  tickets, bookings, follows): foreign keys, CHECK constraints, and triggers
  that keep seat inventory and VIP pricing correct.
- **MongoDB** - variable-shape documents (setlists, reviews with tags/photos,
  artist bios) that would be painful to normalise.
- **Redis** - cached concert listings, distinct city/genre lists, live view
  counters, and session tokens.
- **MinIO** - S3-compatible object storage for image **blobs** (artist photos,
  review photos). The database stores only the URL pointer; bytes live in a
  bucket. Swap the endpoint for AWS S3 / Cloudflare R2 with no code change.

## Features

- Ticketmaster-style landing page: full-width hero, featured grid, category
  tiles (from DB genres), and a "more shows" rail.
- Browse + filter concerts by **city, genre, and time window** (upcoming /
  past / all) - listings are shown **sequentially by date** and Redis-cached.
- Concert detail with lineup, tiered tickets, MongoDB setlist & reviews.
- Customer flow: sign up, log in, book tickets (**max 6 per concert**, enforced
  in a single locking DB transaction - race-safe), review with photos,
  like/unlike, follow artists.
- **Admin dashboard**: create/edit/delete concerts and ticket tiers (add new
  artists/venues on the fly), manage bookings (resize/cancel), manage users
  (edit/disable/delete), with search on every list. Admins can't buy tickets.
- Security: parameterised SQL, bcrypt passwords, CSRF tokens, hardened cookies.

## Ports (host → container)

The datastore host ports match their native defaults and the app uses 5050, so the same `.env` works whether you run
the datastores natively or in Docker. Because nothing is remapped, stop any local
service already using these ports before `docker compose up`.

| Service | In browser / from host | Inside the compose network |
|---|---|---|
| **App (Flask)** | <http://localhost:5050> | `app:5050` |
| MySQL | `localhost:3306` | `mysql:3306` |
| MongoDB | `localhost:27017` | `mongo:27017` |
| Redis | `localhost:6379` | `redis:6379` |
| MinIO API | `localhost:9000` | `minio:9000` |
| MinIO Console | <http://localhost:9001> (`minioadmin`/`minioadmin`) | `minio:9001` |

## Prerequisites & installation

Three ways to run, pick one:

1. **Native install (no Docker)** - install the four datastores yourself +
   **Python 3.11+**. First section below.
2. **All in Docker** - Docker is the only requirement.
3. **Hybrid** - datastores in Docker, Flask local (Python 3.11+ as well).

**Install Docker Desktop** (only for options 2–3; includes Docker Compose):

- macOS: <https://docs.docker.com/desktop/install/mac-install/> - or `brew install --cask docker`
- Windows (WSL2): <https://docs.docker.com/desktop/install/windows-install/>
- Linux: Docker Engine + Compose plugin - <https://docs.docker.com/engine/install/>

Verify: `docker --version` and `docker compose version`.

**Python (options 1 and 3):** 3.11+ from <https://www.python.org/downloads/>
(macOS: `brew install python`). Verify: `python3 --version`.

## Quick start - native install (no Docker)

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
Windows: MySQL Installer - <https://dev.mysql.com/downloads/installer/>

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

# Ubuntu/Debian - follow the official repo steps, then:
sudo systemctl enable --now mongod
```
Install guide / Windows: <https://www.mongodb.com/docs/manual/installation/>
(No manual seeding here - `mongo/seed.py` in step 5 populates it.)

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
(Optional: skip MinIO and set `S3_ENDPOINT=` empty in `.env` - reviews still
post, just without photos, and artist cards show the gradient placeholder.)

### 5. Point the app at the native services and run

Native services use their **default ports**, which are the same as the Docker host
ports, so set `.env` accordingly:

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
flask --app app/app.py run --port 5050   # → http://localhost:5050
```

`.env` is loaded automatically by every entry point (python-dotenv) and is
gitignored - never commit real secrets.

## Alternative - all in Docker

The only prerequisite is Docker. No `.env` needed - `docker-compose.yml`
injects every variable into the app container.

```bash
docker compose up --build
# First boot takes ~1 min: MySQL + Mongo seed, and artist photos download.
open http://localhost:5050          # Linux: xdg-open, Windows: start
```

Sample logins (password is `password` for everyone):

- **Admin:** `macc@example.com` / `password` → Admin dashboard at `/admin`
- Regular users: `user02@example.com` … `user21@example.com`

> **Artist photos.** On boot `scripts/fetch_artist_images.py` downloads a real
> placeholder portrait per artist (from Pravatar - free, no API key, no rate
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

## Quick start - local Flask, datastores in Docker

Run the datastores in Docker (they auto-seed) but run Flask on your machine for
faster iteration. `.env.example` already uses the default host ports, so just
copy it - no edits needed.

```bash
# 1. Start ONLY the datastores (MySQL auto-loads schema.sql + seed.sql).
docker compose up -d mysql mongo redis minio

# 2. Copy the env file (no editing required - it matches the ports above).
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

# 5. Run the app (on 5050; 5000 is taken by macOS AirPlay).
flask --app app/app.py run --port 5050   # → http://localhost:5050
```

## Troubleshooting (local setup)

Common issues when running natively (mostly macOS) and how to fix them:

- **`brew install mysql` fails or won't link because MariaDB is installed.** Both ship a `mysql` binary, so Homebrew refuses to link. Free port 3306 and the link, then install:
  ```bash
  brew services stop mariadb && brew unlink mariadb
  brew install mysql && brew link --overwrite mysql && brew services start mysql
  ```
  To switch back later: `brew services stop mysql && brew unlink mysql && brew link mariadb && brew services start mariadb`. (Or skip MySQL entirely and use your existing MariaDB 10.6+, which runs this schema; just point `.env` at port 3306.)

- **`ERROR 1419 (HY000): You do not have the SUPER privilege and binary logging is enabled`** while loading `schema.sql`. MySQL 8 enables binary logging by default, so a non-root user cannot create triggers until creators are trusted. Set it once as root (who has the privilege), then re-run (the schema self-drops, so re-running is clean):
  ```bash
  mysql -uroot -e "SET PERSIST log_bin_trust_function_creators = 1;"
  mysql -ugigtrack -pgigtrack_pw gigtrack < sql/schema.sql
  mysql -ugigtrack -pgigtrack_pw gigtrack < sql/seed.sql
  ```
  Alternatively, load the schema as root: `mysql -uroot gigtrack < sql/schema.sql` (root has SUPER).

- **`Refusing to load formula mongodb/brew/... from untrusted tap`.** Homebrew now requires trusting third-party taps. `mongodb/brew` is MongoDB's official tap:
  ```bash
  brew trust mongodb/brew
  brew install mongodb-community@7.0 && brew services start mongodb-community@7.0
  ```

- **The app's port (5050) is already in use, or you want 5000 (which macOS AirPlay Receiver owns).** Run on another port, or turn AirPlay Receiver off in System Settings > General > AirDrop & Handoff:
  ```bash
  flask --app app/app.py run --port 5060   # then open http://localhost:5060
  ```

- **`Can't connect to MySQL / MongoDB / Redis (Connection refused)`.** Both native and Docker now use the same host ports (3306 / 27017 / 6379), so the same `.env` works either way; just don't run a local datastore and its Docker container on the same port at once. Confirm the service is up (`brew services list` or `docker compose ps`).

- **`Access denied for user 'gigtrack'`.** The DB user was not created or the password is wrong. Re-run the `CREATE USER ... GRANT ... FLUSH PRIVILEGES` block from step 1.

- **PyMySQL `Authentication plugin 'caching_sha2_password'` error (MySQL 8).** Make sure deps are installed (`pip install -r requirements.txt`, which includes `cryptography`). If it persists: `ALTER USER 'gigtrack'@'localhost' IDENTIFIED WITH mysql_native_password BY 'gigtrack_pw';`

- **`ModuleNotFoundError: No module named 'db'` (or `app`).** Set the import path before running Flask, the scripts, or pytest: `export PYTHONPATH=$PWD/app`.

- **No setlists, reviews or bios appear.** MongoDB was not seeded (only MySQL auto-seeds). With the venv active and `PYTHONPATH=$PWD/app`, run `python mongo/seed.py`.

- **Artist images or review photos do not load.** MinIO is not running, or the bucket/photos were not created. Start `minio server ...`, then `python app/storage.py` (creates the bucket) and `python scripts/fetch_artist_images.py`. To run without object storage, set `S3_ENDPOINT=` (blank) in `.env`; reviews still work, just without photos.

- **Schema or seed changes do not appear (Docker).** MySQL only runs the init SQL on a fresh data volume: `docker compose down -v && docker compose up --build`.

- **`flask: command not found`.** Activate the venv (`source .venv/bin/activate`) or call `python -m flask --app app/app.py run`.

## Accessing the databases and consoles

Once the app is running, open a console for each store to view data and watch it
change live. Native commands are shown first; the Docker equivalent is in the
comment beside it.

### MySQL (relational core)
```bash
mysql -ugigtrack -pgigtrack_pw gigtrack
# Docker: docker compose exec mysql mysql -ugigtrack -pgigtrack_pw gigtrack
```
```sql
SHOW Databases;
USE gigtrack;
SHOW TABLES;
SELECT * FROM concerts LIMIT 5;
SELECT ticket_id, tier, available_seats FROM tickets WHERE concert_id = 6;
SELECT * FROM bookings ORDER BY booking_id DESC LIMIT 5;
SHOW TRIGGERS;                              -- the 4 business-rule triggers
SELECT * FROM v_concert_summary LIMIT 5;    -- the reusable view
```

### MongoDB (documents)
```bash
mongosh gigtrack
# Docker: docker compose exec mongo mongosh gigtrack
```
```javascript
show collections                       // reviews, setlists, artist_bios
db.reviews.find().limit(3)
db.reviews.find({ concert_id: 6 }).pretty()
db.setlists.findOne()
db.artist_bios.findOne()
db.reviews.countDocuments()
```

### Redis (cache, sessions, view counters)
```bash
redis-cli
# Docker: docker compose exec redis redis-cli
```
```bash
KEYS *                       # everything currently cached
KEYS session:*               # active login sessions
TTL session:<paste-token>    # about 1800s, refreshes on each request
GET concert:6:views          # pending view counter for concert 6
KEYS browse:*                # cached listing pages
MONITOR                      # live stream of every command (Ctrl-C to stop)
```

### MinIO (object storage)
- Web console: http://localhost:9001 (login `minioadmin` / `minioadmin`), then open the `gigtrack-media` bucket to browse the uploaded images.
- Or list objects from the terminal with the MinIO client:
  ```bash
  brew install minio-mc            # the 'mc' client (skip if already installed)
  mc alias set local http://localhost:9000 minioadmin minioadmin
  mc ls -r local/gigtrack-media
  ```
  Docker, no install needed:
  ```bash
  docker run --rm --network gigtrack_default minio/mc sh -c \
    "mc alias set local http://minio:9000 minioadmin minioadmin && mc ls -r local/gigtrack-media"
  ```

> `docs/demo_cli.md` has step-by-step before/after scenarios (sign up, book, review, cache hit) that show each store changing live as you use the app.

## Stopping everything

### All in Docker
```bash
docker compose stop          # pause containers, keep them and the data
docker compose down          # stop AND remove the containers (data volumes kept)
docker compose down -v       # also delete the data volumes (next up reseeds from scratch)
```

### Native + local Flask, macOS (Homebrew)
```bash
# 1. Stop the app: press Ctrl-C in the terminal running `flask run`, then:
deactivate                   # leave the Python virtualenv (optional)

# 2. Stop the datastores
brew services stop mysql
brew services stop mongodb-community@7.0
brew services stop redis
# MinIO: press Ctrl-C in the terminal running `minio server`
#        (or, if backgrounded:  pkill -f 'minio server')

brew services list           # confirm everything shows "stopped"
```

### Native + local Flask, Linux (systemd)
```bash
# 1. Ctrl-C the `flask run`, then (optional) `deactivate` the virtualenv.
# 2. Stop the datastores (service names vary slightly by distro):
sudo systemctl stop mysql        # or: mysqld / mariadb
sudo systemctl stop mongod
sudo systemctl stop redis-server # or: redis
# MinIO: Ctrl-C its terminal (or: pkill -f 'minio server')
systemctl is-active mysql mongod redis-server   # confirm "inactive"
```

### Native + local Flask, Windows
```powershell
# 1. Press Ctrl-C in the terminal running `flask run`; `deactivate` the venv.
# 2. Stop the services (PowerShell as Administrator):
net stop MySQL80                 # service name from `Get-Service *mysql*`
net stop MongoDB
# Redis (Memurai): net stop Memurai   ; or stop "Redis" in services.msc
# MinIO: close/Ctrl-C its window (or end the minio.exe task)
Get-Service MySQL80, MongoDB     # confirm "Stopped"
```
> Don't know a service's exact name? `Get-Service *mysql*` / `*mongo*` / `*redis*`
> lists it. You can also stop any of these from the **Services** app (`services.msc`).

### Hybrid (datastores in Docker, Flask local)
```bash
# Ctrl-C the local `flask run`, then stop the datastore containers:
docker compose stop          # or: docker compose down  (down -v to wipe data)
```

> `docker compose stop` is the gentle option (resume later with `docker compose start`).
> `down` removes containers but keeps your seeded data; only `down -v` wipes it.

## Project layout

```
gigtrack/
├── docs/
│   ├── er_diagram.mermaid       ER diagram (Mermaid)
│   ├── data_flow.svg            Sequence diagram across the 4 datastores
│   ├── schema_design.md         Datastore rationale + SQL↔NoSQL discussion
│   ├── demo_cli.md              CLI cheat-sheet + per-store demo scenarios
│   ├── flow_diagrams.md         Mermaid flow diagrams for presentation/demo
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
├── tests/
│   ├── conftest.py              Fixtures (test client, fake sessions, skip logic)
│   ├── test_unit.py             Pure-logic tests (no Docker needed)
│   └── test_integration.py      Triggers/quota/cascade/cache tests (stack up)
├── docker-compose.yml
├── Dockerfile
└── requirements.txt
```

## Demo script (short version)

A full 10-minute video script for 6 presenters is in
[`docs/presentation_demo.md`](docs/presentation_demo.md); live CLI scenarios for
all four datastores are in [`docs/demo_cli.md`](docs/demo_cli.md).

1. Landing page (`/`) - hero + category tiles + featured rail (artist photos from MinIO).
2. Browse `/concerts?genre=Pop&city=Singapore` - filtered listing; second load served from Redis cache.
3. Concert detail - lineup + tiers from MySQL; setlist & reviews from MongoDB; Redis view counter ticks.
4. Log in as a customer → book tickets with the **+/- stepper**; try to exceed **6 per concert** → blocked with an error.
5. Oversell attempt → `BEFORE INSERT` trigger rolls back; cancel a booking → `AFTER UPDATE` trigger restores seats.
6. Post a review with a photo → text/metadata in Mongo, image blob in MinIO (show the MinIO console).
7. Like a review twice → count doesn't double (per-user `liked_by` set).
8. Log in as **admin** (`macc`) → `/admin`: add a concert with a new artist/venue + tiers (VIP-pricing rule enforced); search bookings; resize/cancel a customer booking.
9. Run `scripts/benchmark.py` → cached vs uncached, indexed vs scan, CPU/memory.

## Running the performance benchmark

Correctness is covered by `pytest` (see the next section); **database performance**
(compute, access time, write cost, concurrency) is covered by
`scripts/benchmark.py`. It measures **six groups** of scenarios - every one
reports avg/p50/p95/p99 latency, throughput (ops/s) and client **CPU time per
call**, plus **peak memory** for the run:

| Group | What it measures | Scenarios |
|---|---|---|
| `[reads]` | Caching & indexing on the read path | uncached 3-table join vs Redis cache; indexed vs full-scan (MySQL **and** Mongo); payload scaling (LIMIT 5/20/50) |
| `[point]` | **Access time** - fetch one record by key on each store | MySQL PRIMARY KEY lookup vs Mongo unique-index `find_one` vs Redis `GET` |
| `[compute]` | **Server-side computation** (engine does the work, not the client) | MySQL `GROUP BY` revenue join, window function (`RANK` per city); Mongo `$group`, `$lookup` join, `$facet` dashboard |
| `[writes]` | **Write latency** per store + trigger cost | Redis `SET`, Mongo `insert_one`, MySQL plain `INSERT`, MySQL `INSERT` into `bookings` (fires the seat trigger - the delta vs plain INSERT ≈ trigger overhead) |
| `[txn]` | Commit/fsync overhead | 50 INSERTs in **1 commit** vs 50 INSERTs in **50 commits** (latency per 50-row batch) |
| `[parallel]` | Throughput under concurrency | trending read hammered by N threads (default 8), cached vs uncached - aggregate ops/s |

### Why these benchmark cases are required

Every architectural decision in GigTrack is a performance claim, and each
group exists to back one of those claims with a number instead of an assertion:

- **`[reads]`** - we claim Redis caching and our indexes (incl. the composite
  `(status, concert_date)`) are worth their complexity. Cached-vs-uncached and
  indexed-vs-full-scan are the only honest way to show *by how much*; payload
  scaling shows whether latency is per-row or per-roundtrip.
- **`[point]`** - we put sessions and counters in Redis rather than MySQL.
  That's only justified if a Redis GET measurably beats a PK lookup; this
  ladder quantifies the gap that motivates the whole polyglot design.
- **`[compute]`** - we push aggregation into the engines (`GROUP BY`, window
  functions, `$facet`, `$lookup`) instead of computing in Python. The
  `cpu/call ≪ avg` gap is the evidence the server, not the client, did the
  work - i.e. the queries scale with the DB, not the app process.
- **`[writes]`** - triggers aren't free: every booking INSERT also locks and
  updates a ticket row. Measuring INSERT-with-trigger against a plain INSERT
  prices that integrity guarantee, so "we chose triggers" is an informed
  trade-off rather than a guess.
- **`[txn]`** - the seed loader and any future bulk import depend on batching.
  One commit vs fifty shows the per-commit (fsync/roundtrip) cost and justifies
  why `generate_seed.py` emits multi-row INSERTs.
- **`[parallel]`** - single-threaded latency hides contention. A real app
  serves concurrent users; this group shows whether throughput scales with
  workers and how much further the cache pulls ahead under load.

(They also directly serve the brief's optional Task 7 - "database performance
analysis, e.g., speed and memory usage" - which is why CPU-per-call and peak
memory are reported alongside latency.)

Write scenarios are **self-cleaning**: the scratch table is dropped, bench
bookings are deleted, and seats are restored. `--skip-writes` gives a
read-only run.

```bash
# Inside the running stack (easiest):
docker compose exec app python scripts/benchmark.py --iterations 300
docker compose cp app:/app/scripts/benchmark_results.csv ./scripts/
docker compose cp app:/app/scripts/benchmark_latency.png ./scripts/

# Or locally (datastores in Docker, .env on the remapped ports):
export PYTHONPATH=$PWD/app
python scripts/benchmark.py --iterations 300
python scripts/benchmark.py --iterations 300 --workers 16   # heavier concurrency
python scripts/benchmark.py --skip-writes                   # read-only run
```

Output: a grouped table on stdout, `scripts/benchmark_results.csv` (with a
`group` column) and a colour-coded log-scale chart
`scripts/benchmark_latency.png`. Illustrative excerpt (numbers depend on your
machine):

```
[reads]
MySQL trending (uncached)            1.842   …      542.8 ops/s
Redis cached                         0.071   …    14084.5 ops/s
[point]
MySQL point (PRIMARY KEY)            0.520   …
Redis point (GET)                    0.045   …
[writes]
MySQL write (plain INSERT)           1.10    …
MySQL write (INSERT + trigger)       1.65    …   ← delta ≈ trigger cost
[txn]
MySQL 50 INSERTs / 1 commit          12.4    …   (per 50-row batch)
MySQL 50 INSERTs / 50 commits        58.9    …
```

Headlines to expect: Redis ~20–30× faster than the uncached join; indexed
lookups beat full scans on both stores; one big commit beats fifty small ones;
`cpu/call` ≪ `avg` on the `[compute]` group proves the DB engine (not the
client) did the work.

## Running the logic tests

The test suite (`tests/`) has two layers:

| Layer | File | Needs Docker? | What it covers |
|---|---|---|---|
| **Unit** | `tests/test_unit.py` | No | Pure logic: open-redirect guard, date decoration, image validation/downscaling, error-message mapping, business constants. |
| **Integration** | `tests/test_integration.py` | Yes (datastores) | Real business rules end-to-end: seat triggers (book/oversell/cancel/refund), VIP-pricing trigger, the 6-ticket quota via the live route, CSRF rejection, Mongo like-idempotency, cross-store cascade on concert delete, headliner-lineup sync, Redis caching, sliding session TTL. |

### Why these test cases are required

Each test exists because something specific breaks silently without it:

| Test case(s) | Why it's required |
|---|---|
| Seat trigger: book / oversell / cancel / refund | The triggers are the **core integrity guarantee** of the whole app - money and inventory. A wrong trigger doesn't throw errors; it quietly corrupts `available_seats` (overselling a venue, or leaking seats on every refund - a real bug this suite caught). Only a before/after seat-count assertion proves rollback and restore actually happen. |
| VIP-pricing trigger | Business rules enforced *in the database* (not the app) can only be verified by attempting a violating INSERT and asserting the DB rejects it - app-level checks could pass while the trigger is broken or missing after a reseed. |
| 6-ticket quota via the live route | The quota spans **multiple rows and tiers** (a `SUM` across bookings), so no single constraint can enforce it - and it was previously race-prone. Driving the real route proves the locking transaction, not just the SQL, is correct. |
| Cross-store cascade on concert delete | MongoDB has **no foreign keys into MySQL** - nothing in any engine stops orphaned setlists/reviews/photo-blobs. The only safety net for the logical-FK boundary is a test that deletes and counts what's left. |
| Headliner-lineup sync | `concerts.headline_artist_id` and the slot-1 `concert_artists` row store the **same fact twice** (denormalisation). Anything stored twice can disagree; the test pins the sync code that keeps them consistent. |
| Mongo like-idempotency | "Helpful count" is derived from a set - if `$addToSet`/`$pull` were ever swapped for `$inc`, double-counting returns. The test encodes the invariant: liking twice = liking once. |
| CSRF rejection | Security controls fail **open**: if the guard is accidentally removed, every form still works and nothing visibly breaks. A test that asserts a token-less POST gets 400 is the only thing that notices. |
| Redis caching + sliding session TTL | Cache bugs are invisible (the page still renders - just slowly, or stale), and a non-sliding session logs users out mid-demo. Asserting the cache key is written and the TTL refreshes makes both observable. |
| Open-redirect guard, image validation (unit) | Input-handling edge cases (`//evil.com`, `/\evil.com`, fake/oversized images) are exactly what attackers and markers try first; pure-logic tests cover them in milliseconds with no Docker. |
| Business constants (unit) | Docs, templates and seed generator all assume max-6 tickets / 30-min sessions / upload caps. The test fails loudly if a constant changes so the dependents get updated together. |

Two further reasons the suite earns its place: it's the **regression net** for
the brief's evolving deliverables (every bug fixed during development got a
test so it can't return), and it's **proof of executability** for the
source-code submission - a marker can run `pytest` and watch the database
guarantees demonstrate themselves.

Integration tests **skip themselves automatically** if the datastores aren't
reachable, so a plain `pytest` is always safe. Every test cleans up after
itself - running against the seeded dev database is fine.

```bash
# One-time setup (same venv as the app):
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env

# Unit tests only - no Docker needed:
pytest tests/test_unit.py

# Full suite - start the datastores first:
docker compose up -d mysql mongo redis minio
pytest

# Or run inside the app container (all-Docker setup):
docker compose exec app pytest
```

## Security notes

- All SQL uses **parameterised queries** (no string interpolation) → injection-safe.
- Passwords are **bcrypt**-hashed (unique salt per user); sessions are random tokens
  in Redis with a **sliding 30-min idle timeout** (refreshed on each request).
- **CSRF tokens** on every state-changing form; **HttpOnly/SameSite** cookies; `Secure` when `FLASK_ENV=production`.
- `debug` is off unless `FLASK_DEBUG=1`; set a real `FLASK_SECRET` outside dev.
- Uploaded images are type-checked, size-capped (5 MB) and downscaled before storage.

## Mapping to the project brief

| Brief item | Where |
|---|---|
| Task 1 - application | This README + `docs/presentation_demo.md` |
| Task 2 - dataset | Synthetic seed (`scripts/generate_seed.py`); optional Kaggle import |
| Task 3 - ER + NoSQL schema | `docs/er_diagram.mermaid`, `docs/schema_design.md` |
| Task 4 - CRUD (SQL + NoSQL, wired into the app) | signup/booking/profile/admin + `sql/queries.sql`; review create/read/like/delete + `mongo/queries.py` |
| Task 5 - complex / triggers / SQL-vs-NoSQL | `sql/queries.sql` (nested, window, CTE, transaction, triggers); Mongo `$facet` + `$lookup` |
| Task 6 - GenAI reflection | Final report (see `docs/report_structure.md`) |
| Task 7 - performance | `scripts/benchmark.py` (6 groups: reads, point lookup, compute, writes incl. trigger cost, txn batching, concurrency) → CSV + chart |
| Task 8 - web UI + admin | `app/` + `/admin` |
| Data organisation & security | `docs/schema_design.md`; Security notes above |
| Object storage | `app/storage.py` + MinIO |
| Correctness verification | `tests/` (pytest) - unit + integration; see "Running the logic tests" |

# GigTrack — Project Reference

A file-by-file rundown of the codebase and a dependency overview.

## What each file does

### Root
| File | Purpose |
|---|---|
| `docker-compose.yml` | Orchestrates the 5 containers (MySQL, MongoDB, Redis, MinIO, the Flask app), maps host ports, injects env vars, and runs the boot sequence (Mongo seed → bucket → images → Flask). |
| `Dockerfile` | Builds the app image: Python 3.12-slim + the Python deps. |
| `requirements.txt` | Python dependencies (see table below). |
| `.env.example` | Template for local config; copy to `.env` (which is gitignored). |
| `.gitignore` | Excludes `__pycache__`, `.env`, build artifacts. |
| `README.md` | Setup (Docker + local), ports, demo script, security notes, brief mapping. |
| `LICENSE` | Licence text. |

### `sql/` — relational layer (MySQL)
| File | Purpose |
|---|---|
| `schema.sql` | 8 tables (`users`, `artists`, `venues`, `concerts`, `concert_artists`, `tickets`, `bookings`, `follows`) with primary/foreign keys, `CHECK` constraints, indexes, and **2 triggers** (decrement seats on booking; restore seats on cancellation). |
| `seed.sql` | **Generated** sample data (don't hand-edit — see `scripts/generate_seed.py`). 21 users, 30 artists, 15 venues, 50 concerts, 103 ticket tiers, ~180 bookings, 220 follows. |
| `queries.sql` | Reference queries: CRUD, joins, aggregation, nested/correlated subqueries, **window functions**, **CTE**, **explicit transaction**, **trigger demos**, a view, and an `EXPLAIN`. |

### `mongo/` — document layer (MongoDB)
| File | Purpose |
|---|---|
| `seed.py` | Seeds the `setlists`, `reviews`, `artist_bios` collections. Idempotent (skips if already populated unless `--force`); creates indexes incl. a text index. |
| `queries.py` | Representative Mongo queries: CRUD, aggregation pipelines, full-text search, plus **`$facet`** (multi-metric in one pass) and **`$lookup`** (join reviews↔setlists). |

### `app/` — Flask application
| File | Purpose |
|---|---|
| `app.py` | All routes + logic: auth (signup/login/logout with bcrypt + Redis sessions), CSRF protection, landing/browse, concert & artist pages, booking, reviews (with photo upload + image validation), like-toggle (dedup via `$addToSet`), profile, the **admin dashboard** (concert/ticket/user CRUD), info pages, healthcheck, and the Redis→MySQL view-count flush. |
| `db.py` | Loads `.env`, builds the MySQL config + Mongo/Redis singletons, and exposes `query_all` / `query_one` / `execute` / `get_mysql` helpers. |
| `storage.py` | S3-compatible (MinIO/AWS/R2) helper: `ensure_bucket`, `upload_fileobj`, `public_url`, `presigned_url`, `delete`. Holds the media *bytes*; the DB keeps only URL pointers. |
| `static/style.css` | The full UI theme (dark blue Ticketmaster-style), layout, and keyframe animations. |
| `templates/base.html` | Shared layout: sticky header, city dropdown, flash messages, multi-column footer. |
| `templates/landing.html` | Hero + search + trending cards (the `/` page). |
| `templates/home.html` | Full concert listing (`/concerts`), city-filterable. |
| `templates/concert_detail.html` | Concert info, lineup, tickets/booking, setlist + reviews (star widget, photo upload, like/delete). |
| `templates/artist_detail.html` | Artist bio, image, follow button, upcoming shows. |
| `templates/login.html` / `signup.html` / `profile.html` | Centered auth + profile forms. |
| `templates/my_bookings.html` | A user's bookings with cancel. |
| `templates/info.html` | Generic content page for footer links (About/Contact/etc.). |
| `templates/admin/*.html` | Admin dashboard, concert list/form, user list/form. |

### `scripts/` — tooling (not part of the running app)
| File | Purpose |
|---|---|
| `generate_seed.py` | Writes `sql/seed.sql` — deterministic synthetic data. Run when you want to change data volume/shape. |
| `fetch_artist_images.py` | Downloads a real placeholder portrait per artist (from Pravatar) and uploads it to MinIO, setting `artists.image_url`. Concurrent and idempotent; a fetch failure just leaves the gradient placeholder. (Replaced the old AI image generator; `generate_images.py` remains only as a deprecated shim that forwards here.) |
| `benchmark.py` | Performance benchmark: cache vs uncached, index vs scan (MySQL + Mongo), latency percentiles, throughput, **CPU time/call**, **peak memory** → `benchmark_results.csv` + `benchmark_latency.png`. |

### `docs/`
| File | Purpose |
|---|---|
| `er_diagram.mermaid` | Entity-relationship diagram (Mermaid). |
| `schema_design.md` | Why each datastore is used; schemas + the SQL↔NoSQL boundary discussion. |
| `data_flow.svg` | Sequence diagram of signup/login/book/review/logout across all four stores. |
| `demo_cli.md` | How to open each datastore's CLI + before/after demo scenarios. |
| `project_reference.md` | This document. |

## Dependencies and why they're here

### Python packages (`requirements.txt`)
| Package | Used for | Where |
|---|---|---|
| **Flask** | Web framework — routing, templating (Jinja2), sessions, request handling. | `app/app.py` |
| **PyMySQL** | Pure-Python MySQL driver. | `app/db.py` |
| **pymongo** | MongoDB driver. | `app/db.py`, `mongo/*` |
| **redis** | Redis client (sessions, cache, counters). | `app/db.py` |
| **bcrypt** | Salted password hashing + verification. | `app/app.py` |
| **python-dotenv** | Loads `.env` so the app + scripts share config. | `app/db.py` |
| **boto3** | S3-compatible client for MinIO/AWS/R2 object storage. | `app/storage.py` |
| **Pillow** | Image processing — validates/downscales uploaded review photos and normalises fetched artist portraits. | `app/app.py`, `scripts/fetch_artist_images.py` |
| **matplotlib** | Renders the benchmark latency chart (PNG). | `scripts/benchmark.py` |
| **psutil** | Measures the benchmark's CPU % and peak memory. Optional — falls back to the stdlib `resource` module. | `scripts/benchmark.py` |

### Backing services (containers in `docker-compose.yml`)
| Service | Image | Role |
|---|---|---|
| MySQL | `mysql:8.0` | Relational source of truth (identity, inventory, bookings, follows). |
| MongoDB | `mongo:7` | Document store (setlists, reviews, artist bios). |
| Redis | `redis:7-alpine` | Cache, session store, view counters. |
| MinIO | `minio/minio` | S3-compatible object storage for media blobs. |

### External service (runtime, optional)
| Service | Role | Notes |
|---|---|---|
| Pravatar (`i.pravatar.cc`) | Source of real placeholder portrait photos for artists. | Free, key-less, no rate limit. Fetched once at boot and stored in MinIO. Override with `AVATAR_BASE`; a fetch failure just leaves a gradient placeholder. |

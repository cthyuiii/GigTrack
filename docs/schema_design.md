# GigTrack — Schema Design

## 1. Relational layer (MySQL)

Used for **transactional, structured data with clear referential integrity**: identity, inventory, money, follow graph.

| Table | Purpose | Notable relationships |
|---|---|---|
| `users` | Account info | 1:N → bookings, follows, reviews |
| `artists` | Performing acts | 1:N → concerts (as headliner); M:N ↔ concerts via `concert_artists` |
| `venues` | Physical locations | 1:N → concerts |
| `concerts` | A show at a venue on a date | M:N ↔ artists; 1:N → tickets |
| `concert_artists` | Junction for multi-artist lineups (support acts, festivals) | M:N |
| `tickets` | Pricing tier inventory per concert | 1:N → bookings |
| `bookings` | A user's purchase of N tickets | N:1 → users, tickets |
| `follows` | M:N user ↔ artist relationship | M:N |

Key constraints:
- `ticket.available_seats >= 0` enforced by trigger on `bookings` insert.
- Cascade delete from `concerts` → `tickets` → soft-handle `bookings` (status=`cancelled`) rather than hard delete.
- Composite PKs on `concert_artists` and `follows`.

## 2. Document layer (MongoDB)

Used for **semi-structured, variable-shape, append-mostly** data that would be painful to normalize.

### `setlists` collection
```json
{
  "_id": "ObjectId",
  "concert_id": 42,
  "submitted_by_user_id": 7,
  "source": "fan",
  "songs": [
    {"order": 1, "title": "Welcome to the Black Parade", "duration_sec": 312, "encore": false, "cover_of": null, "notes": "Extended intro"},
    {"order": 2, "title": "Helena", "duration_sec": 215, "encore": false, "cover_of": null}
  ],
  "encore_songs": [
    {"order": 1, "title": "Cancer", "duration_sec": 134, "cover_of": null}
  ],
  "total_duration_sec": 6890,
  "submitted_at": "2026-03-14T22:30:00Z",
  "upvotes": 18
}
```
Justification: setlist length varies wildly (10–40 songs), each song has optional metadata (cover_of, guest performer, notes). A normalized SQL schema would need a `setlist_songs` table with many nullable columns and joins on every read. A document is a natural fit and is read whole.

### `reviews` collection
```json
{
  "_id": "ObjectId",
  "concert_id": 42,
  "user_id": 7,
  "rating": 5,
  "title": "Best show I've seen all year",
  "body": "Sound mix was crisp, crowd energy unreal...",
  "tags": ["sound-quality", "crowd", "lights"],
  "photos": [
    {"url": "/uploads/r1_1.jpg", "caption": "Opening pyro"}
  ],
  "helpful_count": 23,
  "posted_at": "2026-03-15T09:14:00Z"
}
```
Justification: photo arrays and tag arrays are variable-length; body is unstructured text we may want full-text search on later.

### `artist_bios` collection
```json
{
  "_id": "ObjectId",
  "artist_id": 12,
  "bio_text": "Formed in Newark in 2001...",
  "tour_history": [
    {"year": 2007, "name": "The Black Parade World Tour", "regions": ["NA", "EU", "ASIA"]}
  ],
  "social": {"instagram": "@...", "spotify_id": "..."},
  "related_artists": [15, 22, 31]
}
```

## 3. Cache layer (Redis)

| Key pattern | Type | Purpose | TTL |
|---|---|---|---|
| `concert:{id}:views` | counter | pending view delta; drained into MySQL `concerts.view_count` by `flush_view_counts()` before any view-ordered list renders (GETDEL = atomic, lossless) | none |
| `trending:city:{city}` / `trending:all` | string (JSON) | cached home/trending listing | 5 min |
| `search:artist:{prefix}` | set | autocomplete | 1 hr |
| `session:{token}` | string (user_id) | auth session | 30 min |
| `concert:{id}:detail` | hash | cached detail-page payload | 60 s |

This gives a clear demoable speedup on the home/detail pages and offloads view-counting from MySQL.

## 4. Object storage (file uploads)

Review photos (binary media) are stored in an **S3-compatible object store** — MinIO locally (`app/storage.py`, `gigtrack-media` bucket), swappable for AWS S3 / Cloudflare R2 by changing only the endpoint + credentials.

**The blob lives in object storage; the database keeps only a pointer.** A review document in Mongo stores:
```json
"photos": [
  {"url": "http://localhost:9000/gigtrack-media/reviews/6/<uuid>.jpg",
   "key": "reviews/6/<uuid>.jpg",
   "caption": "Stage from balcony"}
]
```
Justification: documents have a 16 MB cap, and putting image bytes in MySQL/Mongo bloats the working set, slows backups/replication, and prevents CDN delivery. Object storage is purpose-built for large immutable blobs and is cheap and effectively unbounded. The three-tier media model is: MySQL owns the relational subject (which user/concert), Mongo owns the *structured photo metadata* (caption, order), and the object store owns the *bytes*.

The demo uses a public-read bucket policy so `<img src>` works directly; `storage.presigned_url()` shows the private-bucket (time-limited signed URL) alternative for production.

## 5. SQL ↔ NoSQL boundary

- Mongo documents reference MySQL rows by integer `concert_id` / `user_id` / `artist_id`. This is a **logical foreign key** — Mongo does not enforce it; the application layer does.
- The two designs are **independent but reconciled at the app layer**. This is the realistic enterprise pattern and is the discussion point for project task 5.
- Pros of independence: each store optimizes for its access pattern; schema evolution in Mongo doesn't require migrations in MySQL. Cons: dangling references possible; we need to handle "concert deleted but setlist still exists" (we'll cascade in app code).

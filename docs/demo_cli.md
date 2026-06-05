# Demo cheat-sheet — showing live changes in all four datastores

Yes — keeping a CLI open for each store is the single most convincing way to
prove the app really writes to all four backends. Put the browser on one side
and the four terminals on the other, do an action in the UI, then re-run a
query to show the row/document/key/object change.

Open each CLI with `docker compose exec` (run from the project root while the
stack is up). Inside the containers the default ports apply.

| Store   | Open the CLI |
|---------|--------------|
| MySQL   | `docker compose exec mysql mysql -ugigtrack -pgigtrack_pw gigtrack` |
| MongoDB | `docker compose exec mongo mongosh gigtrack` |
| Redis   | `docker compose exec redis redis-cli` |
| MinIO   | Web console at <http://localhost:9001> (`minioadmin` / `minioadmin`) — or the `mc` one-liner below |

MinIO via the `mc` client (optional, no install needed — throwaway container on
the compose network; the network name is usually `<folder>_default`, check with
`docker network ls`):

```bash
docker run --rm --network gigtrack_default minio/mc sh -c \
  "mc alias set local http://minio:9000 minioadmin minioadmin && mc ls -r local/gigtrack-media"
```

---

## Scenario A — Sign up + Log in  → MySQL + Redis

1. **MySQL (before):** `SELECT user_id, username, is_admin FROM users ORDER BY user_id DESC LIMIT 3;`
2. In the UI, **sign up** a new account.
3. **MySQL (after):** re-run the query → the new row appears, `password_hash`
   is a bcrypt string (show it's not plaintext):
   `SELECT username, password_hash FROM users ORDER BY user_id DESC LIMIT 1;`
4. **Redis (before):** `KEYS session:*`
5. **Log in** in the UI.
6. **Redis (after):** `KEYS session:*` shows a new token; prove it expires:
   `TTL session:<paste-token>` → ~1800 seconds. The value is the user id:
   `GET session:<token>`.
7. **Log out**, then `KEYS session:*` → the key is gone.

> Tip: run `MONITOR` in a spare Redis terminal to watch `SETEX`/`DEL` happen live.

## Scenario B — Book a ticket  → MySQL (trigger in action)

1. Pick a ticket tier on a concert page. In MySQL:
   `SELECT ticket_id, tier, available_seats FROM tickets WHERE concert_id = <id>;`
2. **Book** N seats in the UI.
3. Re-run the query → `available_seats` dropped by N (the `BEFORE INSERT`
   trigger), and `SELECT * FROM bookings ORDER BY booking_id DESC LIMIT 1;`
   shows the new booking.
4. **Oversell test:** try to book more than remain → the trigger raises and the
   app shows a safe error; `available_seats` is unchanged (transaction rolled back).
5. **Cancel** the booking under *My bookings* → re-run step 1: seats are
   restored by the `AFTER UPDATE` trigger.

## Scenario C — Write & like a review  → MongoDB + MinIO

1. **MongoDB (before):** `db.reviews.countDocuments({concert_id: <id>})`
2. **Post a review with a photo** in the UI.
3. **MongoDB (after):**
   `db.reviews.find({concert_id: <id>}).sort({posted_at:-1}).limit(1).pretty()`
   → new document; note `photos: [{ url, key, caption }]` (a pointer, not bytes).
4. **MinIO:** in the console (or `mc ls -r local/gigtrack-media`) the uploaded
   object appears under `reviews/<concert_id>/…` — the bytes live here, not in Mongo.
5. **Like-dedup:** click 👍, then in Mongo:
   `db.reviews.findOne({_id: <id>}, {liked_by:1})` → your id is in `liked_by`.
   Click 👍 again → run it again → the array is **unchanged** (no duplicate).

## Scenario D — Caching & view counter  → Redis (+ MySQL)

1. Open a concert detail page a few times. In Redis:
   `GET concert:<id>:views` → a pending counter.
2. Load the concerts list (`/concerts`). In MySQL:
   `SELECT view_count FROM concerts WHERE concert_id = <id>;` → the pending
   delta was flushed into `view_count` (and the Redis key reset).
3. **Cache demo:** load `/concerts?city=Singapore`, then in Redis:
   `KEYS browse:*` and `TTL browse:Singapore:*` → the cached list with a
   ~300s TTL. The second page load is served from this key (watch with `MONITOR`).

## Scenario E — Admin  → MySQL

1. Log in as `macc@example.com` / `password`, open **/admin**.
2. Create or edit a concert / ticket tier. In MySQL:
   `SELECT concert_id, title, status FROM concerts ORDER BY concert_id DESC LIMIT 3;`
   → the change is there immediately.
3. Disable a user in the dashboard → `SELECT username, is_active FROM users WHERE …;`
   shows `is_active = 0`, and that user can no longer log in.

---

### Handy one-liners

```sql
-- MySQL: revenue leaderboard (window function)
SELECT * FROM (
  SELECT v.city, c.title, COALESCE(SUM(b.total_price),0) rev,
         RANK() OVER (PARTITION BY v.city ORDER BY COALESCE(SUM(b.total_price),0) DESC) rk
  FROM concerts c JOIN venues v ON v.venue_id=c.venue_id
  LEFT JOIN tickets t ON t.concert_id=c.concert_id
  LEFT JOIN bookings b ON b.ticket_id=t.ticket_id AND b.status='confirmed'
  GROUP BY v.city, c.concert_id) r WHERE rk=1;
```

```javascript
// MongoDB: rating distribution in one pass ($facet)
db.reviews.aggregate([{ $facet: {
  byRating: [{ $group: { _id: "$rating", n: { $sum: 1 } } }, { $sort: { _id: -1 } }],
  topTags:  [{ $unwind: "$tags" }, { $group: { _id: "$tags", n: { $sum: 1 } } },
             { $sort: { n: -1 } }, { $limit: 5 }]
}}])
```

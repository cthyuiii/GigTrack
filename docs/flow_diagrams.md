# GigTrack — Flow Diagrams (for slides & demo)

Mermaid diagrams of the key request flows, one per demo scenario. Paste into
[mermaid.live](https://mermaid.live) (or any Mermaid renderer) to export PNG/SVG
for the slides. Each diagram maps to a presenter segment in
`presentation_demo.md` and a CLI scenario in `demo_cli.md`.

---

## 1. Login & session flow — *Member E segment / Scenario A*

Redis-backed sessions with bcrypt verification and a **sliding** 30-minute
idle timeout.

```mermaid
sequenceDiagram
    actor U as Browser
    participant F as Flask
    participant M as MySQL
    participant R as Redis

    U->>F: POST /login (email, password, CSRF token)
    F->>M: SELECT user_id, password_hash, is_active WHERE email=?
    M-->>F: row
    alt account disabled
        F-->>U: "This account has been disabled."
    else bcrypt.checkpw OK
        F->>R: SETEX session:&lt;token&gt; 1800 user_id
        F-->>U: Set-Cookie (HttpOnly, SameSite=Lax) + redirect
    else wrong password
        F-->>U: "Invalid credentials"
    end

    Note over U,R: every later authenticated request
    U->>F: GET /concerts (cookie)
    F->>R: GET session:&lt;token&gt; → user_id
    F->>R: EXPIRE session:&lt;token&gt; 1800  (sliding refresh)
    F->>M: SELECT user row (incl. is_active)
```

---

## 2. Ticket booking — quota + seat trigger — *Member C segment / Scenario B*

The quota check and the INSERT run in **one locking transaction**; the
`BEFORE INSERT` trigger enforces inventory atomically and rolls back on
oversell.

```mermaid
flowchart TD
    A[POST /concerts/id/book] --> B{Logged in,<br>not admin?}
    B -- no --> X1[Redirect to login /<br>admin blocked]
    B -- yes --> C{1 &le; qty &le; 6?}
    C -- no --> X2[Flash: choose 1–6]
    C -- yes --> T[BEGIN TRANSACTION]
    T --> L["SELECT tickets of concert FOR UPDATE<br>(serialises concurrent bookings)"]
    L --> Q{"SUM(confirmed qty) + qty &le; 6?"}
    Q -- no --> X3[Flash: limit reached<br>ROLLBACK - no writes]
    Q -- yes --> I[INSERT INTO bookings]
    I --> TR{"TRIGGER trg_booking_decrement_seats:<br>available_seats &ge; qty?"}
    TR -- no --> X4[SIGNAL 45000<br>whole transaction ROLLS BACK]
    TR -- yes --> D[UPDATE tickets SET<br>available_seats -= qty]
    D --> CM[COMMIT] --> OK[Flash: Booked N - total $X]
```

---

## 3. Review with photo — pointer vs blob — *Member D segment / Scenario C*

Bytes go to object storage; both databases keep only pointers.

```mermaid
sequenceDiagram
    actor U as Browser
    participant F as Flask
    participant M as MySQL
    participant P as Pillow
    participant S as MinIO (S3)
    participant D as MongoDB

    U->>F: POST /concerts/id/review (rating, text, photos[])
    F->>M: concert exists? (404 if not)
    loop each photo
        F->>P: validate type/size, verify, downscale to ≤1600px JPEG
        P-->>F: clean JPEG bytes
        F->>S: upload reviews/<concert>/<uuid>.jpg
        S-->>F: public URL
    end
    F->>D: insert review {rating, title, body, tags,<br>photos:[{url,key,caption}], liked_by:[]}
    F-->>U: redirect + flash

    Note over U,D: like toggle = one atomic update
    U->>F: POST /reviews/<id>/like
    F->>D: $addToSet / $pull liked_by (idempotent — never double-counts)
```

---

## 4. Browse caching & view counters — *Member E/F segments / Scenario D*

Cache-aside for listings; write-behind (atomic `GETDEL`) for view counts.

```mermaid
flowchart TD
    subgraph "GET /concerts?city=&genre="
        A[Request] --> FL["flush_view_counts():<br>GETDEL concert:*:views → UPDATE MySQL view_count"]
        FL --> K{Redis GET<br>browse:when:city:genre?}
        K -- hit --> H[Serve cached JSON<br>~20–30x faster]
        K -- miss --> Q[MySQL 3-table JOIN<br>upcoming/past/all filter,<br>ORDER BY concert_date]
        Q --> W[SETEX browse:key 300s] --> H2[Render]
    end

    subgraph "GET /concerts/id"
        V[Request] --> E{Concert exists?}
        E -- no --> N[404 — no counter key created]
        E -- yes --> I[Redis INCR concert:id:views<br>cheap, no MySQL write]
    end

    subgraph "Admin edits"
        AD[Create/edit/delete concert] --> BU["_bust_trending_cache():<br>DEL browse:* cities genres"]
    end
```

---

## 5. Concert delete — cross-store cascade — *Member F segment / Scenario E*

MySQL cascades via FKs; the app completes the cascade across the logical-FK
boundary into MongoDB and MinIO (no orphans).

```mermaid
flowchart TD
    A[POST /admin/concerts/id/delete] --> B{DELETE FROM concerts}
    B -- "FK RESTRICT (bookings exist)" --> X[Flash: delete failed<br>nothing removed anywhere]
    B -- success --> C["MySQL FK cascade:<br>tickets + concert_artists rows gone"]
    C --> D["MinIO: delete each review photo blob<br>(keys from review documents)"]
    D --> E["MongoDB: delete_many reviews,<br>delete_many setlists for concert_id"]
    E --> F["Redis: bust browse/cities/genres caches,<br>DEL concert:id:views"]
    F --> G[Flash: concert + setlists,<br>reviews, photos deleted]
```

---

## 6. Where each datastore is used (architecture recap)

`data_flow.svg` has the full sequence diagram; this is the slide-friendly
summary.

```mermaid
flowchart LR
    B[Browser] --> F[Flask app]
    F -->|identity, inventory,<br>bookings, follows<br>FKs + CHECKs + 4 triggers| MY[(MySQL 8)]
    F -->|setlists, reviews,<br>artist bios<br>$facet / $lookup| MO[(MongoDB 7)]
    F -->|sessions, listing cache,<br>view counters| RD[(Redis 7)]
    F -->|image bytes<br>pointer kept in DBs| MN[(MinIO / S3)]
```

-- ============================================================
-- GigTrack — Sample SQL queries
-- CRUD, joins, aggregation, nested/correlated subqueries, window
-- functions, CTEs, transactions, and trigger demonstrations.
-- Each block is independently runnable after schema.sql + seed.sql.
-- ============================================================

-- ------------------------------------------------------------
-- (A) CRUD — basic
-- ------------------------------------------------------------

-- A1. Create — register a new user
INSERT INTO users (username, email, password_hash, home_city)
VALUES ('new_user', 'new@example.com', '$2b$12$placeholderhash', 'Singapore');

-- A2. Read — get a single concert with venue + headliner name
SELECT c.concert_id, c.title, c.concert_date,
       v.name AS venue, v.city,
       a.name AS headliner
FROM   concerts c
JOIN   venues  v ON v.venue_id  = c.venue_id
JOIN   artists a ON a.artist_id = c.headline_artist_id
WHERE  c.concert_id = 1;

-- A3. Update — change a concert status
UPDATE concerts
SET    status = 'sold_out'
WHERE  concert_id = 6;

-- A4. Delete — remove a follow
DELETE FROM follows
WHERE  user_id = 2 AND artist_id = 3;

-- ------------------------------------------------------------
-- (B) Joins — many-to-many resolution
-- ------------------------------------------------------------

-- B1. Full lineup for a concert (headliner + support), ordered
SELECT ca.slot_order, a.name, ca.role
FROM   concert_artists ca
JOIN   artists a ON a.artist_id = ca.artist_id
WHERE  ca.concert_id = 1
ORDER  BY ca.slot_order;

-- B2. All upcoming concerts in Singapore, with cheapest ticket per concert
SELECT c.concert_id, c.title, c.concert_date, v.name AS venue,
       MIN(t.price) AS cheapest_ticket
FROM   concerts c
JOIN   venues  v ON v.venue_id  = c.venue_id
JOIN   tickets t ON t.concert_id = c.concert_id
WHERE  v.city = 'Singapore'
  AND  c.concert_date > NOW()
  AND  c.status = 'scheduled'
GROUP  BY c.concert_id, c.title, c.concert_date, v.name
ORDER  BY c.concert_date;

-- ------------------------------------------------------------
-- (C) Aggregation — revenue & engagement reporting
-- ------------------------------------------------------------

-- C1. Revenue by concert (only confirmed bookings)
SELECT c.concert_id, c.title,
       COUNT(b.booking_id)             AS booking_count,
       COALESCE(SUM(b.total_price), 0) AS revenue
FROM   concerts c
LEFT   JOIN tickets  t ON t.concert_id = c.concert_id
LEFT   JOIN bookings b ON b.ticket_id  = t.ticket_id AND b.status = 'confirmed'
GROUP  BY c.concert_id, c.title
ORDER  BY revenue DESC;

-- C2. Top genres by follower count
SELECT a.genre, COUNT(f.user_id) AS followers
FROM   artists a
LEFT   JOIN follows f ON f.artist_id = a.artist_id
GROUP  BY a.genre
ORDER  BY followers DESC;

-- ------------------------------------------------------------
-- (D) Nested / correlated subqueries
-- ------------------------------------------------------------

-- D1. Concerts whose headliner has more followers than the average artist
SELECT c.concert_id, c.title, a.name AS headliner,
       (SELECT COUNT(*) FROM follows f WHERE f.artist_id = a.artist_id) AS follower_count
FROM   concerts c
JOIN   artists a ON a.artist_id = c.headline_artist_id
WHERE  (SELECT COUNT(*) FROM follows f WHERE f.artist_id = a.artist_id) >
       (SELECT AVG(cnt) FROM (
            SELECT COUNT(*) AS cnt FROM follows GROUP BY artist_id
        ) sub);

-- D2. Users who have booked into every concert headlined by an artist they follow
--     (relational division pattern — classic "for every" using NOT EXISTS)
SELECT DISTINCT u.username
FROM   users u
JOIN   follows f ON f.user_id = u.user_id
WHERE  NOT EXISTS (
   SELECT 1
   FROM   concerts c
   WHERE  c.headline_artist_id = f.artist_id
     AND  c.status IN ('scheduled','completed')
     AND  NOT EXISTS (
         SELECT 1 FROM bookings b
         JOIN   tickets t ON t.ticket_id = b.ticket_id
         WHERE  b.user_id = u.user_id
           AND  t.concert_id = c.concert_id
           AND  b.status = 'confirmed'
     )
);

-- D3. Concerts at risk of selling out (less than 10% inventory left)
SELECT c.concert_id, c.title,
       SUM(t.available_seats) AS seats_left,
       SUM(t.total_seats)     AS seats_total,
       ROUND(100 * SUM(t.available_seats) / SUM(t.total_seats), 1) AS pct_left
FROM   concerts c
JOIN   tickets  t ON t.concert_id = c.concert_id
WHERE  c.status = 'scheduled'
GROUP  BY c.concert_id, c.title
HAVING pct_left < 10
ORDER  BY pct_left;

-- ------------------------------------------------------------
-- (E) Trigger demonstrations
-- ------------------------------------------------------------

-- E1. Successful booking — trigger decrements available_seats
SELECT available_seats AS before_seats FROM tickets WHERE ticket_id = 1;
INSERT INTO bookings (user_id, ticket_id, quantity, total_price)
VALUES (2, 1, 3, 264.00);
SELECT available_seats AS after_seats  FROM tickets WHERE ticket_id = 1;

-- E2. Oversell attempt — trigger raises and transaction is rolled back
-- Expected error: 'Not enough seats available for this ticket tier'
INSERT INTO bookings (user_id, ticket_id, quantity, total_price)
VALUES (1, 10, 5, 375.00);

-- E3. Cancellation restores seats
UPDATE bookings SET status = 'cancelled' WHERE booking_id = 1;
SELECT available_seats FROM tickets WHERE ticket_id = 1;

-- ------------------------------------------------------------
-- (F) Useful view (optional; cleans up app-side queries)
-- ------------------------------------------------------------
DROP VIEW IF EXISTS v_concert_summary;
CREATE VIEW v_concert_summary AS
SELECT c.concert_id, c.title, c.concert_date, c.status,
       v.name AS venue, v.city,
       a.name AS headliner, a.genre,
       (SELECT MIN(price) FROM tickets t WHERE t.concert_id = c.concert_id) AS from_price,
       (SELECT SUM(available_seats) FROM tickets t WHERE t.concert_id = c.concert_id) AS seats_left
FROM   concerts c
JOIN   venues  v ON v.venue_id  = c.venue_id
JOIN   artists a ON a.artist_id = c.headline_artist_id;

-- ------------------------------------------------------------
-- (G) Window functions — ranking & running totals (MySQL 8.0+)
-- ------------------------------------------------------------

-- G1. Rank concerts by revenue WITHIN each city (partitioned window).
--     RANK() lets us answer "top earner per city" without a self-join.
SELECT *
FROM (
    SELECT v.city, c.concert_id, c.title,
           COALESCE(SUM(b.total_price), 0) AS revenue,
           RANK() OVER (PARTITION BY v.city
                        ORDER BY COALESCE(SUM(b.total_price), 0) DESC) AS city_rank
    FROM   concerts c
    JOIN   venues   v ON v.venue_id  = c.venue_id
    LEFT   JOIN tickets  t ON t.concert_id = c.concert_id
    LEFT   JOIN bookings b ON b.ticket_id  = t.ticket_id AND b.status = 'confirmed'
    GROUP  BY v.city, c.concert_id, c.title
) ranked
WHERE city_rank <= 3
ORDER BY city, city_rank;

-- G2. Running cumulative revenue over time (frame from start to current row).
SELECT b.booking_id, b.booked_at, b.total_price,
       SUM(b.total_price) OVER (ORDER BY b.booked_at
                                ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
                                AS cumulative_revenue
FROM   bookings b
WHERE  b.status = 'confirmed'
ORDER  BY b.booked_at;

-- ------------------------------------------------------------
-- (H) Common Table Expression (CTE) — readable multi-step query
-- ------------------------------------------------------------

-- H1. Artists whose average headliner-show rating beats the global average.
--     (rating data lives in Mongo; here we assume an app-side sync table, but
--      the pattern stands: CTEs name intermediate results for clarity.)
WITH concert_revenue AS (
    SELECT c.concert_id, c.headline_artist_id,
           COALESCE(SUM(b.total_price), 0) AS revenue
    FROM   concerts c
    LEFT   JOIN tickets  t ON t.concert_id = c.concert_id
    LEFT   JOIN bookings b ON b.ticket_id  = t.ticket_id AND b.status = 'confirmed'
    GROUP  BY c.concert_id, c.headline_artist_id
),
artist_totals AS (
    SELECT headline_artist_id AS artist_id,
           SUM(revenue)  AS total_revenue,
           COUNT(*)      AS shows
    FROM   concert_revenue
    GROUP  BY headline_artist_id
)
SELECT a.name, at.shows, at.total_revenue,
       ROUND(at.total_revenue / NULLIF(at.shows, 0), 2) AS avg_per_show
FROM   artist_totals at
JOIN   artists a ON a.artist_id = at.artist_id
WHERE  at.total_revenue > (SELECT AVG(total_revenue) FROM artist_totals)
ORDER  BY at.total_revenue DESC;

-- ------------------------------------------------------------
-- (I) Explicit transaction — atomic multi-statement money move
-- ------------------------------------------------------------

-- I1. Refund a booking atomically: mark refunded AND log it. Either both
--     succeed or neither does. (The seat-restore is handled by the existing
--     AFTER UPDATE trigger when status changes.)
START TRANSACTION;
  UPDATE bookings SET status = 'refunded' WHERE booking_id = 2 AND status = 'confirmed';
  -- imagine an audit_log insert here; if it failed we ROLLBACK instead:
  -- INSERT INTO audit_log (...) VALUES (...);
COMMIT;

-- ------------------------------------------------------------
-- (J) EXPLAIN — show the optimizer using our indexes
-- ------------------------------------------------------------

-- J1. Confirms idx_concerts_status_date is used for the home/trending query.
EXPLAIN
SELECT c.concert_id, c.title
FROM   concerts c
WHERE  c.status = 'scheduled'
ORDER  BY c.concert_date
LIMIT  20;

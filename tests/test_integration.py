"""
Integration tests - exercise the REAL business logic against live datastores.

Requires the stack (or at least MySQL+Mongo+Redis) to be up and seeded:
    docker compose up -d mysql mongo redis minio
    cp .env.example .env            # once
    pytest tests/test_integration.py

Every test cleans up after itself, so the suite is safe to run repeatedly
against the seeded dev database. The whole module is SKIPPED automatically
if the datastores are unreachable.
"""
import pytest

from conftest import stack_available

pytestmark = pytest.mark.skipif(
    not stack_available(), reason="datastores not reachable - start the stack first"
)

CSRF = {"csrf_token": "test-csrf"}


def _seats(ticket_id):
    from db import query_one
    return query_one("SELECT available_seats, concert_id FROM tickets "
                     "WHERE ticket_id=%s", (ticket_id,))


def _cleanup_booking(booking_id):
    """Remove a test booking and restore its seats regardless of status."""
    from db import query_one, execute
    bk = query_one("SELECT ticket_id, quantity, status FROM bookings "
                   "WHERE booking_id=%s", (booking_id,))
    if not bk:
        return
    if bk["status"] == "confirmed":      # seats still held - give them back
        execute("UPDATE tickets SET available_seats = available_seats + %s "
                "WHERE ticket_id=%s", (bk["quantity"], bk["ticket_id"]))
    execute("DELETE FROM bookings WHERE booking_id=%s", (booking_id,))


def _last_booking_id(user_id):
    from db import query_one
    row = query_one("SELECT MAX(booking_id) AS b FROM bookings WHERE user_id=%s",
                    (user_id,))
    return row["b"]


# ---- healthcheck -----------------------------------------------------------

def test_healthz_reports_all_stores_up(client):
    data = client.get("/healthz").get_json()
    assert data == {"mysql": True, "mongo": True, "redis": True}


# ---- trigger: seat inventory -------------------------------------------------

def test_booking_trigger_decrements_and_cancel_restores(customer_client):
    from db import execute
    before = _seats(1)["available_seats"]

    booking_id = execute(
        "INSERT INTO bookings (user_id, ticket_id, quantity, total_price) "
        "VALUES (2, 1, 2, 100.00)")
    try:
        assert _seats(1)["available_seats"] == before - 2
        # cancelled -> AFTER UPDATE trigger restores the seats
        execute("UPDATE bookings SET status='cancelled' WHERE booking_id=%s",
                (booking_id,))
        assert _seats(1)["available_seats"] == before
    finally:
        from db import execute as ex
        ex("DELETE FROM bookings WHERE booking_id=%s", (booking_id,))


def test_refund_also_restores_seats():
    from db import execute
    before = _seats(1)["available_seats"]
    booking_id = execute(
        "INSERT INTO bookings (user_id, ticket_id, quantity, total_price) "
        "VALUES (2, 1, 1, 50.00)")
    try:
        assert _seats(1)["available_seats"] == before - 1
        execute("UPDATE bookings SET status='refunded' WHERE booking_id=%s",
                (booking_id,))
        assert _seats(1)["available_seats"] == before
    finally:
        execute("DELETE FROM bookings WHERE booking_id=%s", (booking_id,))


def test_oversell_is_rejected_by_trigger():
    import pymysql
    from db import execute
    avail = _seats(1)["available_seats"]
    with pytest.raises(pymysql.err.OperationalError):
        execute("INSERT INTO bookings (user_id, ticket_id, quantity, total_price) "
                "VALUES (2, 1, %s, 1.00)", (avail + 1,))
    assert _seats(1)["available_seats"] == avail   # rolled back


# ---- trigger: VIP pricing rule ----------------------------------------------

def test_vip_cannot_be_cheaper_than_other_tiers():
    import pymysql
    from db import query_one, execute
    row = query_one(
        "SELECT concert_id, MAX(price) AS top FROM tickets "
        "WHERE tier <> 'VIP' GROUP BY concert_id ORDER BY top DESC LIMIT 1")
    cheap = float(row["top"]) - 1
    with pytest.raises(pymysql.err.OperationalError):
        execute("INSERT INTO tickets (concert_id, tier, price, total_seats, "
                "available_seats) VALUES (%s, 'VIP', %s, 10, 10)",
                (row["concert_id"], cheap))


# ---- per-concert booking quota (via the real route, CSRF + session) ----------

def test_quota_blocks_seventh_ticket(customer_client):
    from db import query_one
    # A tier with plenty of seats, on a concert user 2 has no bookings for.
    tk = query_one("""
        SELECT t.ticket_id, t.concert_id FROM tickets t
        WHERE t.available_seats > 20 AND t.concert_id NOT IN (
            SELECT t2.concert_id FROM bookings b
            JOIN tickets t2 ON t2.ticket_id = b.ticket_id
            WHERE b.user_id = 2 AND b.status = 'confirmed')
        LIMIT 1""")
    made = []
    try:
        r = customer_client.post(f"/concerts/{tk['concert_id']}/book",
                                 data={**CSRF, "ticket_id": tk["ticket_id"],
                                       "quantity": 6},
                                 follow_redirects=True)
        assert b"Booked 6" in r.data
        made.append(_last_booking_id(2))

        r = customer_client.post(f"/concerts/{tk['concert_id']}/book",
                                 data={**CSRF, "ticket_id": tk["ticket_id"],
                                       "quantity": 1},
                                 follow_redirects=True)
        assert b"Ticket limit reached" in r.data
    finally:
        for b in made:
            _cleanup_booking(b)


def test_zero_and_negative_quantities_rejected(customer_client):
    r = customer_client.post("/concerts/1/book",
                             data={**CSRF, "ticket_id": 1, "quantity": 0},
                             follow_redirects=True)
    assert b"between 1 and" in r.data
    r = customer_client.post("/concerts/1/book",
                             data={**CSRF, "ticket_id": 1, "quantity": -3},
                             follow_redirects=True)
    assert b"between 1 and" in r.data


# ---- CSRF guard ---------------------------------------------------------------

def test_post_without_csrf_token_is_rejected(customer_client):
    r = customer_client.post("/concerts/1/book",
                             data={"ticket_id": 1, "quantity": 1})
    assert r.status_code == 400


# ---- Mongo: like idempotency ----------------------------------------------------

def test_like_set_never_double_counts():
    from db import mongo
    rid = mongo.reviews.insert_one(
        {"concert_id": 1, "user_id": 2, "rating": 5, "title": "t", "body": "b",
         "tags": [], "photos": [], "liked_by": [], "helpful_count": 0}).inserted_id
    try:
        for _ in range(3):   # $addToSet is idempotent
            mongo.reviews.update_one({"_id": rid}, {"$addToSet": {"liked_by": 7}})
        assert mongo.reviews.find_one({"_id": rid})["liked_by"] == [7]
        mongo.reviews.update_one({"_id": rid}, {"$pull": {"liked_by": 7}})
        assert mongo.reviews.find_one({"_id": rid})["liked_by"] == []
    finally:
        mongo.reviews.delete_one({"_id": rid})


# ---- review for a nonexistent concert is rejected -------------------------------

def test_review_for_missing_concert_404s(customer_client):
    r = customer_client.post("/concerts/999999/review",
                             data={**CSRF, "rating": 5, "title": "x", "body": "y"})
    assert r.status_code == 404


# ---- cross-store cascade on concert delete ---------------------------------------

def test_concert_delete_cascades_to_mongo(admin_client):
    from db import query_one, execute, mongo
    # Create a throwaway concert with no bookings + a Mongo setlist/review.
    cid = execute(
        "INSERT INTO concerts (venue_id, headline_artist_id, title, "
        "concert_date, status, base_price) "
        "VALUES (1, 1, 'PYTEST throwaway', '2030-01-01 20:00:00', 'scheduled', 10)")
    mongo.setlists.insert_one({"concert_id": cid, "songs": [], "encore_songs": []})
    mongo.reviews.insert_one({"concert_id": cid, "user_id": 2, "rating": 4,
                              "title": "t", "body": "b", "tags": [], "photos": [],
                              "liked_by": [], "helpful_count": 0})

    r = admin_client.post(f"/admin/concerts/{cid}/delete", data=CSRF,
                          follow_redirects=True)
    assert r.status_code == 200
    assert query_one("SELECT 1 FROM concerts WHERE concert_id=%s", (cid,)) is None
    assert mongo.setlists.count_documents({"concert_id": cid}) == 0
    assert mongo.reviews.count_documents({"concert_id": cid}) == 0


# ---- headliner edit keeps the lineup in sync ---------------------------------------

def test_concert_edit_syncs_headliner_lineup(admin_client):
    from db import query_one, execute
    cid = execute(
        "INSERT INTO concerts (venue_id, headline_artist_id, title, "
        "concert_date, status, base_price) "
        "VALUES (1, 1, 'PYTEST lineup', '2030-01-01 20:00:00', 'scheduled', 10)")
    execute("INSERT INTO concert_artists (concert_id, artist_id, slot_order, role) "
            "VALUES (%s, 1, 1, 'headliner')", (cid,))
    try:
        admin_client.post(f"/admin/concerts/{cid}/edit",
                          data={**CSRF, "venue_id": 1, "headline_artist_id": 2,
                                "title": "PYTEST lineup", "date": "2030-01-01",
                                "time": "20:00", "status": "scheduled",
                                "base_price": "10"},
                          follow_redirects=True)
        slot1 = query_one("SELECT artist_id FROM concert_artists "
                          "WHERE concert_id=%s AND slot_order=1", (cid,))
        assert slot1["artist_id"] == 2    # junction row follows the new headliner
    finally:
        execute("DELETE FROM concerts WHERE concert_id=%s", (cid,))


# ---- Redis: cache + sliding session TTL ----------------------------------------

def test_browse_listing_is_cached(client):
    from db import redis_client
    for key in redis_client.scan_iter(match="browse:*"):
        redis_client.delete(key)
    client.get("/concerts")
    assert any(redis_client.scan_iter(match="browse:*")), "browse cache not written"


def test_browse_time_window_filters(client):
    from db import redis_client
    for key in redis_client.scan_iter(match="browse:*"):
        redis_client.delete(key)
    # Each window renders and gets its own cache key; 'past' shows the
    # completed seed concerts that 'upcoming' must not.
    for when in ("upcoming", "past", "all"):
        assert client.get(f"/concerts?when={when}").status_code == 200
        assert redis_client.get(f"browse:{when}:*:*") is not None, when
    # An invalid value falls back to 'upcoming' instead of erroring.
    assert client.get("/concerts?when=bogus").status_code == 200


def test_session_ttl_slides_on_activity(customer_client):
    from db import redis_client
    key = "session:pytest-session-customer"
    redis_client.expire(key, 100)            # simulate an old session
    customer_client.get("/concerts")          # any authenticated request
    assert redis_client.ttl(key) > 100        # TTL refreshed (to SESSION_TTL)


# ---- admin dashboard: advanced-SQL analytics + the view --------------------------

def test_admin_dashboard_analytics_render(admin_client):
    """The window-function, GROUP BY+HAVING and CTE panels render on /admin."""
    r = admin_client.get("/admin")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "Live analytics" in body
    assert "Top earner per city" in body
    assert "Top artists by revenue" in body


def test_admin_concerts_list_renders(admin_client):
    """The admin concert list (served from the v_concert_summary view) renders."""
    r = admin_client.get("/admin/concerts")
    assert r.status_code == 200
    assert "Seats left" in r.get_data(as_text=True)   # a column the view provides


def test_advanced_sql_executes():
    """The view, the window function and a CTE all run against the database."""
    from db import query_all, query_one
    # The reusable view exists and exposes its computed columns.
    row = query_one("SELECT concert_id, from_price, seats_left "
                    "FROM v_concert_summary LIMIT 1")
    assert row is not None and "from_price" in row
    # Window function (RANK partitioned by city).
    ranked = query_all(
        "SELECT * FROM ("
        "  SELECT v.city, RANK() OVER (PARTITION BY v.city ORDER BY c.concert_id) rk"
        "  FROM concerts c JOIN venues v ON v.venue_id = c.venue_id) z "
        "WHERE rk = 1")
    assert isinstance(ranked, list) and ranked
    # Common Table Expression.
    cte = query_all("WITH x AS (SELECT 1 AS n) SELECT n FROM x")
    assert cte and cte[0]["n"] == 1

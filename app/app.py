"""
GigTrack — Flask web application entry point.

Run:
    export FLASK_APP=app/app.py
    flask run --port 5000
"""
import json
import logging
import os
import secrets
from datetime import datetime, timezone
from functools import wraps
from uuid import uuid4

import bcrypt
from bson import ObjectId
from flask import (Flask, abort, flash, g, redirect, render_template,
                   request, session, url_for)
from werkzeug.utils import secure_filename

import storage
from db import execute, get_mysql, mongo, query_all, query_one, redis_client

app = Flask(__name__, template_folder="templates", static_folder="static")
app.secret_key = os.environ.get("FLASK_SECRET", "dev-secret-change-me")

log = logging.getLogger("gigtrack")

CACHE_TTL_SECONDS = 60
TRENDING_TTL      = 5 * 60


# ============================================================
# View-count flush (Redis -> MySQL)
# ============================================================
# Each concert detail view does a cheap Redis INCR on a *pending delta* key.
# Before we render any list ordered by view_count, we drain those deltas into
# MySQL so the durable counter (and therefore the trending order) is correct.
# GETDEL is atomic, so a concurrent viewer's increment is never lost — it just
# lands in the next flush. This is the "flush periodically" pattern described
# in docs/schema_design.md, triggered opportunistically instead of via cron.

def flush_view_counts():
    updates = []
    for key in redis_client.scan_iter(match="concert:*:views"):
        delta = redis_client.getdel(key)
        if delta and int(delta) != 0:
            concert_id = int(key.split(":")[1])
            updates.append((int(delta), concert_id))
    if not updates:
        return
    with get_mysql() as conn, conn.cursor() as cur:
        cur.executemany(
            "UPDATE concerts SET view_count = view_count + %s WHERE concert_id = %s",
            updates,
        )


# ============================================================
# Auth
# ============================================================

def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not g.user:
            flash("Please log in first.")
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


@app.before_request
def load_user():
    g.user = None
    token = session.get("token")
    if token:
        user_id = redis_client.get(f"session:{token}")
        if user_id:
            g.user = query_one(
                "SELECT user_id, username, email, home_city FROM users WHERE user_id=%s",
                (int(user_id),),
            )


@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        username = request.form["username"].strip()
        email    = request.form["email"].strip().lower()
        password = request.form["password"]
        city     = request.form.get("home_city", "").strip() or None
        pw_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
        if not username or not email or not password:
            flash("Username, email and password are all required.")
            return render_template("signup.html")
        try:
            execute(
                "INSERT INTO users (username, email, password_hash, home_city) "
                "VALUES (%s, %s, %s, %s)",
                (username, email, pw_hash, city),
            )
        except Exception:
            # Most likely a duplicate username/email (UNIQUE constraint).
            # Log the detail server-side; show the user a safe message.
            log.exception("Sign-up failed for email=%s", email)
            flash("Sign-up failed — that username or email may already be in use.")
            return render_template("signup.html")
        flash("Account created — please log in.")
        return redirect(url_for("login"))
    return render_template("signup.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form["email"].strip().lower()
        password = request.form["password"].encode()
        row = query_one(
            "SELECT user_id, password_hash FROM users WHERE email=%s", (email,)
        )
        if row and bcrypt.checkpw(password, row["password_hash"].encode()):
            token = secrets.token_urlsafe(24)
            redis_client.setex(f"session:{token}", 1800, row["user_id"])
            session["token"] = token
            return redirect(request.args.get("next") or url_for("home"))
        flash("Invalid credentials")
    return render_template("login.html")


@app.route("/logout")
def logout():
    token = session.pop("token", None)
    if token:
        redis_client.delete(f"session:{token}")
    return redirect(url_for("home"))


# ============================================================
# Browse
# ============================================================

@app.route("/")
def home():
    city = request.args.get("city")

    # Drain pending view deltas into MySQL so view_count (and the trending
    # order below) reflects reality before we read it.
    flush_view_counts()

    # Both branches are cacheable. The cache key encodes whether a city filter
    # is applied so the two result sets never collide.
    cache_key = f"trending:city:{city}" if city else "trending:all"
    cached = redis_client.get(cache_key)
    if cached:
        concerts = json.loads(cached)
    else:
        if city:
            concerts = query_all(
                """
                SELECT c.concert_id, c.title, c.concert_date, c.view_count,
                       v.name AS venue, v.city,
                       a.name AS headliner, a.genre
                FROM   concerts c
                JOIN   venues  v ON v.venue_id  = c.venue_id
                JOIN   artists a ON a.artist_id = c.headline_artist_id
                WHERE  v.city = %s AND c.status = 'scheduled'
                ORDER  BY c.view_count DESC, c.concert_date ASC
                LIMIT  20
                """,
                (city,),
            )
        else:
            concerts = query_all(
                """
                SELECT c.concert_id, c.title, c.concert_date, c.view_count,
                       v.name AS venue, v.city,
                       a.name AS headliner, a.genre
                FROM   concerts c
                JOIN   venues  v ON v.venue_id  = c.venue_id
                JOIN   artists a ON a.artist_id = c.headline_artist_id
                WHERE  c.status = 'scheduled'
                ORDER  BY c.concert_date ASC
                LIMIT  20
                """
            )
        redis_client.setex(cache_key, TRENDING_TTL,
                           json.dumps(concerts, default=str))

    return render_template("home.html", concerts=concerts, city=city)


@app.route("/concerts/<int:concert_id>")
def concert_detail(concert_id):
    # Track view: increment Redis counter, batch-flush to MySQL elsewhere.
    redis_client.incr(f"concert:{concert_id}:views")

    concert = query_one(
        """
        SELECT c.*, v.name AS venue, v.city, v.country,
               a.name AS headliner, a.genre
        FROM   concerts c
        JOIN   venues  v ON v.venue_id  = c.venue_id
        JOIN   artists a ON a.artist_id = c.headline_artist_id
        WHERE  c.concert_id = %s
        """,
        (concert_id,),
    )
    if not concert:
        abort(404)

    lineup = query_all(
        """
        SELECT ca.slot_order, ca.role, a.name, a.genre
        FROM   concert_artists ca
        JOIN   artists a ON a.artist_id = ca.artist_id
        WHERE  ca.concert_id = %s
        ORDER  BY ca.slot_order
        """,
        (concert_id,),
    )
    tickets = query_all(
        "SELECT ticket_id, tier, price, available_seats, total_seats "
        "FROM   tickets WHERE concert_id = %s ORDER BY price",
        (concert_id,),
    )

    # Pull setlist + reviews from Mongo
    setlist = mongo.setlists.find_one({"concert_id": concert_id})
    reviews = list(
        mongo.reviews.find({"concert_id": concert_id})
                     .sort("posted_at", -1).limit(20)
    )

    # Reviews only store the integer user_id (a logical FK into MySQL).
    # Resolve those to usernames in one batched query for display.
    user_ids = {r["user_id"] for r in reviews if r.get("user_id") is not None}
    if user_ids:
        placeholders = ",".join(["%s"] * len(user_ids))
        rows = query_all(
            f"SELECT user_id, username FROM users WHERE user_id IN ({placeholders})",
            tuple(user_ids),
        )
        names = {row["user_id"]: row["username"] for row in rows}
        for r in reviews:
            r["author"] = names.get(r.get("user_id"), "Unknown")

    return render_template(
        "concert_detail.html",
        concert=concert, lineup=lineup, tickets=tickets,
        setlist=setlist, reviews=reviews,
    )


@app.route("/artists/<int:artist_id>")
def artist_detail(artist_id):
    artist = query_one(
        "SELECT * FROM artists WHERE artist_id = %s", (artist_id,)
    )
    if not artist:
        abort(404)
    upcoming = query_all(
        """
        SELECT c.concert_id, c.title, c.concert_date, v.city
        FROM   concerts c
        JOIN   venues v ON v.venue_id = c.venue_id
        WHERE  c.headline_artist_id = %s AND c.concert_date > NOW()
        ORDER  BY c.concert_date
        """,
        (artist_id,),
    )
    bio = mongo.artist_bios.find_one({"artist_id": artist_id})

    is_following = False
    if g.user:
        is_following = bool(query_one(
            "SELECT 1 FROM follows WHERE user_id=%s AND artist_id=%s",
            (g.user["user_id"], artist_id),
        ))
    return render_template("artist_detail.html",
                           artist=artist, upcoming=upcoming,
                           bio=bio, is_following=is_following)


# ============================================================
# Actions
# ============================================================

@app.route("/concerts/<int:concert_id>/book", methods=["POST"])
@login_required
def book_ticket(concert_id):
    try:
        ticket_id = int(request.form["ticket_id"])
        quantity  = int(request.form["quantity"])
    except (KeyError, ValueError):
        abort(400, "Invalid ticket or quantity")

    # Guard against zero / negative quantities: a non-positive value would
    # create a junk booking and, for negatives, try to *inflate* inventory.
    if quantity < 1 or quantity > 6:
        flash("Please choose a quantity between 1 and 6.")
        return redirect(url_for("concert_detail", concert_id=concert_id))

    ticket = query_one(
        "SELECT price FROM tickets WHERE ticket_id = %s AND concert_id = %s",
        (ticket_id, concert_id),
    )
    if not ticket:
        abort(400, "Bad ticket selection")

    total = float(ticket["price"]) * quantity
    try:
        execute(
            "INSERT INTO bookings (user_id, ticket_id, quantity, total_price) "
            "VALUES (%s, %s, %s, %s)",
            (g.user["user_id"], ticket_id, quantity, total),
        )
        flash(f"Booked {quantity} x ${ticket['price']}. Total ${total:.2f}.")
    except Exception:
        # The BEFORE INSERT trigger raises (SQLSTATE 45000) when seats are
        # insufficient. Log the real cause; tell the user something safe.
        log.exception("Booking failed for user=%s ticket=%s",
                      g.user["user_id"], ticket_id)
        flash("Booking failed — there may not be enough seats left in that tier.")
    return redirect(url_for("concert_detail", concert_id=concert_id))


@app.route("/artists/<int:artist_id>/follow", methods=["POST"])
@login_required
def toggle_follow(artist_id):
    existing = query_one(
        "SELECT 1 FROM follows WHERE user_id=%s AND artist_id=%s",
        (g.user["user_id"], artist_id),
    )
    if existing:
        execute(
            "DELETE FROM follows WHERE user_id=%s AND artist_id=%s",
            (g.user["user_id"], artist_id),
        )
        flash("Unfollowed.")
    else:
        execute(
            "INSERT INTO follows (user_id, artist_id) VALUES (%s, %s)",
            (g.user["user_id"], artist_id),
        )
        flash("Following.")
    return redirect(url_for("artist_detail", artist_id=artist_id))


@app.route("/concerts/<int:concert_id>/review", methods=["POST"])
@login_required
def post_review(concert_id):
    try:
        rating = int(request.form["rating"])
    except (KeyError, ValueError):
        abort(400, "Invalid rating")
    if not 1 <= rating <= 5:
        flash("Rating must be between 1 and 5.")
        return redirect(url_for("concert_detail", concert_id=concert_id))

    # Upload any attached photos to object storage. The BLOB goes to MinIO/S3;
    # the review document keeps only a pointer ({url, key, caption}). If storage
    # is disabled or a single upload fails, we still post the review text.
    photos = []
    if storage.is_enabled():
        for f in request.files.getlist("photos"):
            if not f or not f.filename:
                continue
            safe = secure_filename(f.filename) or "photo"
            key = f"reviews/{concert_id}/{uuid4().hex}-{safe}"
            try:
                url = storage.upload_fileobj(f.stream, key, f.mimetype)
                photos.append({"url": url, "key": key, "caption": f.filename})
            except Exception:
                log.exception("Photo upload failed for concert=%s key=%s",
                              concert_id, key)

    review = {
        "concert_id":   concert_id,
        "user_id":      g.user["user_id"],
        "rating":       rating,
        "title":        request.form["title"].strip(),
        "body":         request.form["body"].strip(),
        "tags":         [t.strip() for t in request.form.get("tags", "").split(",") if t.strip()],
        "photos":       photos,
        "helpful_count": 0,
        "posted_at":    datetime.now(timezone.utc),
    }
    mongo.reviews.insert_one(review)
    flash(f"Review posted{' with ' + str(len(photos)) + ' photo(s)' if photos else ''}.")
    return redirect(url_for("concert_detail", concert_id=concert_id))


@app.route("/reviews/<review_id>/upvote", methods=["POST"])
@login_required
def upvote_review(review_id):
    mongo.reviews.update_one({"_id": ObjectId(review_id)},
                             {"$inc": {"helpful_count": 1}})
    return redirect(request.referrer or url_for("home"))


@app.route("/my/bookings")
@login_required
def my_bookings():
    rows = query_all(
        """
        SELECT b.booking_id, b.quantity, b.total_price, b.status, b.booked_at,
               t.tier, t.price,
               c.concert_id, c.title, c.concert_date,
               v.name AS venue, v.city
        FROM   bookings b
        JOIN   tickets  t ON t.ticket_id  = b.ticket_id
        JOIN   concerts c ON c.concert_id = t.concert_id
        JOIN   venues   v ON v.venue_id   = c.venue_id
        WHERE  b.user_id = %s
        ORDER  BY b.booked_at DESC
        """,
        (g.user["user_id"],),
    )
    return render_template("my_bookings.html", bookings=rows)


@app.route("/my/bookings/<int:booking_id>/cancel", methods=["POST"])
@login_required
def cancel_booking(booking_id):
    # Only allow cancelling your own, currently-confirmed booking. The
    # ownership + status check is done in the WHERE clause so a forged
    # booking_id simply matches zero rows. The AFTER UPDATE trigger
    # (trg_booking_restore_seats_on_cancel) puts the seats back.
    booking = query_one(
        "SELECT booking_id FROM bookings "
        "WHERE booking_id = %s AND user_id = %s AND status = 'confirmed'",
        (booking_id, g.user["user_id"]),
    )
    if not booking:
        flash("That booking can't be cancelled.")
        return redirect(url_for("my_bookings"))
    execute(
        "UPDATE bookings SET status = 'cancelled' WHERE booking_id = %s",
        (booking_id,),
    )
    flash("Booking cancelled — seats released.")
    return redirect(url_for("my_bookings"))


# ============================================================
# Healthcheck — useful for the demo and for verifying the stack
# ============================================================
@app.route("/healthz")
def healthz():
    try:
        query_one("SELECT 1 AS ok")
        mysql_ok = True
    except Exception:
        mysql_ok = False
    try:
        mongo.command("ping")
        mongo_ok = True
    except Exception:
        mongo_ok = False
    try:
        redis_client.ping()
        redis_ok = True
    except Exception:
        redis_ok = False
    return {"mysql": mysql_ok, "mongo": mongo_ok, "redis": redis_ok}


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)

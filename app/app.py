"""
GigTrack — Flask web application entry point.

Run:
    export FLASK_APP=app/app.py
    flask run --port 5000
"""
import io
import json
import logging
import os
import secrets
from datetime import datetime, timezone
from functools import wraps
from uuid import uuid4

import bcrypt
from bson import ObjectId
from bson.errors import InvalidId
from flask import (Flask, abort, flash, g, redirect, render_template,
                   request, session, url_for)
from werkzeug.utils import secure_filename

import storage
from db import execute, get_mysql, mongo, query_all, query_one, redis_client

app = Flask(__name__, template_folder="templates", static_folder="static")
app.secret_key = os.environ.get("FLASK_SECRET", "dev-secret-change-me")

# ---- security-related cookie/session config ----------------------------
# HttpOnly stops JS from reading the session cookie; SameSite=Lax blunts CSRF
# on top-level navigations; Secure (HTTPS-only) is on unless we're in dev.
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("FLASK_ENV") == "production",
    # Generous request-body cap: a review may carry several photos (each
    # individually capped at MAX_IMAGE_BYTES = 5 MB and downscaled), so the
    # total must comfortably exceed one image or multi-photo posts 413.
    MAX_CONTENT_LENGTH=24 * 1024 * 1024,  # reject request bodies > 24 MB
)

log = logging.getLogger("gigtrack")

TRENDING_TTL = 5 * 60   # seconds; TTL for cached browse/listing payloads

# Max tickets one customer may hold per concert (across all tiers).
MAX_TICKETS_PER_CONCERT = 6

# Session lifetime (seconds). Refreshed on every authenticated request
# (sliding expiry), so this is an idle timeout, not an absolute one.
SESSION_TTL = 1800


def booked_qty_for_concert(user_id, concert_id):
    """Sum of a user's CONFIRMED ticket quantities for one concert (all tiers)."""
    row = query_one(
        """
        SELECT COALESCE(SUM(b.quantity), 0) AS qty
        FROM   bookings b
        JOIN   tickets  t ON t.ticket_id = b.ticket_id
        WHERE  b.user_id = %s AND t.concert_id = %s AND b.status = 'confirmed'
        """,
        (user_id, concert_id),
    )
    return int(row["qty"]) if row else 0

# Review-photo upload limits.
MAX_IMAGE_BYTES = 5 * 1024 * 1024     # 5 MB per image
MAX_IMAGE_DIM   = 1600                 # px; larger images are downscaled
ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}


# ============================================================
# CSRF protection
# ============================================================
# Lightweight synchroniser-token CSRF guard (no extra dependency). A random
# token is stored in the session and echoed in a hidden field by every form;
# unsafe methods must present a matching token or the request is rejected.

def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(32)
    return session["csrf"]


@app.before_request
def csrf_protect():
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        sent = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token")
        if not sent or not secrets.compare_digest(sent, session.get("csrf", "")):
            abort(400, "Invalid or missing CSRF token")


@app.context_processor
def inject_csrf():
    # Makes csrf_token() callable from any template.
    return {"csrf_token": csrf_token}


def get_cities():
    """Distinct venue cities for the filter dropdown, cached in Redis (1h)."""
    try:
        cached = redis_client.get("cities:list")
        if cached:
            return json.loads(cached)
        rows = query_all("SELECT DISTINCT city FROM venues ORDER BY city")
        cities = [r["city"] for r in rows]
        redis_client.setex("cities:list", 3600, json.dumps(cities))
        return cities
    except Exception:
        return []


@app.context_processor
def inject_cities():
    # The nav city dropdown appears on every page.
    return {"cities": get_cities()}


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


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not g.user:
            flash("Please log in first.")
            return redirect(url_for("login", next=request.path))
        if not g.user.get("is_admin"):
            abort(403)
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
                "SELECT user_id, username, email, home_city, is_admin, is_active "
                "FROM users WHERE user_id=%s",
                (int(user_id),),
            )
            # A user disabled mid-session is logged out immediately.
            if g.user and not g.user.get("is_active", 1):
                redis_client.delete(f"session:{token}")
                session.pop("token", None)
                g.user = None
            elif g.user:
                # Sliding expiry: each authenticated request refreshes the
                # session TTL, so active users aren't logged out mid-task.
                redis_client.expire(f"session:{token}", SESSION_TTL)


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
            "SELECT user_id, password_hash, is_active FROM users WHERE email=%s",
            (email,),
        )
        if row and not row.get("is_active", 1):
            flash("This account has been disabled.")
            return render_template("login.html")
        if row and bcrypt.checkpw(password, row["password_hash"].encode()):
            token = secrets.token_urlsafe(24)
            redis_client.setex(f"session:{token}", SESSION_TTL, row["user_id"])
            session["token"] = token
            # Rotate the CSRF token on privilege change (login).
            session.pop("csrf", None)
            return redirect(_safe_next(request.args.get("next")) or url_for("home"))
        flash("Invalid credentials")
    return render_template("login.html")


def _safe_next(target):
    """Only honour relative same-site redirect targets (open-redirect guard).

    Rejects protocol-relative forms: '//evil.com' and '/\\evil.com' (browsers
    normalise backslashes to forward slashes, so '/\\' becomes '//')."""
    if (target and target.startswith("/")
            and not target.startswith("//") and "\\" not in target):
        return target
    return None


@app.route("/logout")
def logout():
    token = session.pop("token", None)
    if token:
        redis_client.delete(f"session:{token}")
    session.pop("csrf", None)
    return redirect(url_for("home"))


# ============================================================
# Browse
# ============================================================

def _decorate_concerts(rows):
    """Add display-ready date fields (date_day / date_mon / date_full) to each
    concert row so templates stay simple. Handles both a real datetime (fresh
    from MySQL) and a string (when the row came back from the Redis JSON cache).
    """
    for r in rows:
        d = r.get("concert_date")
        if not isinstance(d, datetime):
            try:
                d = datetime.strptime(str(d), "%Y-%m-%d %H:%M:%S")
            except (ValueError, TypeError):
                d = None
        if d:
            r["date_day"] = d.strftime("%d")
            r["date_mon"] = d.strftime("%b").upper()
            r["date_full"] = d.strftime("%a %d %b %Y · %H:%M")
        else:
            r["date_day"] = r["date_mon"] = ""
            r["date_full"] = str(r.get("concert_date") or "")
    return rows


@app.route("/")
def home():
    """Landing page — full-width hero, featured grid, category tiles, rail."""
    flush_view_counts()

    featured = _decorate_concerts(query_all(
        """
        SELECT c.concert_id, c.title, c.concert_date, c.view_count,
               v.name AS venue, v.city,
               a.name AS headliner, a.genre, a.image_url AS headliner_image
        FROM   concerts c
        JOIN   venues  v ON v.venue_id  = c.venue_id
        JOIN   artists a ON a.artist_id = c.headline_artist_id
        WHERE  c.status = 'scheduled' AND c.concert_date > NOW()
        ORDER  BY c.view_count DESC, c.concert_date ASC
        LIMIT  14
        """
    ))
    hero = featured[0] if featured else None      # biggest banner
    spotlight = featured[1:5]                       # smaller featured cards
    rail = featured[5:]                             # horizontal "trending" row
    return render_template("landing.html", hero=hero, spotlight=spotlight,
                           rail=rail, genres=get_genres())


def get_genres():
    """Distinct artist genres for the category tiles, cached in Redis (1h)."""
    try:
        cached = redis_client.get("genres:list")
        if cached:
            return json.loads(cached)
        rows = query_all(
            "SELECT genre, COUNT(*) AS n FROM artists "
            "WHERE genre IS NOT NULL AND genre <> '' GROUP BY genre ORDER BY n DESC"
        )
        genres = [r["genre"] for r in rows]
        redis_client.setex("genres:list", 3600, json.dumps(genres))
        return genres
    except Exception:
        return []


@app.route("/concerts")
def browse():
    """Full concert listing, filtered by city/genre and a time window
    (upcoming / past / all). Always ordered sequentially by date. Cached."""
    city = request.args.get("city") or None
    genre = request.args.get("genre") or None
    when = request.args.get("when") or "upcoming"
    if when not in ("upcoming", "past", "all"):
        when = "upcoming"
    flush_view_counts()

    cache_key = f"browse:{when}:{city or '*'}:{genre or '*'}"
    cached = redis_client.get(cache_key)
    if cached:
        concerts = json.loads(cached)
    else:
        # Cancelled shows are never listed; past shows are typically
        # 'completed', so we filter by date rather than status.
        where = ["c.status <> 'cancelled'"]
        params = []
        if when == "upcoming":
            where.append("c.concert_date > NOW()")
        elif when == "past":
            where.append("c.concert_date <= NOW()")
        if city:
            where.append("v.city = %s"); params.append(city)
        if genre:
            where.append("a.genre = %s"); params.append(genre)
        concerts = query_all(
            """
            SELECT c.concert_id, c.title, c.concert_date, c.view_count,
                   v.name AS venue, v.city,
                   a.name AS headliner, a.genre, a.image_url AS headliner_image
            FROM   concerts c
            JOIN   venues  v ON v.venue_id  = c.venue_id
            JOIN   artists a ON a.artist_id = c.headline_artist_id
            WHERE  """ + " AND ".join(where) + """
            ORDER  BY c.concert_date ASC
            LIMIT  60
            """,
            tuple(params),
        )
        _decorate_concerts(concerts)   # add date fields before caching
        redis_client.setex(cache_key, TRENDING_TTL,
                           json.dumps(concerts, default=str))

    return render_template("home.html", concerts=concerts, city=city,
                           genre=genre, when=when, genres=get_genres())


@app.route("/concerts/<int:concert_id>")
def concert_detail(concert_id):
    concert = query_one(
        """
        SELECT c.*, v.name AS venue, v.city, v.country,
               a.artist_id AS headliner_id, a.name AS headliner,
               a.genre, a.image_url AS headliner_image
        FROM   concerts c
        JOIN   venues  v ON v.venue_id  = c.venue_id
        JOIN   artists a ON a.artist_id = c.headline_artist_id
        WHERE  c.concert_id = %s
        """,
        (concert_id,),
    )
    if not concert:
        abort(404)

    # Track view AFTER the existence check, so hits on nonexistent concert
    # IDs can't create junk Redis counter keys. Batch-flushed to MySQL.
    redis_client.incr(f"concert:{concert_id}:views")

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
    else:
        names = {}

    me = g.user["user_id"] if g.user else None
    for r in reviews:
        r["author"] = names.get(r.get("user_id"), "Unknown")
        liked_by = r.get("liked_by") or []
        # helpful_count is derived from the like set (no double counting).
        r["helpful_count"] = len(liked_by)
        r["liked_by_me"] = me in liked_by

    # How many more tickets this customer may book for this concert.
    remaining_quota = MAX_TICKETS_PER_CONCERT
    if g.user and not g.user.get("is_admin"):
        remaining_quota = MAX_TICKETS_PER_CONCERT - booked_qty_for_concert(me, concert_id)
        remaining_quota = max(0, remaining_quota)

    return render_template(
        "concert_detail.html",
        concert=concert, lineup=lineup, tickets=tickets,
        setlist=setlist, reviews=reviews,
        max_per_concert=MAX_TICKETS_PER_CONCERT, remaining_quota=remaining_quota,
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
    # Only customers buy tickets; admins manage them via the dashboard.
    if g.user.get("is_admin"):
        flash("Admin accounts can't buy tickets — use the dashboard to manage bookings.")
        return redirect(url_for("concert_detail", concert_id=concert_id))
    try:
        ticket_id = int(request.form["ticket_id"])
        quantity  = int(request.form["quantity"])
    except (KeyError, ValueError):
        abort(400, "Invalid ticket or quantity")

    # Guard against zero / negative quantities: a non-positive value would
    # create a junk booking and, for negatives, try to *inflate* inventory.
    if quantity < 1 or quantity > MAX_TICKETS_PER_CONCERT:
        flash(f"Please choose a quantity between 1 and {MAX_TICKETS_PER_CONCERT}.")
        return redirect(url_for("concert_detail", concert_id=concert_id))

    # Quota + insert happen in ONE transaction. Locking this concert's ticket
    # rows (FOR UPDATE) serialises concurrent bookings for the same concert,
    # so two simultaneous requests can't both pass the quota check and
    # overshoot the per-concert limit (the old check-then-insert race).
    try:
        with get_mysql() as conn, conn.cursor() as cur:
            cur.execute("SELECT ticket_id, price FROM tickets "
                        "WHERE concert_id = %s FOR UPDATE", (concert_id,))
            tiers = {row["ticket_id"]: row["price"] for row in cur.fetchall()}
            if ticket_id not in tiers:
                flash("Bad ticket selection.")
                return redirect(url_for("concert_detail", concert_id=concert_id))

            cur.execute(
                """
                SELECT COALESCE(SUM(b.quantity), 0) AS qty
                FROM   bookings b
                JOIN   tickets  t ON t.ticket_id = b.ticket_id
                WHERE  b.user_id = %s AND t.concert_id = %s
                  AND  b.status = 'confirmed'
                """,
                (g.user["user_id"], concert_id),
            )
            already = int(cur.fetchone()["qty"])
            if already + quantity > MAX_TICKETS_PER_CONCERT:
                remaining = max(0, MAX_TICKETS_PER_CONCERT - already)
                flash(f"Ticket limit reached: max {MAX_TICKETS_PER_CONCERT} per concert. "
                      f"You already have {already}; you can book {remaining} more.")
                return redirect(url_for("concert_detail", concert_id=concert_id))

            total = float(tiers[ticket_id]) * quantity
            cur.execute(
                "INSERT INTO bookings (user_id, ticket_id, quantity, total_price) "
                "VALUES (%s, %s, %s, %s)",
                (g.user["user_id"], ticket_id, quantity, total),
            )
        flash(f"Booked {quantity} x ${tiers[ticket_id]}. Total ${total:.2f}.")
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
    # The concert must exist — otherwise a forged form could create orphan
    # review documents for arbitrary concert IDs.
    if not query_one("SELECT 1 FROM concerts WHERE concert_id = %s", (concert_id,)):
        abort(404)
    try:
        rating = int(request.form["rating"])
    except (KeyError, ValueError):
        abort(400, "Invalid rating")
    if not 1 <= rating <= 5:
        flash("Rating must be between 1 and 5.")
        return redirect(url_for("concert_detail", concert_id=concert_id))

    # Upload any attached photos to object storage. The BLOB goes to MinIO/S3;
    # the review document keeps only a pointer ({url, key, caption}). Each image
    # is validated and downscaled first so a huge/odd file can't break display.
    photos = []
    rejected = 0
    if storage.is_enabled():
        for f in request.files.getlist("photos"):
            if not f or not f.filename:
                continue
            processed = _process_image(f)
            if processed is None:
                rejected += 1
                continue
            buf, content_type, ext = processed
            key = f"reviews/{concert_id}/{uuid4().hex}.{ext}"
            try:
                url = storage.upload_fileobj(buf, key, content_type)
                photos.append({"url": url, "key": key,
                               "caption": secure_filename(f.filename)})
            except Exception:
                log.exception("Photo upload failed for concert=%s key=%s",
                              concert_id, key)
                rejected += 1

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
    review["liked_by"] = []
    mongo.reviews.insert_one(review)
    msg = "Review posted"
    if photos:
        msg += f" with {len(photos)} photo(s)"
    if rejected:
        msg += f" ({rejected} image(s) skipped — too large or unsupported)"
    flash(msg + ".")
    return redirect(url_for("concert_detail", concert_id=concert_id))


def _process_image(file_storage):
    """Validate + downscale an uploaded image.

    Returns (BytesIO, content_type, ext) ready to upload, or None if the file
    is not an allowed image type or is too large. Large images are resized so
    they always render cleanly in the review grid.
    """
    if file_storage.mimetype not in ALLOWED_IMAGE_TYPES:
        return None
    data = file_storage.read()
    if len(data) > MAX_IMAGE_BYTES:
        return None
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(data))
        img.verify()                      # detect truncated / non-image files
        img = Image.open(io.BytesIO(data))  # reopen (verify exhausts the file)
        img = img.convert("RGB")
        img.thumbnail((MAX_IMAGE_DIM, MAX_IMAGE_DIM))
        out = io.BytesIO()
        img.save(out, format="JPEG", quality=85)
        out.seek(0)
        return out, "image/jpeg", "jpg"
    except Exception:
        log.exception("Image processing failed for %s", file_storage.filename)
        return None


@app.route("/reviews/<review_id>/like", methods=["POST"])
@login_required
def like_review(review_id):
    """Toggle the current user's like. $addToSet/$pull are idempotent, so a
    user can never be counted twice — fixes the duplicate-like bug."""
    try:
        oid = ObjectId(review_id)
    except (InvalidId, TypeError):
        abort(404)
    me = g.user["user_id"]
    review = mongo.reviews.find_one({"_id": oid}, {"liked_by": 1})
    if not review:
        abort(404)
    if me in (review.get("liked_by") or []):
        mongo.reviews.update_one({"_id": oid}, {"$pull": {"liked_by": me}})
    else:
        mongo.reviews.update_one({"_id": oid}, {"$addToSet": {"liked_by": me}})
    return redirect(_safe_next(request.form.get("next"))
                    or request.referrer or url_for("home"))


@app.route("/reviews/<review_id>/delete", methods=["POST"])
@login_required
def delete_review(review_id):
    """Delete your own review (admins can delete any). Also removes its photos
    from object storage so we don't orphan blobs."""
    try:
        oid = ObjectId(review_id)
    except (InvalidId, TypeError):
        abort(404)
    review = mongo.reviews.find_one({"_id": oid})
    if not review:
        abort(404)
    if review.get("user_id") != g.user["user_id"] and not g.user.get("is_admin"):
        abort(403)
    for p in review.get("photos", []):
        if p.get("key"):
            try:
                storage.delete(p["key"])
            except Exception:
                log.exception("Failed to delete blob %s", p.get("key"))
    mongo.reviews.delete_one({"_id": oid})
    flash("Review deleted.")
    return redirect(_safe_next(request.form.get("next"))
                    or request.referrer or url_for("home"))


@app.route("/my/bookings")
@login_required
def my_bookings():
    # Admins don't have personal bookings — send them to the management view.
    if g.user.get("is_admin"):
        return redirect(url_for("admin_bookings"))
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


@app.route("/my/profile", methods=["GET", "POST"])
@login_required
def profile():
    """Self-service profile update — demonstrates user UPDATE at the app level."""
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        city     = request.form.get("home_city", "").strip() or None
        if not username:
            flash("Username can't be empty.")
            return redirect(url_for("profile"))
        try:
            execute(
                "UPDATE users SET username = %s, home_city = %s WHERE user_id = %s",
                (username, city, g.user["user_id"]),
            )
            flash("Profile updated.")
        except Exception:
            log.exception("Profile update failed for user=%s", g.user["user_id"])
            flash("Update failed — that username may already be taken.")
        return redirect(url_for("profile"))
    return render_template("profile.html")


# ============================================================
# Admin dashboard
# ============================================================

@app.route("/admin")
@admin_required
def admin_home():
    stats = query_one(
        """
        SELECT (SELECT COUNT(*) FROM users)                       AS users,
               (SELECT COUNT(*) FROM users WHERE is_active = 0)   AS disabled_users,
               (SELECT COUNT(*) FROM concerts)                    AS concerts,
               (SELECT COUNT(*) FROM bookings WHERE status='confirmed') AS bookings,
               (SELECT COALESCE(SUM(total_price),0) FROM bookings WHERE status='confirmed') AS revenue
        """
    )
    return render_template("admin/dashboard.html", stats=stats)


# ---- admin: concerts ----------------------------------------------------

@app.route("/admin/concerts")
@admin_required
def admin_concerts():
    q = (request.args.get("q") or "").strip()
    sql = """
        SELECT c.concert_id, c.title, c.concert_date, c.status, c.base_price,
               v.name AS venue, a.name AS headliner
        FROM   concerts c
        JOIN   venues  v ON v.venue_id  = c.venue_id
        JOIN   artists a ON a.artist_id = c.headline_artist_id
    """
    params = ()
    if q:
        sql += (" WHERE c.title LIKE %s OR a.name LIKE %s OR v.name LIKE %s"
                " OR v.city LIKE %s")
        like = f"%{q}%"
        params = (like, like, like, like)
    sql += " ORDER BY c.concert_date DESC"
    concerts = query_all(sql, params)
    return render_template("admin/concerts.html", concerts=concerts, q=q)


def _concert_form_options():
    venues  = query_all("SELECT venue_id, name, city FROM venues ORDER BY name")
    artists = query_all("SELECT artist_id, name FROM artists ORDER BY name")
    return venues, artists


def _form_concert_datetime():
    """Combine the separate date + time inputs into a MySQL DATETIME string.
    Falls back to a legacy single datetime-local field if present."""
    d = (request.form.get("date") or "").strip()
    t = (request.form.get("time") or "").strip()
    if d:
        return f"{d} {(t or '20:00')}:00"
    return request.form.get("concert_date", "").replace("T", " ")


def _resolve_artist(cur):
    """Return a headliner artist_id — inserting a new artist first if the admin
    ticked 'add new artist' and filled in a name."""
    if request.form.get("new_artist_toggle"):
        name = (request.form.get("new_artist_name") or "").strip()
        if not name:
            raise ValueError("New artist name is required")
        cur.execute("INSERT INTO artists (name, genre, country) VALUES (%s,%s,%s)",
                    (name,
                     (request.form.get("new_artist_genre") or "").strip() or None,
                     (request.form.get("new_artist_country") or "").strip() or None))
        return cur.lastrowid
    return int(request.form["headline_artist_id"])


def _resolve_venue(cur):
    """Return a venue_id — inserting a new venue first if the admin ticked
    'add new venue' and filled in name + city."""
    if request.form.get("new_venue_toggle"):
        name = (request.form.get("new_venue_name") or "").strip()
        city = (request.form.get("new_venue_city") or "").strip()
        if not (name and city):
            raise ValueError("New venue needs a name and city")
        cap = request.form.get("new_venue_capacity")
        cur.execute("INSERT INTO venues (name, city, country, capacity) "
                    "VALUES (%s,%s,%s,%s)",
                    (name, city,
                     (request.form.get("new_venue_country") or "").strip() or city,
                     int(cap) if cap and cap.isdigit() else None))
        return cur.lastrowid
    return int(request.form["venue_id"])


@app.route("/admin/concerts/new", methods=["GET", "POST"])
@admin_required
def admin_concert_new():
    venues, artists = _concert_form_options()
    if request.method == "POST":
        try:
            # Concert, any new artist/venue, and ticket tiers — one transaction.
            with get_mysql() as conn, conn.cursor() as cur:
                artist_id = _resolve_artist(cur)
                venue_id  = _resolve_venue(cur)
                cur.execute(
                    "INSERT INTO concerts (venue_id, headline_artist_id, title, "
                    "concert_date, status, base_price) VALUES (%s,%s,%s,%s,%s,%s)",
                    (venue_id, artist_id, request.form["title"].strip(),
                     _form_concert_datetime(),
                     request.form.get("status", "scheduled"),
                     float(request.form["base_price"])),
                )
                new_id = cur.lastrowid
                # slot-1 headliner in the lineup, consistent with the concert
                cur.execute("INSERT INTO concert_artists (concert_id, artist_id, "
                            "slot_order, role) VALUES (%s,%s,1,'headliner')",
                            (new_id, artist_id))
                for tier, price, seats in _parse_tier_rows():
                    cur.execute(
                        "INSERT INTO tickets (concert_id, tier, price, "
                        "total_seats, available_seats) VALUES (%s,%s,%s,%s,%s)",
                        (new_id, tier, price, seats, seats),
                    )
            _bust_trending_cache()
            flash("Concert created.")
            return redirect(url_for("admin_concert_edit", concert_id=new_id))
        except Exception as e:
            log.exception("Concert create failed")
            flash(_friendly_db_error(e, "Create failed — check the field values."))
    return render_template("admin/concert_form.html",
                           concert=None, venues=venues, artists=artists, tickets=[])


def _parse_tier_rows():
    """Read repeated tier[]/price[]/seats[] inputs from the concert form into
    a list of (tier, price, seats), skipping blank rows."""
    tiers  = request.form.getlist("tier[]")
    prices = request.form.getlist("price[]")
    seats  = request.form.getlist("seats[]")
    out = []
    for t, p, s in zip(tiers, prices, seats):
        t = t.strip()
        if not t:
            continue
        out.append((t, float(p), int(s)))
    return out


def _friendly_db_error(exc, default):
    """Surface the VIP-pricing trigger message nicely; otherwise a safe default."""
    msg = str(getattr(exc, "args", ["", ""])[-1] if getattr(exc, "args", None) else exc)
    if "VIP" in msg:
        return "VIP tickets cannot be priced lower than other tiers."
    return default


@app.route("/admin/concerts/<int:concert_id>/edit", methods=["GET", "POST"])
@admin_required
def admin_concert_edit(concert_id):
    venues, artists = _concert_form_options()
    if request.method == "POST":
        try:
            with get_mysql() as conn, conn.cursor() as cur:
                artist_id = _resolve_artist(cur)
                venue_id  = _resolve_venue(cur)
                cur.execute(
                    "UPDATE concerts SET venue_id=%s, headline_artist_id=%s, "
                    "title=%s, concert_date=%s, status=%s, base_price=%s "
                    "WHERE concert_id=%s",
                    (venue_id, artist_id, request.form["title"].strip(),
                     _form_concert_datetime(),
                     request.form.get("status", "scheduled"),
                     float(request.form["base_price"]), concert_id),
                )
                # Keep the lineup junction in sync: if the headliner changed,
                # the old slot-1 row would otherwise still show the previous
                # artist as headliner on the detail page.
                cur.execute(
                    "DELETE FROM concert_artists WHERE concert_id=%s "
                    "AND slot_order=1 AND artist_id<>%s",
                    (concert_id, artist_id),
                )
                cur.execute(
                    "INSERT INTO concert_artists (concert_id, artist_id, "
                    "slot_order, role) VALUES (%s,%s,1,'headliner') "
                    "ON DUPLICATE KEY UPDATE slot_order=1, role='headliner'",
                    (concert_id, artist_id),
                )
            _bust_trending_cache()
            flash("Concert updated.")
        except Exception as e:
            log.exception("Concert update failed for %s", concert_id)
            flash(_friendly_db_error(e, "Update failed — check the field values."))
        return redirect(url_for("admin_concert_edit", concert_id=concert_id))

    concert = query_one("SELECT * FROM concerts WHERE concert_id=%s", (concert_id,))
    if not concert:
        abort(404)
    tickets = query_all(
        "SELECT ticket_id, tier, price, total_seats, available_seats "
        "FROM tickets WHERE concert_id=%s ORDER BY price", (concert_id,)
    )
    return render_template("admin/concert_form.html",
                           concert=concert, venues=venues, artists=artists,
                           tickets=tickets)


@app.route("/admin/concerts/<int:concert_id>/delete", methods=["POST"])
@admin_required
def admin_concert_delete(concert_id):
    # tickets cascade via FK ON DELETE CASCADE; bookings reference tickets.
    try:
        execute("DELETE FROM concerts WHERE concert_id=%s", (concert_id,))
    except Exception:
        log.exception("Concert delete failed for %s", concert_id)
        flash("Delete failed — there may be bookings referencing it.")
        return redirect(url_for("admin_concerts"))

    # SQL delete succeeded — cascade to the OTHER stores so we don't orphan
    # data (Mongo has no FK into MySQL; this is the app-level cascade across
    # the logical-FK boundary). Photo blobs first, then the documents.
    try:
        for review in mongo.reviews.find({"concert_id": concert_id}, {"photos": 1}):
            for photo in review.get("photos", []):
                if photo.get("key"):
                    try:
                        storage.delete(photo["key"])
                    except Exception:
                        log.exception("Failed to delete blob %s", photo.get("key"))
        mongo.reviews.delete_many({"concert_id": concert_id})
        mongo.setlists.delete_many({"concert_id": concert_id})
    except Exception:
        log.exception("NoSQL cleanup failed for concert %s", concert_id)

    _bust_trending_cache()
    redis_client.delete(f"concert:{concert_id}:views")
    flash("Concert deleted (including its setlists, reviews and photos).")
    return redirect(url_for("admin_concerts"))


@app.route("/admin/concerts/<int:concert_id>/tickets/add", methods=["POST"])
@admin_required
def admin_ticket_add(concert_id):
    try:
        seats = int(request.form["total_seats"])
        execute(
            "INSERT INTO tickets (concert_id, tier, price, total_seats, available_seats) "
            "VALUES (%s, %s, %s, %s, %s)",
            (concert_id, request.form["tier"].strip(),
             float(request.form["price"]), seats, seats),
        )
        flash("Ticket tier added.")
    except Exception as e:
        log.exception("Ticket add failed for concert=%s", concert_id)
        flash(_friendly_db_error(e, "Could not add ticket tier."))
    return redirect(url_for("admin_concert_edit", concert_id=concert_id))


@app.route("/admin/tickets/<int:ticket_id>/delete", methods=["POST"])
@admin_required
def admin_ticket_delete(ticket_id):
    concert_id = request.form.get("concert_id", type=int)
    try:
        execute("DELETE FROM tickets WHERE ticket_id=%s", (ticket_id,))
        flash("Ticket tier removed.")
    except Exception:
        log.exception("Ticket delete failed for %s", ticket_id)
        flash("Could not remove tier — it may have bookings.")
    return redirect(url_for("admin_concert_edit", concert_id=concert_id)
                    if concert_id else url_for("admin_concerts"))


# ---- admin: users -------------------------------------------------------

@app.route("/admin/users")
@admin_required
def admin_users():
    q = (request.args.get("q") or "").strip()
    sql = ("SELECT user_id, username, email, home_city, is_admin, is_active, "
           "created_at FROM users")
    params = ()
    if q:
        sql += " WHERE username LIKE %s OR email LIKE %s OR home_city LIKE %s"
        like = f"%{q}%"
        params = (like, like, like)
    sql += " ORDER BY user_id"
    users = query_all(sql, params)
    return render_template("admin/users.html", users=users, q=q)


@app.route("/admin/users/<int:user_id>/edit", methods=["GET", "POST"])
@admin_required
def admin_user_edit(user_id):
    user = query_one("SELECT * FROM users WHERE user_id=%s", (user_id,))
    if not user:
        abort(404)
    if request.method == "POST":
        is_admin  = 1 if request.form.get("is_admin") else 0
        is_active = 1 if request.form.get("is_active") else 0
        # Guard: don't let an admin strip their own admin/active rights and
        # lock everyone out by accident.
        if user_id == g.user["user_id"] and (not is_admin or not is_active):
            flash("You can't remove your own admin/active status.")
            return redirect(url_for("admin_user_edit", user_id=user_id))
        try:
            execute(
                "UPDATE users SET username=%s, email=%s, home_city=%s, "
                "is_admin=%s, is_active=%s WHERE user_id=%s",
                (request.form["username"].strip(),
                 request.form["email"].strip().lower(),
                 request.form.get("home_city", "").strip() or None,
                 is_admin, is_active, user_id),
            )
            flash("User updated.")
        except Exception:
            log.exception("User update failed for %s", user_id)
            flash("Update failed — username/email may clash.")
        return redirect(url_for("admin_users"))
    return render_template("admin/user_form.html", user=user)


@app.route("/admin/users/<int:user_id>/delete", methods=["POST"])
@admin_required
def admin_user_delete(user_id):
    if user_id == g.user["user_id"]:
        flash("You can't delete your own account here.")
        return redirect(url_for("admin_users"))
    try:
        execute("DELETE FROM users WHERE user_id=%s", (user_id,))
    except Exception:
        log.exception("User delete failed for %s", user_id)
        flash("Delete failed — the user may have bookings on record.")
        return redirect(url_for("admin_users"))

    # App-level cascade across the logical-FK boundary: remove the user's
    # reviews (and their photo blobs) and pull them from all like-sets, so no
    # dangling user_id references remain in MongoDB.
    try:
        for review in mongo.reviews.find({"user_id": user_id}, {"photos": 1}):
            for photo in review.get("photos", []):
                if photo.get("key"):
                    try:
                        storage.delete(photo["key"])
                    except Exception:
                        log.exception("Failed to delete blob %s", photo.get("key"))
        mongo.reviews.delete_many({"user_id": user_id})
        mongo.reviews.update_many({"liked_by": user_id},
                                  {"$pull": {"liked_by": user_id}})
    except Exception:
        log.exception("NoSQL cleanup failed for user %s", user_id)

    flash("User deleted (including their reviews and photos).")
    return redirect(url_for("admin_users"))


# ---- admin: bookings ----------------------------------------------------

@app.route("/admin/bookings")
@admin_required
def admin_bookings():
    q = (request.args.get("q") or "").strip()
    sql = """
        SELECT b.booking_id, b.quantity, b.total_price, b.status, b.booked_at,
               u.username, u.email,
               t.tier, t.price, t.available_seats,
               c.concert_id, c.title
        FROM   bookings b
        JOIN   users    u ON u.user_id   = b.user_id
        JOIN   tickets  t ON t.ticket_id = b.ticket_id
        JOIN   concerts c ON c.concert_id = t.concert_id
    """
    params = ()
    if q:
        sql += " WHERE u.username LIKE %s OR u.email LIKE %s OR c.title LIKE %s"
        like = f"%{q}%"
        params = (like, like, like)
    sql += " ORDER BY b.booked_at DESC LIMIT 200"
    bookings = query_all(sql, params)
    return render_template("admin/bookings.html", bookings=bookings, q=q)


@app.route("/admin/bookings/<int:booking_id>/edit", methods=["POST"])
@admin_required
def admin_booking_edit(booking_id):
    """Admin edits a customer's booking quantity. Seats are reconciled in a
    single locking transaction (no trigger fires because status is unchanged):
    we take/return exactly the delta, and the tickets CHECK constraints prevent
    overselling or exceeding capacity."""
    try:
        new_qty = int(request.form["quantity"])
    except (KeyError, ValueError):
        abort(400, "Invalid quantity")
    if new_qty < 1:
        flash("Quantity must be at least 1 (cancel the booking to release seats).")
        return redirect(url_for("admin_bookings"))
    try:
        with get_mysql() as conn, conn.cursor() as cur:
            cur.execute("SELECT quantity, ticket_id, status FROM bookings "
                        "WHERE booking_id=%s FOR UPDATE", (booking_id,))
            bk = cur.fetchone()
            if not bk:
                abort(404)
            if bk["status"] != "confirmed":
                flash("Only confirmed bookings can be re-sized.")
                return redirect(url_for("admin_bookings"))
            delta = new_qty - bk["quantity"]           # >0 = needs more seats
            cur.execute("SELECT price, available_seats FROM tickets "
                        "WHERE ticket_id=%s FOR UPDATE", (bk["ticket_id"],))
            tk = cur.fetchone()
            if delta > 0 and tk["available_seats"] < delta:
                flash("Not enough seats left to increase this booking.")
                return redirect(url_for("admin_bookings"))
            cur.execute("UPDATE tickets SET available_seats = available_seats - %s "
                        "WHERE ticket_id=%s", (delta, bk["ticket_id"]))
            cur.execute("UPDATE bookings SET quantity=%s, total_price=%s "
                        "WHERE booking_id=%s",
                        (new_qty, float(tk["price"]) * new_qty, booking_id))
        flash("Booking updated.")
    except Exception:
        log.exception("Admin booking edit failed for %s", booking_id)
        flash("Update failed.")
    return redirect(url_for("admin_bookings"))


@app.route("/admin/bookings/<int:booking_id>/cancel", methods=["POST"])
@admin_required
def admin_booking_cancel(booking_id):
    # The AFTER UPDATE trigger restores seats on confirmed -> cancelled.
    bk = query_one("SELECT status FROM bookings WHERE booking_id=%s", (booking_id,))
    if not bk:
        abort(404)
    if bk["status"] != "confirmed":
        flash("That booking is already cancelled/refunded.")
        return redirect(url_for("admin_bookings"))
    execute("UPDATE bookings SET status='cancelled' WHERE booking_id=%s", (booking_id,))
    flash("Booking cancelled — seats released.")
    return redirect(url_for("admin_bookings"))


def _bust_trending_cache():
    # Concert/venue/artist edits can change listings, cities and genres.
    for pattern in ("browse:*", "trending:*", "cities:list", "genres:list"):
        for key in redis_client.scan_iter(match=pattern):
            redis_client.delete(key)


# ============================================================
# Static info pages (footer links)
# ============================================================

INFO_PAGES = {
    "about": ("About GigTrack",
              "<p>GigTrack is the easiest way to discover live music, grab tickets, "
              "and relive the night with fan-sourced setlists and reviews. We bring "
              "every show — from intimate club gigs to arena tours — into one place.</p>"
              "<p>Founded by music fans, for music fans.</p>"),
    "careers": ("Careers",
                "<p>We're a small team that loves shipping. We're not actively hiring "
                "right now, but we always want to hear from great people — "
                "<a href='/contact'>say hello</a>.</p>"),
    "help": ("Help Centre",
             "<p>Need a hand? Most answers live here:</p>"
             "<ul><li>Booking &amp; tickets: manage everything under "
             "<a href='/my/bookings'>My bookings</a>.</li>"
             "<li>Account: update your details on your "
             "<a href='/my/profile'>profile</a>.</li>"
             "<li>Refunds: cancel a confirmed booking to release the seats.</li></ul>"),
    "contact": ("Contact us",
                "<p>We'd love to hear from you.</p>"
                "<ul><li>General: <a href='mailto:hello@gigtrack.example'>hello@gigtrack.example</a></li>"
                "<li>Support: <a href='mailto:support@gigtrack.example'>support@gigtrack.example</a></li>"
                "<li>Press: <a href='mailto:press@gigtrack.example'>press@gigtrack.example</a></li></ul>"
                "<p>GigTrack HQ · 1 Marina Boulevard · Singapore</p>"),
    "terms": ("Terms of Service",
              "<p>This is a demonstration application. By using GigTrack you agree "
              "that bookings, prices, and events shown are illustrative only.</p>"),
    "privacy": ("Privacy Policy",
                "<p>We store your account details to provide the service and never "
                "sell your data. This is a demonstration application; do not enter "
                "real personal information.</p>"),
}


@app.route("/<page>")
def info_page(page):
    if page not in INFO_PAGES:
        abort(404)
    title, body = INFO_PAGES[page]
    return render_template("info.html", page_title=title, body=body)


# ============================================================
# Healthcheck — useful for verifying the stack
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
    # Debug is OFF unless FLASK_DEBUG=1 — never ship debug=True (it exposes an
    # interactive console / stack traces to anyone who can reach the app).
    debug = os.environ.get("FLASK_DEBUG") == "1"
    app.run(host="0.0.0.0", port=5000, debug=debug)

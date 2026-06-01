"""
Database connection helpers.

We deliberately keep one module per concern (MySQL, Mongo, Redis) so the
app code stays readable and each store's role is obvious to a reader.
"""
import os
from contextlib import contextmanager

# Load a local .env (if present) BEFORE we read any environment variables.
# db.py is imported before app.py touches config, and the dicts / clients
# below are built at import time, so this has to run first to take effect.
from dotenv import load_dotenv
load_dotenv()

import pymysql
from pymongo import MongoClient
import redis

# ---- config -------------------------------------------------------------

MYSQL_CONFIG = {
    "host":     os.environ.get("MYSQL_HOST", "localhost"),
    "port":     int(os.environ.get("MYSQL_PORT", 3306)),
    "user":     os.environ.get("MYSQL_USER", "gigtrack"),
    "password": os.environ.get("MYSQL_PASSWORD", "gigtrack_pw"),
    "database": os.environ.get("MYSQL_DB",   "gigtrack"),
    "charset":  "utf8mb4",
    "cursorclass": pymysql.cursors.DictCursor,
    "autocommit": False,
}

MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017")
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

# ---- singletons ---------------------------------------------------------

_mongo_client = MongoClient(MONGO_URI)
mongo = _mongo_client["gigtrack"]

redis_client = redis.from_url(REDIS_URL, decode_responses=True)


# ---- MySQL connection helper -------------------------------------------

import time
import logging

_log = logging.getLogger("gigtrack.db")

# How long to keep retrying a refused/unavailable MySQL before giving up.
# MySQL's first-time data-dir init (loading schema.sql + the larger seed.sql)
# briefly stops/restarts the server, so a single connect attempt can hit
# "connection refused". Retrying makes the app resilient to that window and to
# ordinary DB restarts, instead of 500-ing.
_CONNECT_RETRIES = int(os.environ.get("MYSQL_CONNECT_RETRIES", "15"))
_CONNECT_BACKOFF = float(os.environ.get("MYSQL_CONNECT_BACKOFF", "2"))


def _connect_with_retry():
    last = None
    for attempt in range(1, _CONNECT_RETRIES + 1):
        try:
            return pymysql.connect(**MYSQL_CONFIG)
        except pymysql.err.OperationalError as e:
            # 2003 = can't connect, 2002 = socket, 1053 = shutting down.
            code = e.args[0] if e.args else None
            if code not in (2002, 2003, 1053) or attempt == _CONNECT_RETRIES:
                raise
            last = e
            _log.warning("MySQL not ready (attempt %s/%s): %s; retrying in %.1fs",
                         attempt, _CONNECT_RETRIES, code, _CONNECT_BACKOFF)
            time.sleep(_CONNECT_BACKOFF)
    raise last


@contextmanager
def get_mysql():
    """Yield a pymysql connection; commit on clean exit, rollback on error.

    Connection is retried while MySQL is still coming up (see above)."""
    conn = _connect_with_retry()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def query_all(sql, params=None):
    with get_mysql() as conn, conn.cursor() as cur:
        cur.execute(sql, params or ())
        return cur.fetchall()


def query_one(sql, params=None):
    with get_mysql() as conn, conn.cursor() as cur:
        cur.execute(sql, params or ())
        return cur.fetchone()


def execute(sql, params=None):
    """Execute a write statement. Returns lastrowid."""
    with get_mysql() as conn, conn.cursor() as cur:
        cur.execute(sql, params or ())
        return cur.lastrowid

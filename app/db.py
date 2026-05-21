"""
Database connection helpers.

We deliberately keep one module per concern (MySQL, Mongo, Redis) so the
app code stays readable and each store's role is obvious to a reader.
"""
import os
from contextlib import contextmanager

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

@contextmanager
def get_mysql():
    """Yield a pymysql connection; commit on clean exit, rollback on error."""
    conn = pymysql.connect(**MYSQL_CONFIG)
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

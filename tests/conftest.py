"""
Pytest configuration for GigTrack.

Makes `app/` importable (so tests can `import app`, `import db`, …) without
needing PYTHONPATH set manually, and provides shared fixtures.

Two test layers:
  tests/test_unit.py        — pure-logic tests, no datastores needed.
  tests/test_integration.py — exercise MySQL/Mongo/Redis through the real
                              code paths; auto-SKIPPED when the stack is down.

Run everything (stack up, venv active):
    pytest
Run only the unit layer (no Docker needed):
    pytest tests/test_unit.py
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "app"))


def stack_available():
    """True when MySQL, Mongo and Redis are all reachable."""
    try:
        from db import query_one, mongo, redis_client
        query_one("SELECT 1 AS ok")
        mongo.command("ping")
        redis_client.ping()
        return True
    except Exception:
        return False


@pytest.fixture(scope="session")
def flask_app():
    from app import app as flask_app_
    flask_app_.config.update(TESTING=True)
    return flask_app_


@pytest.fixture()
def client(flask_app):
    return flask_app.test_client()


@pytest.fixture()
def csrf_client(client):
    """A test client with a CSRF token pre-planted in the session.
    POST with data={'csrf_token': 'test-csrf', ...}."""
    with client.session_transaction() as s:
        s["csrf"] = "test-csrf"
    return client


@pytest.fixture()
def customer_client(csrf_client):
    """csrf_client logged in as seed customer user02 (user_id 2) via a real
    Redis session token, cleaned up afterwards."""
    from db import redis_client
    token = "pytest-session-customer"
    redis_client.setex(f"session:{token}", 300, 2)
    with csrf_client.session_transaction() as s:
        s["token"] = token
    yield csrf_client
    redis_client.delete(f"session:{token}")


@pytest.fixture()
def admin_client(csrf_client):
    """csrf_client logged in as the seed admin (user_id 1)."""
    from db import redis_client
    token = "pytest-session-admin"
    redis_client.setex(f"session:{token}", 300, 1)
    with csrf_client.session_transaction() as s:
        s["token"] = token
    yield csrf_client
    redis_client.delete(f"session:{token}")

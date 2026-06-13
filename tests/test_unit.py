"""
Unit tests - pure logic, no datastores required.

Run:  pytest tests/test_unit.py
(Only needs the Python deps from requirements.txt, not Docker.)
"""
import io
from datetime import datetime

import pytest

pytest.importorskip("flask")

import app as gigtrack


# ---- _safe_next: open-redirect guard --------------------------------------

@pytest.mark.parametrize("target", [
    "/concerts", "/my/bookings", "/concerts/3?x=1",
])
def test_safe_next_allows_relative_paths(target):
    assert gigtrack._safe_next(target) == target


@pytest.mark.parametrize("target", [
    None, "", "http://evil.com", "https://evil.com",
    "//evil.com",            # protocol-relative
    "/\\evil.com",           # backslash variant browsers normalise to //
    "/foo\\..\\bar",         # any backslash is rejected
    "javascript:alert(1)",
])
def test_safe_next_blocks_unsafe_targets(target):
    assert gigtrack._safe_next(target) is None


# ---- _decorate_concerts: display-date derivation ---------------------------

def test_decorate_concerts_with_datetime():
    rows = [{"concert_date": datetime(2026, 7, 4, 20, 30)}]
    out = gigtrack._decorate_concerts(rows)
    assert out[0]["date_day"] == "04"
    assert out[0]["date_mon"] == "JUL"
    assert "2026" in out[0]["date_full"]


def test_decorate_concerts_with_cached_string():
    # Rows that came back from the Redis JSON cache carry string dates.
    rows = [{"concert_date": "2026-01-15 19:00:00"}]
    out = gigtrack._decorate_concerts(rows)
    assert out[0]["date_day"] == "15"
    assert out[0]["date_mon"] == "JAN"


def test_decorate_concerts_with_garbage_date():
    rows = [{"concert_date": "not-a-date"}, {"concert_date": None}]
    out = gigtrack._decorate_concerts(rows)
    for r in out:
        assert r["date_day"] == "" and r["date_mon"] == ""


# ---- _friendly_db_error: trigger-message surfacing --------------------------

def test_friendly_db_error_vip_message():
    exc = Exception(1644, "VIP tickets cannot be priced lower than other tiers")
    msg = gigtrack._friendly_db_error(exc, "default msg")
    assert "VIP" in msg


def test_friendly_db_error_falls_back():
    exc = Exception(1062, "Duplicate entry")
    assert gigtrack._friendly_db_error(exc, "default msg") == "default msg"


# ---- _process_image: upload validation + downscaling ------------------------

class FakeUpload:
    def __init__(self, data, mimetype, filename="x.png"):
        self._buf = io.BytesIO(data)
        self.mimetype = mimetype
        self.filename = filename

    def read(self):
        return self._buf.read()


def _png_bytes(w, h):
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (w, h), "purple").save(buf, format="PNG")
    return buf.getvalue()


def test_process_image_accepts_and_converts_to_jpeg():
    pytest.importorskip("PIL")
    out = gigtrack._process_image(FakeUpload(_png_bytes(100, 80), "image/png"))
    assert out is not None
    buf, content_type, ext = out
    assert content_type == "image/jpeg" and ext == "jpg"


def test_process_image_downscales_large_images():
    pytest.importorskip("PIL")
    from PIL import Image
    big = gigtrack.MAX_IMAGE_DIM + 400
    out = gigtrack._process_image(FakeUpload(_png_bytes(big, big), "image/png"))
    assert out is not None
    img = Image.open(out[0])
    assert max(img.size) <= gigtrack.MAX_IMAGE_DIM


def test_process_image_rejects_wrong_mimetype():
    assert gigtrack._process_image(FakeUpload(b"%PDF-1.4", "application/pdf")) is None


def test_process_image_rejects_non_image_payload():
    pytest.importorskip("PIL")
    junk = FakeUpload(b"this is not an image at all", "image/png")
    assert gigtrack._process_image(junk) is None


def test_process_image_rejects_oversize():
    data = b"\0" * (gigtrack.MAX_IMAGE_BYTES + 1)
    assert gigtrack._process_image(FakeUpload(data, "image/png")) is None


# ---- constants the templates/docs rely on -----------------------------------

def test_business_constants():
    assert gigtrack.MAX_TICKETS_PER_CONCERT == 6
    assert gigtrack.SESSION_TTL == 1800
    # Body cap must comfortably exceed one image so multi-photo posts work.
    assert gigtrack.app.config["MAX_CONTENT_LENGTH"] > gigtrack.MAX_IMAGE_BYTES

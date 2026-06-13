"""
Fetch a real portrait photo for each artist and store it in object storage
(MinIO/S3), then point artists.image_url at the uploaded blob.

This REPLACES the old AI image generation. Photos come from Pravatar
(i.pravatar.cc) - a free set of ~70 real portrait avatars intended as
placeholders. No API key, no rate limit, no per-image generation wait, so the
whole batch finishes in a couple of seconds.

Note: the artists in this app are fictional; these are generic placeholder
portraits, not photos of specific real, named musicians.

The image BYTES still live in object storage and the database keeps only the
URL pointer - so this keeps the blob-storage story intact while being fast and
reliable. Idempotent: an artist whose image_url already points at storage is
skipped (use --force to refetch).

Run:  PYTHONPATH=app python scripts/fetch_artist_images.py [--force]
docker-compose runs this on boot, after the bucket is ensured.
"""
import io
import os
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

import storage
from db import execute, query_all

FORCE = "--force" in sys.argv or os.environ.get("IMAGES_FORCE") == "1"
AVATAR_BASE = os.environ.get("AVATAR_BASE", "https://i.pravatar.cc")
AVATAR_COUNT = int(os.environ.get("AVATAR_COUNT", "70"))   # pravatar has 1..70
SIZE = int(os.environ.get("AVATAR_SIZE", "512"))
HTTP_TIMEOUT = int(os.environ.get("IMAGE_TIMEOUT", "30"))


def _fetch(img_id):
    url = f"{AVATAR_BASE}/{SIZE}?img={img_id}"
    req = urllib.request.Request(url, headers={"User-Agent": "GigTrack/1.0"})
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
        return r.read()


def _normalize(raw):
    """Validate + standardise to a square-ish JPEG in a BytesIO."""
    from PIL import Image
    img = Image.open(io.BytesIO(raw)); img.verify()
    img = Image.open(io.BytesIO(raw)).convert("RGB")
    img.thumbnail((SIZE, SIZE))
    out = io.BytesIO(); img.save(out, format="JPEG", quality=88); out.seek(0)
    return out


def _process_one(a):
    """Fetch + upload one artist's photo. Never raises so one failure can't
    abort the batch; a failed artist just keeps the gradient placeholder."""
    aid = a["artist_id"]
    img_id = ((aid - 1) % AVATAR_COUNT) + 1     # stable, distinct per artist
    try:
        buf = _normalize(_fetch(img_id))
        key = f"artists/{aid}.jpg"
        url = storage.upload_fileobj(buf, key, "image/jpeg")
        execute("UPDATE artists SET image_url=%s WHERE artist_id=%s", (url, aid))
        return "ok"
    except Exception as e:
        print(f"  [{a['name']}] photo fetch/upload failed "
              f"({type(e).__name__}: {e})")
        return "error"


def main():
    if not storage.is_enabled():
        print("Object storage disabled - skipping artist photos.")
        return
    storage.ensure_bucket()
    artists = query_all("SELECT artist_id, name, image_url FROM artists")

    todo = [a for a in artists
            if FORCE or not (a.get("image_url") or "").startswith("http")]
    skipped = len(artists) - len(todo)
    if not todo:
        print(f"All {len(artists)} artists already have photos.")
        return

    counts = {"ok": 0, "error": 0}
    with ThreadPoolExecutor(max_workers=int(os.environ.get("IMAGE_WORKERS", "8"))) as pool:
        for status in pool.map(_process_one, todo):
            counts[status] += 1

    print(f"Artist photos: {counts['ok']} stored, {counts['error']} failed, "
          f"{skipped} skipped (already had one).")


if __name__ == "__main__":
    main()

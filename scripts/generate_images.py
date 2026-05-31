"""
Generate branded placeholder posters for every artist and store them in
object storage (MinIO/S3), then point artists.image_url at the uploaded blob.

Run:  PYTHONPATH=app python scripts/generate_images.py
docker-compose runs this on boot, after the bucket is ensured.

This is exactly the media pipeline the report describes: the IMAGE BYTES live
in object storage; the database holds only the URL pointer. Idempotent — an
artist whose image_url already points at storage is skipped (use --force to
regenerate).
"""
import hashlib
import io
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

import storage
from db import execute, query_all

FORCE = "--force" in sys.argv or os.environ.get("IMAGES_FORCE") == "1"


def _color_from(name):
    """Deterministic pleasant dark color from the artist name."""
    h = hashlib.md5(name.encode()).hexdigest()
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    # keep it darkish so white text reads well
    return (r // 2 + 20, g // 2 + 20, b // 2 + 30)


def _make_poster(name, genre):
    from PIL import Image, ImageDraw, ImageFont
    W = H = 600
    base = _color_from(name)
    img = Image.new("RGB", (W, H), base)
    draw = ImageDraw.Draw(img)
    # simple diagonal gradient overlay for depth
    for y in range(H):
        shade = int(40 * (y / H))
        draw.line([(0, y), (W, y)], fill=(min(base[0] + shade, 255),
                                          min(base[1] + shade, 255),
                                          min(base[2] + shade, 255)))

    def font(size):
        for path in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                     "/Library/Fonts/Arial.ttf"):
            if os.path.exists(path):
                return ImageFont.truetype(path, size)
        return ImageFont.load_default()

    initials = "".join(w[0] for w in name.split()[:2]).upper()
    big = font(220)
    draw.text((W / 2, H / 2 - 60), initials, font=big, fill=(255, 255, 255),
              anchor="mm")
    draw.text((W / 2, H - 120), name, font=font(34), fill=(255, 255, 255),
              anchor="mm")
    if genre:
        draw.text((W / 2, H - 78), genre.upper(), font=font(22),
                  fill=(220, 220, 220), anchor="mm")

    out = io.BytesIO()
    img.save(out, format="PNG")
    out.seek(0)
    return out


def main():
    if not storage.is_enabled():
        print("Object storage disabled — skipping image generation.")
        return
    storage.ensure_bucket()
    artists = query_all("SELECT artist_id, name, genre, image_url FROM artists")
    made = skipped = 0
    for a in artists:
        if a.get("image_url") and a["image_url"].startswith("http") and not FORCE:
            skipped += 1
            continue
        key = f"artists/{a['artist_id']}.png"
        url = storage.upload_fileobj(_make_poster(a["name"], a.get("genre")),
                                     key, "image/png")
        execute("UPDATE artists SET image_url=%s WHERE artist_id=%s",
                (url, a["artist_id"]))
        made += 1
    print(f"Posters generated: {made}, skipped (already had one): {skipped}")


if __name__ == "__main__":
    main()

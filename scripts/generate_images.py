"""
Generate an artist image for every artist and store it in object storage
(MinIO/S3), then point artists.image_url at the uploaded blob.

Two image sources, chosen by the IMAGE_PROVIDER env var:

  * "pollinations" (default) — a FREE, key-less text-to-image API. We build a
    prompt from the artist's name + genre, so each artist gets a unique,
    on-brand picture of "what they might look like". Deterministic per artist
    (seed = artist_id), no account required.
  * "openai" — uses the OpenAI Images API if OPENAI_API_KEY is set.
  * "none"   — skip AI; draw the lightweight procedural poster only.

If an AI call fails (offline, rate-limited, etc.) we fall back to the
procedural poster so the app always ends up with an image. Either way the
BYTES live in object storage and the database keeps only the URL pointer.

Run:  PYTHONPATH=app python scripts/generate_images.py [--force]
docker-compose runs this on boot, after the bucket is ensured.
"""
import hashlib
import io
import os
import sys
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

import storage
from db import execute, query_all

FORCE = "--force" in sys.argv or os.environ.get("IMAGES_FORCE") == "1"
PROVIDER = os.environ.get("IMAGE_PROVIDER", "pollinations").lower()
HTTP_TIMEOUT = int(os.environ.get("IMAGE_TIMEOUT", "60"))


# ---- prompt -------------------------------------------------------------

def _prompt(name, genre):
    genre = genre or "alternative"
    return (f"professional promotional press photo of a {genre} music artist "
            f"named '{name}', moody cinematic studio lighting, shallow depth of "
            f"field, magazine cover quality, no text, no watermark, no logo")


# ---- AI providers -------------------------------------------------------

def _from_pollinations(name, genre, seed):
    prompt = urllib.parse.quote(_prompt(name, genre))
    url = (f"https://image.pollinations.ai/prompt/{prompt}"
           f"?width=640&height=640&seed={seed}&nologo=true&model=flux")
    req = urllib.request.Request(url, headers={"User-Agent": "GigTrack/1.0"})
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
        return resp.read()


def _from_openai(name, genre, seed):
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("OPENAI_API_KEY not set")
    import json
    body = json.dumps({
        "model": os.environ.get("OPENAI_IMAGE_MODEL", "gpt-image-1"),
        "prompt": _prompt(name, genre),
        "size": "1024x1024", "n": 1,
    }).encode()
    req = urllib.request.Request(
        "https://api.openai.com/v1/images/generations", data=body,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
        data = json.loads(resp.read())
    item = data["data"][0]
    if item.get("b64_json"):
        import base64
        return base64.b64decode(item["b64_json"])
    with urllib.request.urlopen(item["url"], timeout=HTTP_TIMEOUT) as r:
        return r.read()


def _ai_image(name, genre, seed):
    if PROVIDER == "openai":
        return _from_openai(name, genre, seed)
    if PROVIDER == "pollinations":
        return _from_pollinations(name, genre, seed)
    raise RuntimeError("AI provider disabled")


def _normalize(raw):
    """Validate + standardise to a 640px JPEG in a BytesIO."""
    from PIL import Image
    img = Image.open(io.BytesIO(raw)); img.verify()
    img = Image.open(io.BytesIO(raw)).convert("RGB")
    img.thumbnail((640, 640))
    out = io.BytesIO(); img.save(out, format="JPEG", quality=88); out.seek(0)
    return out


# ---- procedural fallback poster -----------------------------------------

def _color_from(name):
    h = hashlib.md5(name.encode()).hexdigest()
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return (r // 2 + 20, g // 2 + 20, b // 2 + 30)


def _make_poster(name, genre):
    from PIL import Image, ImageDraw, ImageFont
    W = H = 600
    base = _color_from(name)
    img = Image.new("RGB", (W, H), base)
    draw = ImageDraw.Draw(img)
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
    draw.text((W / 2, H / 2 - 60), initials, font=font(220), fill=(255, 255, 255), anchor="mm")
    draw.text((W / 2, H - 120), name, font=font(34), fill=(255, 255, 255), anchor="mm")
    if genre:
        draw.text((W / 2, H - 78), genre.upper(), font=font(22), fill=(220, 220, 220), anchor="mm")
    out = io.BytesIO(); img.save(out, format="PNG"); out.seek(0)
    return out, "image/png", "png"


# ---- main ---------------------------------------------------------------

def main():
    if not storage.is_enabled():
        print("Object storage disabled — skipping image generation.")
        return
    storage.ensure_bucket()
    artists = query_all("SELECT artist_id, name, genre, image_url FROM artists")
    made = skipped = fell_back = 0
    for a in artists:
        if a.get("image_url") and a["image_url"].startswith("http") and not FORCE:
            skipped += 1
            continue

        buf = content_type = ext = None
        if PROVIDER != "none":
            try:
                buf = _normalize(_ai_image(a["name"], a.get("genre"), a["artist_id"]))
                content_type, ext = "image/jpeg", "jpg"
            except Exception as e:
                print(f"  [{a['name']}] AI image failed ({e}); using poster")
                buf = None
        if buf is None:
            buf, content_type, ext = _make_poster(a["name"], a.get("genre"))
            fell_back += 1

        key = f"artists/{a['artist_id']}.{ext}"
        url = storage.upload_fileobj(buf, key, content_type)
        execute("UPDATE artists SET image_url=%s WHERE artist_id=%s",
                (url, a["artist_id"]))
        made += 1

    print(f"Artist images: {made} uploaded ({fell_back} procedural fallbacks), "
          f"{skipped} skipped (already had one). Provider={PROVIDER}")


if __name__ == "__main__":
    main()

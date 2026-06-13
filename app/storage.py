"""
Object storage helper (S3-compatible).

Backs binary media (review photos) with an S3-compatible object store.
Defaults target the local MinIO container from docker-compose, but the same
code works unchanged against AWS S3 / Cloudflare R2 / Backblaze B2 - only the
endpoint + credentials change.

Design: the BLOB lives here; the database keeps only a POINTER.
A review document in MongoDB stores photos as
    {"url": "<browser-reachable URL>", "key": "reviews/.../uuid.jpg", "caption": "..."}
so MySQL/Mongo never hold image bytes.

Env vars (see .env.example):
    S3_ENDPOINT     internal URL boto3 connects to        (http://minio:9000)
    S3_PUBLIC_URL   browser-reachable base for <img src>  (http://localhost:9000)
    S3_ACCESS_KEY / S3_SECRET_KEY
    S3_BUCKET       bucket name                           (gigtrack-media)
    S3_REGION       region label                          (us-east-1)
If S3_ENDPOINT / credentials are absent, storage is treated as DISABLED and the
app still runs (reviews just post without photos).
"""
import json
import logging
import os

# Load .env before reading config, so running this module standalone
# (e.g. `python app/storage.py` for a local run) picks up the same settings
# the app uses. Harmless/idempotent when env is already set (Docker).
from dotenv import load_dotenv
load_dotenv()

log = logging.getLogger("gigtrack.storage")

S3_ENDPOINT   = os.environ.get("S3_ENDPOINT")
S3_PUBLIC_URL = os.environ.get("S3_PUBLIC_URL", S3_ENDPOINT or "")
S3_ACCESS_KEY = os.environ.get("S3_ACCESS_KEY")
S3_SECRET_KEY = os.environ.get("S3_SECRET_KEY")
S3_BUCKET     = os.environ.get("S3_BUCKET", "gigtrack-media")
S3_REGION     = os.environ.get("S3_REGION", "us-east-1")

_client = None
_bucket_ready = False


def is_enabled():
    """True only if we have enough config to talk to a bucket."""
    return bool(S3_ENDPOINT and S3_ACCESS_KEY and S3_SECRET_KEY)


def _get_client():
    global _client
    if _client is None:
        import boto3  # imported lazily so the app runs without boto3 installed
        from botocore.config import Config
        _client = boto3.client(
            "s3",
            endpoint_url=S3_ENDPOINT,
            aws_access_key_id=S3_ACCESS_KEY,
            aws_secret_access_key=S3_SECRET_KEY,
            region_name=S3_REGION,
            # path-style ("endpoint/bucket/key") is what MinIO expects.
            config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
        )
    return _client


def ensure_bucket():
    """Create the bucket if missing and make it anonymously readable.

    Public-read keeps the demo simple: <img src> URLs work directly with no
    signing. For a real deployment you'd drop the policy and serve via
    presigned_url() or a CDN instead.
    """
    global _bucket_ready
    if _bucket_ready or not is_enabled():
        return
    c = _get_client()

    # On container boot MinIO may still be starting; retry briefly.
    import time
    for attempt in range(10):
        try:
            c.list_buckets()
            break
        except Exception:
            if attempt == 9:
                raise
            time.sleep(1)

    try:
        c.head_bucket(Bucket=S3_BUCKET)
    except Exception:
        c.create_bucket(Bucket=S3_BUCKET)
    policy = {
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Principal": {"AWS": ["*"]},
            "Action": ["s3:GetObject"],
            "Resource": [f"arn:aws:s3:::{S3_BUCKET}/*"],
        }],
    }
    try:
        c.put_bucket_policy(Bucket=S3_BUCKET, Policy=json.dumps(policy))
    except Exception:
        log.warning("Could not set public-read policy on %s", S3_BUCKET)
    _bucket_ready = True


def upload_fileobj(fileobj, key, content_type=None):
    """Stream a file-like object to the bucket; return its browser URL."""
    ensure_bucket()
    extra = {"ContentType": content_type} if content_type else {}
    _get_client().upload_fileobj(fileobj, S3_BUCKET, key, ExtraArgs=extra)
    return public_url(key)


def public_url(key):
    """Stable, browser-reachable URL for an object (works with public-read)."""
    base = (S3_PUBLIC_URL or "").rstrip("/")
    return f"{base}/{S3_BUCKET}/{key}"


def presigned_url(key, expires=3600):
    """Time-limited signed GET URL - the private-bucket alternative."""
    return _get_client().generate_presigned_url(
        "get_object",
        Params={"Bucket": S3_BUCKET, "Key": key},
        ExpiresIn=expires,
    )


def delete(key):
    """Remove an object (e.g. when its review is deleted)."""
    if is_enabled():
        _get_client().delete_object(Bucket=S3_BUCKET, Key=key)


if __name__ == "__main__":
    # `python app/storage.py` - used by docker-compose to pre-create the bucket.
    logging.basicConfig(level=logging.INFO)
    if is_enabled():
        ensure_bucket()
        print(f"Bucket ready: {S3_BUCKET} @ {S3_ENDPOINT}")
    else:
        print("Object storage disabled (S3_ENDPOINT / credentials not set).")

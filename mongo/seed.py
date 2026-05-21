"""
GigTrack — MongoDB seed script.

Usage:
    python mongo/seed.py

Reads MONGO_URI from environment (defaults to mongodb://localhost:27017).
Drops and recreates the `gigtrack` database collections: setlists, reviews, artist_bios.
"""
import os
from datetime import datetime, timezone
from pymongo import MongoClient, ASCENDING, DESCENDING, TEXT

MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017")

client = MongoClient(MONGO_URI)
db = client["gigtrack"]

# ---- wipe ---------------------------------------------------------------
for coll in ("setlists", "reviews", "artist_bios"):
    db[coll].drop()

# ---- setlists -----------------------------------------------------------
setlists = [
    {
        "concert_id": 6,                       # Habitat 67 in Singapore (completed)
        "submitted_by_user_id": 1,
        "source": "fan",
        "songs": [
            {"order": 1, "title": "Frozen Lake",       "duration_sec": 245, "encore": False, "cover_of": None},
            {"order": 2, "title": "Carbon Maps",       "duration_sec": 198, "encore": False, "cover_of": None, "notes": "Acoustic intro"},
            {"order": 3, "title": "Sodium Lights",     "duration_sec": 312, "encore": False, "cover_of": None},
            {"order": 4, "title": "Hallelujah",        "duration_sec": 410, "encore": False, "cover_of": "Leonard Cohen"},
            {"order": 5, "title": "Boreal",            "duration_sec": 287, "encore": False, "cover_of": None},
        ],
        "encore_songs": [
            {"order": 1, "title": "Old Pine", "duration_sec": 220, "cover_of": None},
        ],
        "total_duration_sec": 1672,
        "submitted_at": datetime(2026, 5, 2, 23, 5, tzinfo=timezone.utc),
        "upvotes": 14,
    },
    {
        "concert_id": 1,                       # Midnight Lanterns — upcoming, no setlist yet
        "submitted_by_user_id": None,
        "source": "predicted",
        "songs": [],
        "encore_songs": [],
        "total_duration_sec": 0,
        "submitted_at": None,
        "upvotes": 0,
    },
]
db.setlists.insert_many(setlists)

# ---- reviews ------------------------------------------------------------
reviews = [
    {
        "concert_id": 6, "user_id": 1, "rating": 5,
        "title": "Heart-on-sleeve gig, sound was crisp",
        "body": "Habitat 67 sounded incredible at the Esplanade. The encore took the roof off.",
        "tags": ["acoustic", "sound-quality", "intimate"],
        "photos": [
            {"url": "/static/uploads/r1_a.jpg", "caption": "Stage from balcony"},
        ],
        "helpful_count": 12,
        "posted_at": datetime(2026, 5, 3, 9, 14, tzinfo=timezone.utc),
    },
    {
        "concert_id": 6, "user_id": 2, "rating": 4,
        "title": "Great show, drinks line was brutal",
        "body": "Setlist was strong but bar queues ate the support act. Bring water.",
        "tags": ["logistics", "support-act"],
        "photos": [],
        "helpful_count": 3,
        "posted_at": datetime(2026, 5, 3, 11, 2, tzinfo=timezone.utc),
    },
]
db.reviews.insert_many(reviews)

# ---- artist_bios --------------------------------------------------------
bios = [
    {
        "artist_id": 1, "bio_text": "Manchester-formed indie rock quartet known for cinematic builds.",
        "tour_history": [
            {"year": 2024, "name": "Lantern Light EU Tour", "regions": ["EU"]},
            {"year": 2026, "name": "Asia Tour 2026",        "regions": ["ASIA"]},
        ],
        "social": {"instagram": "@midnight_lanterns", "spotify_id": "1AbCdEf"},
        "related_artists": [6, 5],
    },
    {
        "artist_id": 4, "bio_text": "Singapore-born pop vocalist. Two-time Asia Pop Award nominee.",
        "tour_history": [{"year": 2025, "name": "Glow Tour", "regions": ["ASIA"]}],
        "social": {"instagram": "@nadiahsg", "spotify_id": "9XYZ123"},
        "related_artists": [2],
    },
    {
        "artist_id": 6, "bio_text": "Montréal indie-folk duo. Heavy harmonies and pedal steel.",
        "tour_history": [{"year": 2026, "name": "Sodium Lights Tour", "regions": ["NA", "ASIA"]}],
        "social": {"instagram": "@habitat67band"},
        "related_artists": [1],
    },
]
db.artist_bios.insert_many(bios)

# ---- indexes ------------------------------------------------------------
db.setlists.create_index([("concert_id", ASCENDING)])
db.reviews.create_index([("concert_id", ASCENDING), ("posted_at", DESCENDING)])
db.reviews.create_index([("body", TEXT), ("title", TEXT)])
db.artist_bios.create_index([("artist_id", ASCENDING)], unique=True)

print("Seeded gigtrack Mongo collections:")
for coll in ("setlists", "reviews", "artist_bios"):
    print(f"  {coll}: {db[coll].count_documents({})} docs")

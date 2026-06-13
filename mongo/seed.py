"""
GigTrack - MongoDB seed script.

Usage:
    python mongo/seed.py            # idempotent: seeds only if collections are empty
    python mongo/seed.py --force    # wipe and re-seed (also: SEED_FORCE=1)

Reads MONGO_URI from environment (defaults to mongodb://localhost:27017).
Seeds the `gigtrack` collections: setlists, reviews, artist_bios.

By default this is SAFE TO RUN ON EVERY APP BOOT - it will not destroy data
a user created during the session. It only drops + reseeds when --force /
SEED_FORCE=1 is given.
"""
import os
import random
import sys
from datetime import datetime, timezone

from dotenv import load_dotenv
load_dotenv()                      # honour .env for local (non-Docker) runs

from pymongo import MongoClient, ASCENDING, DESCENDING, TEXT

MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017")
FORCE = "--force" in sys.argv or os.environ.get("SEED_FORCE") == "1"

# Mirror the counts in scripts/generate_seed.py so the logical FKs line up.
NUM_USERS    = 21
NUM_ARTISTS  = 30
NUM_CONCERTS = 50
rng = random.Random(20260530)

client = MongoClient(MONGO_URI)
db = client["gigtrack"]

COLLECTIONS = ("setlists", "reviews", "artist_bios")

# ---- idempotency guard --------------------------------------------------
already_seeded = any(db[c].estimated_document_count() > 0 for c in COLLECTIONS)
if already_seeded and not FORCE:
    print("gigtrack Mongo collections already populated - skipping seed "
          "(use --force or SEED_FORCE=1 to wipe and reseed).")
    for coll in COLLECTIONS:
        print(f"  {coll}: {db[coll].count_documents({})} docs")
    sys.exit(0)

# ---- wipe (only when seeding) -------------------------------------------
for coll in COLLECTIONS:
    db[coll].drop()

# ---- setlists -----------------------------------------------------------
# A setlist for roughly every other concert; lengths and covers vary, which is
# exactly the variable-shape data that motivates the document model.
SONG_WORDS = ["Frozen", "Carbon", "Sodium", "Boreal", "Neon", "Glass", "Velvet",
              "Echoes", "Midnight", "Harbour", "Static", "Bloom", "Drift", "Pulse"]
COVERS = [None, None, None, "Leonard Cohen", "Fleetwood Mac", "Radiohead", "Prince"]

setlists = []
for cid in range(1, NUM_CONCERTS + 1):
    if rng.random() < 0.5:
        continue
    n = rng.randint(8, 22)
    songs = [{"order": i + 1, "title": f"{rng.choice(SONG_WORDS)} {rng.choice(SONG_WORDS)}",
              "duration_sec": rng.randint(150, 400), "encore": False,
              "cover_of": rng.choice(COVERS)} for i in range(n)]
    encore = [{"order": i + 1, "title": f"{rng.choice(SONG_WORDS)} (encore)",
               "duration_sec": rng.randint(180, 360), "cover_of": None}
              for i in range(rng.randint(0, 2))]
    setlists.append({
        "concert_id": cid,
        "submitted_by_user_id": rng.randint(1, NUM_USERS),
        "source": rng.choice(["fan", "fan", "official", "predicted"]),
        "songs": songs,
        "encore_songs": encore,
        "total_duration_sec": sum(s["duration_sec"] for s in songs + encore),
        "submitted_at": datetime(2026, rng.randint(1, 5), rng.randint(1, 28),
                                 22, 0, tzinfo=timezone.utc),
        "upvotes": rng.randint(0, 40),
    })
db.setlists.insert_many(setlists)

# ---- reviews ------------------------------------------------------------
TITLES = ["Unreal energy", "Sound was crisp", "Worth every cent", "A bit flat",
          "Crowd went off", "Encore took the roof off", "Mixed feelings",
          "Best gig this year", "Logistics were rough", "Pure magic"]
BODIES = ["The mix was clean and the lighting design was stunning.",
          "Great setlist but the bar queues were brutal - bring water.",
          "Support act stole the show honestly.",
          "Sound bled a bit at the back but the energy made up for it.",
          "Tight performance, no filler, straight bangers.",
          "Venue was packed; arrive early for a good spot."]
TAGS = ["sound-quality", "crowd", "lights", "logistics", "support-act",
        "setlist", "intimate", "value", "acoustic"]

reviews = []
for _ in range(220):
    cid = rng.randint(1, NUM_CONCERTS)
    rating = rng.choices([5, 4, 3, 2, 1], weights=[40, 30, 18, 8, 4])[0]
    likers = rng.sample(range(1, NUM_USERS + 1), rng.randint(0, 8))
    reviews.append({
        "concert_id": cid,
        "user_id": rng.randint(1, NUM_USERS),
        "rating": rating,
        "title": rng.choice(TITLES),
        "body": rng.choice(BODIES),
        "tags": rng.sample(TAGS, rng.randint(1, 3)),
        "photos": [],
        "liked_by": likers,
        "helpful_count": len(likers),
        "posted_at": datetime(2026, rng.randint(1, 5), rng.randint(1, 28),
                              rng.randint(0, 23), rng.randint(0, 59), tzinfo=timezone.utc),
    })
db.reviews.insert_many(reviews)

# ---- artist_bios (one per artist) ---------------------------------------
REGIONS = ["NA", "EU", "ASIA", "OCE"]
bios = []
for aid in range(1, NUM_ARTISTS + 1):
    related = [x for x in rng.sample(range(1, NUM_ARTISTS + 1), 3) if x != aid][:2]
    bios.append({
        "artist_id": aid,
        "bio_text": f"Touring act #{aid}, known for a distinctive live sound and a loyal fanbase.",
        "tour_history": [{"year": y, "name": f"Tour {y}",
                          "regions": rng.sample(REGIONS, rng.randint(1, 3))}
                         for y in rng.sample([2022, 2023, 2024, 2025, 2026], rng.randint(1, 3))],
        "social": {"instagram": f"@artist{aid:02d}", "spotify_id": f"sp{aid:04d}"},
        "related_artists": related,
    })
db.artist_bios.insert_many(bios)

# ---- indexes ------------------------------------------------------------
db.setlists.create_index([("concert_id", ASCENDING)])
db.reviews.create_index([("concert_id", ASCENDING), ("posted_at", DESCENDING)])
db.reviews.create_index([("body", TEXT), ("title", TEXT)])
db.artist_bios.create_index([("artist_id", ASCENDING)], unique=True)

print("Seeded gigtrack Mongo collections:")
for coll in ("setlists", "reviews", "artist_bios"):
    print(f"  {coll}: {db[coll].count_documents({})} docs")

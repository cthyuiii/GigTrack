"""
GigTrack — Sample MongoDB queries.
Each function corresponds to a representative MongoDB query used by the app.
"""
import os
from dotenv import load_dotenv
load_dotenv()
from pymongo import MongoClient, DESCENDING

client = MongoClient(os.environ.get("MONGO_URI", "mongodb://localhost:27017"))
db = client["gigtrack"]


# ---- CRUD ---------------------------------------------------------------

def create_review(concert_id, user_id, rating, title, body, tags=None, photos=None):
    """Insert a new review document."""
    from datetime import datetime, timezone
    doc = {
        "concert_id": concert_id,
        "user_id":    user_id,
        "rating":     rating,
        "title":      title,
        "body":       body,
        "tags":       tags or [],
        "photos":     photos or [],
        "helpful_count": 0,
        "posted_at":  datetime.now(timezone.utc),
    }
    return db.reviews.insert_one(doc).inserted_id


def get_reviews_for_concert(concert_id, limit=20):
    """Most recent reviews for a concert."""
    return list(
        db.reviews.find({"concert_id": concert_id})
                  .sort("posted_at", DESCENDING)
                  .limit(limit)
    )


def upvote_review(review_id):
    """Atomic increment of a counter inside a document."""
    return db.reviews.update_one({"_id": review_id}, {"$inc": {"helpful_count": 1}})


def delete_review(review_id):
    return db.reviews.delete_one({"_id": review_id})


# ---- Complex / aggregation ----------------------------------------------

def average_rating_per_concert():
    """Aggregation pipeline: avg rating + review count grouped by concert."""
    pipeline = [
        {"$group": {
            "_id":           "$concert_id",
            "avg_rating":    {"$avg": "$rating"},
            "review_count":  {"$sum": 1},
            "top_tags":      {"$push": "$tags"},
        }},
        {"$sort": {"avg_rating": -1}},
    ]
    return list(db.reviews.aggregate(pipeline))


def search_reviews(text):
    """Full-text search over review title + body (uses the text index)."""
    return list(
        db.reviews.find(
            {"$text": {"$search": text}},
            {"score": {"$meta": "textScore"}}
        ).sort([("score", {"$meta": "textScore"})])
    )


def tag_frequency():
    """Unwind embedded tags and count occurrences."""
    pipeline = [
        {"$unwind": "$tags"},
        {"$group": {"_id": "$tags", "n": {"$sum": 1}}},
        {"$sort":  {"n": -1}},
    ]
    return list(db.reviews.aggregate(pipeline))


def setlist_summary(concert_id):
    """Pull a setlist + computed encore-vs-main split."""
    pipeline = [
        {"$match": {"concert_id": concert_id}},
        {"$project": {
            "concert_id":         1,
            "main_set_count":     {"$size": "$songs"},
            "encore_count":       {"$size": "$encore_songs"},
            "total_duration_sec": 1,
            "covers": {
                "$filter": {
                    "input": "$songs",
                    "as":    "s",
                    "cond":  {"$ne": ["$$s.cover_of", None]},
                }
            },
        }},
    ]
    return list(db.setlists.aggregate(pipeline))


def add_song_to_setlist(concert_id, song):
    """Append a song to an existing setlist using $push — no schema migration needed."""
    return db.setlists.update_one(
        {"concert_id": concert_id},
        {"$push": {"songs": song}}
    )


# ---- Advanced aggregation -----------------------------------------------

def review_dashboard():
    """$facet — compute several independent analytics in ONE pass over reviews.

    Returns rating distribution, top tags, and overall stats together. $facet
    is the document-DB answer to running multiple GROUP BYs at once; the SQL
    equivalent would be several separate queries or UNIONs.
    """
    pipeline = [
        {"$facet": {
            "rating_distribution": [
                {"$group": {"_id": "$rating", "n": {"$sum": 1}}},
                {"$sort": {"_id": -1}},
            ],
            "top_tags": [
                {"$unwind": "$tags"},
                {"$group": {"_id": "$tags", "n": {"$sum": 1}}},
                {"$sort": {"n": -1}},
                {"$limit": 5},
            ],
            "overall": [
                {"$group": {"_id": None,
                            "avg_rating": {"$avg": "$rating"},
                            "total_reviews": {"$sum": 1},
                            "total_likes": {"$sum": "$helpful_count"}}},
            ],
        }},
    ]
    return list(db.reviews.aggregate(pipeline))


def concerts_with_setlist_and_reviews(min_rating=4):
    """$lookup — join reviews to their setlist by concert_id (a NoSQL join).

    Demonstrates that MongoDB can relate collections server-side; we attach each
    concert's setlist to its highly-rated reviews and project a compact summary.
    """
    pipeline = [
        {"$match": {"rating": {"$gte": min_rating}}},
        {"$lookup": {
            "from": "setlists",
            "localField": "concert_id",
            "foreignField": "concert_id",
            "as": "setlist",
        }},
        {"$addFields": {
            "song_count": {"$size": {"$ifNull": [
                {"$arrayElemAt": ["$setlist.songs", 0]}, []]}},
        }},
        {"$project": {
            "_id": 0, "concert_id": 1, "rating": 1, "title": 1, "song_count": 1,
        }},
        {"$sort": {"rating": -1, "concert_id": 1}},
        {"$limit": 20},
    ]
    return list(db.reviews.aggregate(pipeline))


if __name__ == "__main__":
    import json
    from bson import ObjectId

    def jdefault(o):
        if isinstance(o, ObjectId):
            return str(o)
        return str(o)

    print("== average_rating_per_concert ==")
    print(json.dumps(average_rating_per_concert(), default=jdefault, indent=2))

    print("\n== tag_frequency ==")
    print(json.dumps(tag_frequency(), default=jdefault, indent=2))

    print("\n== setlist_summary(concert_id=6) ==")
    print(json.dumps(setlist_summary(6), default=jdefault, indent=2))

    print("\n== review_dashboard ($facet) ==")
    print(json.dumps(review_dashboard(), default=jdefault, indent=2))

    print("\n== concerts_with_setlist_and_reviews ($lookup) ==")
    print(json.dumps(concerts_with_setlist_and_reviews(), default=jdefault, indent=2))

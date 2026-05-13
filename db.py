"""
SQLite-based deduplication layer.
Tracks which posts have already been seen/notified to avoid duplicates.
Also stores parsed listings and rejected fingerprints for the web UI.
"""

import hashlib
import json
import logging
import re
import sqlite3
from datetime import datetime

from config import DB_PATH

logger = logging.getLogger(__name__)


def _get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS seen_posts (
            post_id TEXT PRIMARY KEY,
            group_url TEXT,
            first_seen TEXT,
            notified INTEGER DEFAULT 0
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS listings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id TEXT,
            group_url TEXT,
            permalink TEXT,
            raw_text TEXT,
            price INTEGER,
            rooms REAL,
            sqm INTEGER,
            bathrooms REAL,
            location_name TEXT,
            in_target_area INTEGER,
            has_mamad INTEGER,
            has_parking INTEGER,
            is_sublet INTEGER,
            summary TEXT,
            highlights TEXT,
            status TEXT DEFAULT 'pending',
            fingerprint TEXT,
            created_at TEXT,
            updated_at TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS rejected_fingerprints (
            fingerprint TEXT PRIMARY KEY,
            rejected_at TEXT
        )
    """)
    conn.commit()
    return conn


def is_new_post(post_id: str) -> bool:
    """Check if a post has NOT been seen before."""
    conn = _get_connection()
    try:
        row = conn.execute(
            "SELECT 1 FROM seen_posts WHERE post_id = ?", (post_id,)
        ).fetchone()
        return row is None
    finally:
        conn.close()


def mark_seen(post_id: str, group_url: str, notified: bool = False):
    """Mark a post as seen."""
    conn = _get_connection()
    try:
        conn.execute(
            """INSERT OR REPLACE INTO seen_posts (post_id, group_url, first_seen, notified)
               VALUES (?, ?, ?, ?)""",
            (post_id, group_url, datetime.utcnow().isoformat(), int(notified)),
        )
        conn.commit()
    finally:
        conn.close()


def get_all_seen_ids(group_url: str | None = None) -> set[str]:
    """Return the set of post IDs already in seen_posts. Optionally restricted
    to a single group_url. Used to short-circuit scraping when a scroll has
    only already-seen posts."""
    conn = _get_connection()
    try:
        if group_url:
            cur = conn.execute(
                "SELECT post_id FROM seen_posts WHERE group_url = ?",
                (group_url,),
            )
        else:
            cur = conn.execute("SELECT post_id FROM seen_posts")
        return {row[0] for row in cur.fetchall()}
    finally:
        conn.close()


def filter_new_posts(posts: list[dict]) -> list[dict]:
    """Return only posts that haven't been seen before."""
    new_posts = []
    for post in posts:
        post_id = post.get("id", "")
        if not post_id:
            continue
        if is_new_post(post_id):
            new_posts.append(post)
        else:
            logger.debug("Skipping already-seen post: %s", post_id[:50])
    logger.info("Dedup: %d posts in, %d new", len(posts), len(new_posts))
    return new_posts


def mark_posts_seen(posts: list[dict], notified: bool = False):
    """Mark a batch of posts as seen."""
    conn = _get_connection()
    try:
        for post in posts:
            post_id = post.get("id", "") if isinstance(post, dict) else post.post_id
            group_url = post.get("group_url", "") if isinstance(post, dict) else post.group_url
            conn.execute(
                """INSERT OR REPLACE INTO seen_posts (post_id, group_url, first_seen, notified)
                   VALUES (?, ?, ?, ?)""",
                (post_id, group_url, datetime.utcnow().isoformat(), int(notified)),
            )
        conn.commit()
    finally:
        conn.close()


def get_stats() -> dict:
    """Get database statistics."""
    conn = _get_connection()
    try:
        total = conn.execute("SELECT COUNT(*) FROM seen_posts").fetchone()[0]
        notified = conn.execute(
            "SELECT COUNT(*) FROM seen_posts WHERE notified = 1"
        ).fetchone()[0]
        return {"total_seen": total, "total_notified": notified}
    finally:
        conn.close()


# --- Listing management for web UI ---


def compute_fingerprint(listing) -> str:
    """Generate a normalized hash fingerprint for cross-group dedup.

    Works with both ApartmentListing objects and dicts.
    """
    if isinstance(listing, dict):
        price = listing.get("price") or listing.get("price_nis")
        rooms = listing.get("rooms")
        location = listing.get("location_name", "") or ""
        raw = listing.get("raw_text", "") or ""
    else:
        price = listing.price
        rooms = listing.rooms
        location = listing.location_name or ""
        raw = listing.raw_text or ""

    # Normalize numeric values to avoid int/float mismatches
    price_str = str(int(price)) if price is not None else ""
    rooms_str = str(float(rooms)) if rooms is not None else ""

    # Normalize: lowercase, collapse whitespace, take first 200 chars of text
    norm_text = re.sub(r"\s+", " ", raw.strip().lower())[:200]
    norm_location = location.strip().lower()

    key = f"{price_str}|{rooms_str}|{norm_location}|{norm_text}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]


def save_listing(listing_data: dict) -> int:
    """Insert a new listing into the listings table. Returns the new row id."""
    conn = _get_connection()
    now = datetime.utcnow().isoformat()
    try:
        cur = conn.execute(
            """INSERT INTO listings
               (post_id, group_url, permalink, raw_text, price, rooms, sqm,
                bathrooms, location_name, in_target_area, has_mamad, has_parking,
                is_sublet, summary, highlights, status, fingerprint,
                created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                listing_data.get("post_id"),
                listing_data.get("group_url"),
                listing_data.get("permalink"),
                listing_data.get("raw_text"),
                listing_data.get("price"),
                listing_data.get("rooms"),
                listing_data.get("sqm"),
                listing_data.get("bathrooms"),
                listing_data.get("location_name"),
                1 if listing_data.get("in_target_area") else (0 if listing_data.get("in_target_area") is False else None),
                1 if listing_data.get("has_mamad") else (0 if listing_data.get("has_mamad") is False else None),
                1 if listing_data.get("has_parking") else (0 if listing_data.get("has_parking") is False else None),
                1 if listing_data.get("is_sublet") else 0,
                listing_data.get("summary"),
                json.dumps(listing_data.get("highlights", []), ensure_ascii=False),
                listing_data.get("status", "pending"),
                listing_data.get("fingerprint"),
                now,
                now,
            ),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def get_listings(status: str | None = None) -> list[dict]:
    """Get listings, optionally filtered by status. Returns list of dicts."""
    conn = _get_connection()
    try:
        if status:
            rows = conn.execute(
                "SELECT * FROM listings WHERE status = ? ORDER BY created_at DESC",
                (status,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM listings ORDER BY created_at DESC"
            ).fetchall()
        result = []
        for row in rows:
            d = dict(row)
            # Parse highlights JSON back to list
            if d.get("highlights"):
                try:
                    d["highlights"] = json.loads(d["highlights"])
                except (json.JSONDecodeError, TypeError):
                    d["highlights"] = []
            else:
                d["highlights"] = []
            result.append(d)
        return result
    finally:
        conn.close()


def update_listing_status(listing_id: int, status: str) -> bool:
    """Update listing status. If rejecting, also add fingerprint to rejected table."""
    conn = _get_connection()
    now = datetime.utcnow().isoformat()
    try:
        conn.execute(
            "UPDATE listings SET status = ?, updated_at = ? WHERE id = ?",
            (status, now, listing_id),
        )
        if status == "rejected":
            # Get the fingerprint for this listing
            row = conn.execute(
                "SELECT fingerprint FROM listings WHERE id = ?", (listing_id,)
            ).fetchone()
            if row and row["fingerprint"]:
                conn.execute(
                    "INSERT OR IGNORE INTO rejected_fingerprints (fingerprint, rejected_at) VALUES (?, ?)",
                    (row["fingerprint"], now),
                )
        elif status in ("pending", "saved"):
            # If un-rejecting, remove fingerprint from rejected table
            row = conn.execute(
                "SELECT fingerprint FROM listings WHERE id = ?", (listing_id,)
            ).fetchone()
            if row and row["fingerprint"]:
                conn.execute(
                    "DELETE FROM rejected_fingerprints WHERE fingerprint = ?",
                    (row["fingerprint"],),
                )
        conn.commit()
        return True
    finally:
        conn.close()


def is_fingerprint_rejected(fingerprint: str) -> bool:
    """Check if a fingerprint has been rejected."""
    conn = _get_connection()
    try:
        row = conn.execute(
            "SELECT 1 FROM rejected_fingerprints WHERE fingerprint = ?",
            (fingerprint,),
        ).fetchone()
        return row is not None
    finally:
        conn.close()

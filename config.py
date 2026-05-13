"""
Configuration for the Tel Aviv Apartment Monitor.
"""

import os
from pathlib import Path

# --- Project paths ---
PROJECT_DIR = Path(__file__).parent
DB_PATH = PROJECT_DIR / "seen_posts.db"
LOG_PATH = PROJECT_DIR / "monitor.log"

# --- Editable values loaded from settings.json ---
# Managed via the web UI's Settings page, with baked-in defaults as fallback.
# Re-read on every process start, so cron-triggered monitor.py runs always
# pick up the latest values.
from settings import load_settings as _load_settings

_s = _load_settings()
FACEBOOK_GROUPS = list(_s["facebook_groups"])
FACEBOOK_ENABLED = bool(_s.get("facebook_enabled", False))
YAD2_SEARCH_URLS = list(_s["yad2_search_urls"])
INCLUSION_POLYGON = [tuple(p) for p in _s["polygon"]]
TARGET_AREA_DESCRIPTION = str(_s.get("target_area_description") or "")

# --- Apartment search criteria ---
# Editable fields (max_rent, min_rent, ideal_max_rent, min_rooms, min_sqm,
# min_bathrooms, sublet_ok) are loaded from settings.json. Non-editable
# fields (the *_missing flags and nice_to_have list) stay hardcoded below.
CRITERIA = {
    "max_rent": int(_s["max_rent"]),
    "min_rent": int(_s["min_rent"]),
    "min_rooms": float(_s["min_rooms"]),
    "min_sqm": int(_s["min_sqm"]),
    "min_bathrooms": float(_s["min_bathrooms"]),
    "ideal_max_rent": int(_s["ideal_max_rent"]),
    "sublet_ok": bool(_s["sublet_ok"]),

    # Not user-editable
    "max_bathrooms_missing": True,
    "max_sqm_missing": True,
    "nice_to_have": ["mamad", "parking"],
}

# --- Slack ---
# Prefers the new APT_RADAR_SLACK_WEBHOOK; falls back to legacy TLV_APT_SLACK_WEBHOOK.
SLACK_WEBHOOK_URL = (
    os.environ.get("APT_RADAR_SLACK_WEBHOOK")
    or os.environ.get("TLV_APT_SLACK_WEBHOOK")
    or ""
)

# --- Chrome scrape behavior ---
# How many times to scroll the group page to load more posts
SCROLL_COUNT = 8
# Seconds to wait between scrolls for content to load
SCROLL_DELAY = 2.5
# Seconds to wait for initial page load
PAGE_LOAD_DELAY = 5

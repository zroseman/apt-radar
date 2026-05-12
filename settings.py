"""
Editable settings layer.

Persists user-tunable values (price/rooms criteria, geographic polygon,
Yad2 search URLs) in `settings.json`. Falls back to baked-in defaults
if the file is missing or malformed.

`config.py` reads from this module on import, so any cron-triggered
`monitor.py` run picks up changes automatically. The Flask UI re-reads
`settings.json` directly on each request.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

logger = logging.getLogger(__name__)

SETTINGS_PATH = Path(__file__).parent / "settings.json"


# Baked-in defaults — used the first time the app runs (before settings.json
# exists) and as fallbacks for any field missing from a partial settings file.
# These are example values — users override them via the Settings page.
# The Facebook groups list is pre-seeded with Tel Aviv apartment groups, useful
# as a starting point for Israeli users (anyone else can replace them).
DEFAULTS: dict = {
    "max_rent": 3000,
    "min_rent": 1000,
    "ideal_max_rent": 2500,
    "min_rooms": 2,
    "min_sqm": 50,
    "min_bathrooms": 1,
    "sublet_ok": True,
    # Polygon stored as list of [lat, lng] pairs (NOT GeoJSON's lng/lat order).
    # Empty = no geographic filter applied.
    "polygon": [],
    "yad2_search_urls": [],
    # Empty by default — Facebook scraping is opt-in (requires Chrome debug setup).
    # The settings.json.example file ships with example FB groups for Tel Aviv users
    # who want to enable the FB path.
    "facebook_groups": [],
}


def load_settings() -> dict:
    """Load settings.json, falling back to DEFAULTS for missing keys."""
    if SETTINGS_PATH.exists():
        try:
            stored = json.loads(SETTINGS_PATH.read_text())
            merged = {**DEFAULTS, **stored}
            return merged
        except (json.JSONDecodeError, OSError) as e:
            logger.error("Failed to read settings.json (%s) — using defaults", e)
    return dict(DEFAULTS)


def save_settings(updates: dict) -> None:
    """
    Atomically merge `updates` into settings.json. Writes to a tmp file
    then renames so a crash mid-write can't corrupt the file.
    """
    current = load_settings()
    current.update(updates)
    tmp = SETTINGS_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(current, indent=2, ensure_ascii=False))
    tmp.replace(SETTINGS_PATH)
    logger.info("Saved settings.json (%d keys)", len(current))


# --- Yad2 URL regeneration ---


def update_yad2_url(url: str, min_price: int, max_price: int, min_rooms: float) -> str:
    """
    Replace minPrice/maxPrice/minRooms params on a Yad2 search URL,
    preserving every other param (notably bBox, neighborhood, area).
    """
    parsed = urlparse(url)
    qs = parse_qs(parsed.query, keep_blank_values=True)
    qs["minPrice"] = [str(int(min_price))]
    qs["maxPrice"] = [str(int(max_price))]
    # Yad2 accepts "4.5" and "5"; normalize integer-valued floats to int form
    if float(min_rooms).is_integer():
        qs["minRooms"] = [str(int(min_rooms))]
    else:
        qs["minRooms"] = [str(float(min_rooms))]
    new_query = urlencode(qs, doseq=True)
    return urlunparse(parsed._replace(query=new_query))


def update_yad2_urls(urls: list[str], min_price: int, max_price: int, min_rooms: float) -> list[str]:
    return [update_yad2_url(u, min_price, max_price, min_rooms) for u in urls]


# --- Facebook group URL parsing ---


class FacebookURLError(ValueError):
    pass


def parse_facebook_groups(text: str) -> list[str]:
    """
    Parse a textarea of Facebook group URLs (one per line). Lines starting
    with `#` and blank lines are ignored. Each URL must look like a
    facebook.com/groups/... link. Trailing slashes are normalized.
    """
    urls: list[str] = []
    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        # Tolerate users pasting without scheme
        if line.startswith("facebook.com/") or line.startswith("www.facebook.com/"):
            line = "https://" + line
        if not line.startswith(("http://", "https://")):
            raise FacebookURLError(f"Not a URL: {raw_line!r}")
        parsed = urlparse(line)
        host = (parsed.netloc or "").lower()
        if "facebook.com" not in host:
            raise FacebookURLError(f"Not a facebook.com URL: {line}")
        if "/groups/" not in parsed.path:
            raise FacebookURLError(f"Not a Facebook group URL (must contain /groups/): {line}")
        # Normalize: ensure trailing slash on the group root
        normalized = f"https://www.facebook.com{parsed.path}"
        if not normalized.endswith("/"):
            normalized += "/"
        urls.append(normalized)
    if not urls:
        raise FacebookURLError("At least one Facebook group URL is required")
    # Dedup while preserving order
    seen: set[str] = set()
    unique: list[str] = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            unique.append(u)
    return unique


# --- GeoJSON polygon parsing ---


class GeoJSONError(ValueError):
    pass


def parse_geojson_polygon(text: str) -> list[list[float]]:
    """
    Accept either a full GeoJSON FeatureCollection (what geojson.io exports)
    or a bare Polygon geometry. Return a list of [lat, lng] pairs.

    GeoJSON stores coordinates as [lng, lat]; we flip to [lat, lng] to
    match the rest of this codebase. The closing duplicate point is dropped.
    """
    text = (text or "").strip()
    if not text:
        raise GeoJSONError("GeoJSON is empty")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise GeoJSONError(f"Not valid JSON: {e}") from e

    polygon_geom = None
    typ = data.get("type")
    if typ == "FeatureCollection":
        for f in data.get("features", []):
            geom = f.get("geometry") or {}
            if geom.get("type") == "Polygon":
                polygon_geom = geom
                break
    elif typ == "Feature":
        geom = data.get("geometry") or {}
        if geom.get("type") == "Polygon":
            polygon_geom = geom
    elif typ == "Polygon":
        polygon_geom = data

    if polygon_geom is None:
        raise GeoJSONError("Could not find a Polygon. Draw a polygon on geojson.io and copy the full output.")

    coords = polygon_geom.get("coordinates")
    if not coords or not isinstance(coords, list) or not coords[0]:
        raise GeoJSONError("Polygon has no coordinates")

    ring = coords[0]  # outer ring; ignore holes
    if not isinstance(ring, list) or len(ring) < 4:
        raise GeoJSONError("Polygon must have at least 3 distinct points")

    # Drop the closing duplicate point if present
    if ring[0] == ring[-1]:
        ring = ring[:-1]

    result: list[list[float]] = []
    for pt in ring:
        if not isinstance(pt, (list, tuple)) or len(pt) < 2:
            raise GeoJSONError(f"Bad point in polygon: {pt}")
        lng, lat = float(pt[0]), float(pt[1])
        # Sanity check — refuse polygons obviously not in Israel
        if not (29 < lat < 34 and 33 < lng < 36):
            raise GeoJSONError(
                f"Point ({lat}, {lng}) is outside Israel — did you draw on the wrong map?"
            )
        result.append([lat, lng])

    if len(result) < 3:
        raise GeoJSONError("Polygon must have at least 3 distinct points")

    return result

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
# Pre-seeded with a working Tel Aviv example so a new user can run their first
# scan immediately and see real results. Non-Israeli users replace via the UI.
DEFAULTS: dict = {
    "min_rent": 10000,
    "max_rent": 20000,
    "ideal_max_rent": 15000,
    "min_rooms": 4,
    "min_sqm": 90,
    "min_bathrooms": 1,
    "sublet_ok": True,
    # Facebook scanning is OFF by default. Users opt in via the dashboard toggle
    # (or the wizard sets it to True when they pick the Yad2+FB path). When
    # False, no FB scraping happens even if Chrome debug is reachable and
    # facebook_groups is populated.
    "facebook_enabled": False,
    # Polygon stored as list of [lat, lng] pairs (NOT GeoJSON's lng/lat order).
    # Empty by default — geo_filter treats empty polygon as "no filter, accept
    # all locations". Friends in non-Israeli cities don't accidentally drop
    # every Yad2 result through a Tel Aviv-shaped filter.
    "polygon": [],
    "yad2_search_urls": [
        "https://www.yad2.co.il/realestate/rent/tel-aviv-area?minPrice=10000&maxPrice=20000&minRooms=4&zoom=15&area=1&city=5000&neighborhood=1461&bBox=32.081987%2C34.764798%2C32.096724%2C34.787711",
        "https://www.yad2.co.il/realestate/rent/tel-aviv-area?minPrice=10000&maxPrice=20000&minRooms=4&zoom=15&area=1&city=5000&neighborhood=1461&bBox=32.071149%2C34.761555%2C32.085888%2C34.784468",
    ],
    # Empty by default — Facebook scraping is opt-in (requires Chrome debug setup).
    # See STARTER_FACEBOOK_GROUPS below for a curated set the user can add.
    "facebook_groups": [],
}


# Starter Facebook groups offered to users who enable the Facebook path.
# Displayed in the settings UI as a separate "available groups" panel — the
# user opts in by clicking a button to copy them into the active list.
STARTER_FACEBOOK_GROUPS: list[str] = [
    "https://www.facebook.com/groups/1673941052823845/",
    "https://www.facebook.com/groups/785935868134249/",
    "https://www.facebook.com/groups/458499457501175/",
    "https://www.facebook.com/groups/188430365379/",
    "https://www.facebook.com/groups/TelAvivApartments/",
]


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


def polygon_bbox(polygon: list[list[float]]) -> tuple[float, float, float, float]:
    """Compute the axis-aligned bounding box of a [lat, lng] polygon.
    Returns (min_lat, min_lng, max_lat, max_lng). Raises if empty."""
    if not polygon:
        raise ValueError("Cannot compute bbox of empty polygon")
    lats = [p[0] for p in polygon]
    lngs = [p[1] for p in polygon]
    return min(lats), min(lngs), max(lats), max(lngs)


def yad2_url_from_polygon(
    polygon: list[list[float]],
    min_price: int,
    max_price: int,
    min_rooms: float,
) -> str:
    """Auto-generate a Yad2 rental search URL from a polygon's bounding box.
    The polygon also acts as a post-scrape filter, so the resulting URL
    may return some listings that get rejected by the polygon check."""
    min_lat, min_lng, max_lat, max_lng = polygon_bbox(polygon)
    bbox_raw = f"{min_lat},{min_lng},{max_lat},{max_lng}"
    bbox_enc = urlencode({"bBox": bbox_raw})  # produces bBox=lat1%2Clng1%2Clat2%2Clng2
    rooms_str = str(int(min_rooms)) if float(min_rooms).is_integer() else str(float(min_rooms))
    return (
        f"https://www.yad2.co.il/realestate/rent?"
        f"minPrice={int(min_price)}&maxPrice={int(max_price)}&minRooms={rooms_str}&{bbox_enc}"
    )


# --- Facebook group URL parsing ---


class FacebookURLError(ValueError):
    pass


def parse_facebook_groups(text: str) -> list[str]:
    """
    Parse a textarea of Facebook group URLs (one per line). Lines starting
    with `#` and blank lines are ignored. Each URL must look like a
    facebook.com/groups/... link. Trailing slashes are normalized.

    Empty input returns an empty list — Facebook scraping is opt-in.
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
    polygon_count = 0
    typ = data.get("type")
    if typ == "FeatureCollection":
        for f in data.get("features", []):
            geom = f.get("geometry") or {}
            if geom.get("type") == "Polygon":
                polygon_count += 1
                if polygon_geom is None:
                    polygon_geom = geom
    elif typ == "Feature":
        geom = data.get("geometry") or {}
        if geom.get("type") == "Polygon":
            polygon_geom = geom
            polygon_count = 1
    elif typ == "Polygon":
        polygon_geom = data
        polygon_count = 1

    if polygon_geom is None:
        raise GeoJSONError("Could not find a Polygon. Draw a polygon on geojson.io and copy the full output.")
    if polygon_count > 1:
        raise GeoJSONError(
            f"Found {polygon_count} polygons in the GeoJSON — only one is supported. "
            "Delete the others on geojson.io and re-export."
        )

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
        # Loose sanity check — polygons need plausible Earth coordinates.
        if not (-90 < lat < 90 and -180 < lng < 180):
            raise GeoJSONError(
                f"Point ({lat}, {lng}) is not valid lat/lng — check coordinate order."
            )
        result.append([lat, lng])

    if len(result) < 3:
        raise GeoJSONError("Polygon must have at least 3 distinct points")

    return result

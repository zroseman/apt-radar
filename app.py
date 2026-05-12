#!/usr/bin/env python3
"""
Web UI for the Tel Aviv Apartment Monitor.
Shows matched listings and lets you save or reject them.
"""

import json
import os
import subprocess
from pathlib import Path

from flask import Flask, render_template, jsonify, request, redirect

from db import get_listings, update_listing_status
from settings import (
    STARTER_FACEBOOK_GROUPS,
    FacebookURLError,
    GeoJSONError,
    load_settings,
    parse_facebook_groups,
    parse_geojson_polygon,
    save_settings,
    update_yad2_urls,
    yad2_url_from_polygon,
)

app = Flask(__name__)


@app.route("/")
def index():
    listings = get_listings(status="pending")
    return render_template("index.html", listings=listings, current_tab="pending")


@app.route("/saved")
def saved():
    listings = get_listings(status="saved")
    return render_template("index.html", listings=listings, current_tab="saved")


@app.route("/rejected")
def rejected():
    listings = get_listings(status="rejected")
    return render_template("index.html", listings=listings, current_tab="rejected")


@app.route("/api/listings/<int:listing_id>/save", methods=["POST"])
def api_save(listing_id):
    update_listing_status(listing_id, "saved")
    return jsonify({"ok": True, "status": "saved"})


@app.route("/api/listings/<int:listing_id>/reject", methods=["POST"])
def api_reject(listing_id):
    update_listing_status(listing_id, "rejected")
    return jsonify({"ok": True, "status": "rejected"})


@app.route("/api/listings/<int:listing_id>/pending", methods=["POST"])
def api_pending(listing_id):
    update_listing_status(listing_id, "pending")
    return jsonify({"ok": True, "status": "pending"})


# --- Manual scan trigger ---

SCRIPT_DIR = Path(__file__).resolve().parent
SCAN_PID_FILE = SCRIPT_DIR / ".scan.pid"


def _scan_pid():
    """Return PID if a scan is genuinely still running, else None.
    Zombies and PID reuse are treated as not-running and the stale file is removed."""
    if not SCAN_PID_FILE.exists():
        return None
    try:
        pid = int(SCAN_PID_FILE.read_text().strip())
    except ValueError:
        SCAN_PID_FILE.unlink(missing_ok=True)
        return None
    try:
        out = subprocess.run(
            ["ps", "-p", str(pid), "-o", "stat=,command="],
            capture_output=True, text=True, timeout=2,
        )
    except Exception:
        return None
    line = out.stdout.strip()
    if out.returncode != 0 or not line:
        SCAN_PID_FILE.unlink(missing_ok=True)
        return None
    parts = line.split(None, 1)
    stat = parts[0] if parts else ""
    cmd = parts[1] if len(parts) > 1 else ""
    if stat.startswith("Z") or ("run_monitor" not in cmd and "monitor.py" not in cmd):
        SCAN_PID_FILE.unlink(missing_ok=True)
        return None
    return pid


@app.route("/api/scan/start", methods=["POST"])
def api_scan_start():
    if _scan_pid():
        return jsonify({"ok": False, "error": "Scan already running"}), 409
    env = {**os.environ, "TLV_APT_FORCE": "1", "TLV_APT_FOREGROUND": "1"}
    proc = subprocess.Popen(
        [str(SCRIPT_DIR / "run_monitor.sh")],
        cwd=str(SCRIPT_DIR),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    SCAN_PID_FILE.write_text(str(proc.pid))
    return jsonify({"ok": True, "pid": proc.pid})


@app.route("/api/scan/status")
def api_scan_status():
    pid = _scan_pid()
    return jsonify({"running": pid is not None, "pid": pid})


# --- Settings page ---


def _polygon_to_geojson(polygon: list) -> str:
    """Render the stored polygon back into a GeoJSON FeatureCollection
    so the user can edit it on geojson.io if they want."""
    if not polygon:
        return ""
    # Polygon is stored as [lat, lng]; GeoJSON wants [lng, lat]
    ring = [[float(p[1]), float(p[0])] for p in polygon]
    # Close the ring
    if ring and ring[0] != ring[-1]:
        ring.append(ring[0])
    fc = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {},
                "geometry": {"type": "Polygon", "coordinates": [ring]},
            }
        ],
    }
    return json.dumps(fc, indent=2)


@app.route("/settings", methods=["GET", "POST"])
def settings_page():
    error = None
    success = None

    if request.method == "POST":
        try:
            form = request.form
            min_rent = int(form["min_rent"])
            max_rent = int(form["max_rent"])
            ideal_max_rent = int(form["ideal_max_rent"])
            min_rooms = float(form["min_rooms"])
            min_sqm = int(form["min_sqm"])
            min_bathrooms = float(form["min_bathrooms"])
            sublet_ok = form.get("sublet_ok") == "on"
            facebook_enabled = form.get("facebook_enabled") == "on"

            # Cross-field validation
            if min_rent < 0 or max_rent < 0 or ideal_max_rent < 0:
                raise ValueError("Prices cannot be negative")
            if min_rent >= max_rent:
                raise ValueError("min_rent must be less than max_rent")
            if not (min_rent <= ideal_max_rent <= max_rent):
                raise ValueError("ideal_max_rent must be between min_rent and max_rent")
            if min_rooms <= 0 or min_sqm <= 0 or min_bathrooms <= 0:
                raise ValueError("Rooms / sqm / bathrooms must be positive")

            geojson_text = (form.get("geojson") or "").strip()
            current = load_settings()
            polygon = current["polygon"]
            if geojson_text:
                polygon = parse_geojson_polygon(geojson_text)

            # Facebook groups: parsed from textarea (one URL per line). Empty = no FB scraping.
            fb_groups = parse_facebook_groups(form.get("facebook_groups") or "")

            # Yad2 URLs: prefer what the user pasted into the textarea (one per line).
            # Falls back to the existing stored list. If the result is empty AND we
            # have a polygon, auto-generate a URL from the polygon's bounding box.
            yad2_raw = (form.get("yad2_search_urls") or "").strip()
            if yad2_raw:
                new_urls = [
                    line.strip() for line in yad2_raw.splitlines()
                    if line.strip() and not line.strip().startswith("#")
                ]
            else:
                new_urls = list(current["yad2_search_urls"])
            new_urls = update_yad2_urls(new_urls, min_rent, max_rent, min_rooms)
            if not new_urls and polygon:
                new_urls = [yad2_url_from_polygon(polygon, min_rent, max_rent, min_rooms)]

            save_settings({
                "min_rent": min_rent,
                "max_rent": max_rent,
                "ideal_max_rent": ideal_max_rent,
                "min_rooms": min_rooms,
                "min_sqm": min_sqm,
                "min_bathrooms": min_bathrooms,
                "sublet_ok": sublet_ok,
                "facebook_enabled": facebook_enabled,
                "polygon": polygon,
                "yad2_search_urls": new_urls,
                "facebook_groups": fb_groups,
            })

            # Optional: trigger a scan immediately after save (default behavior).
            if form.get("run_after_save") == "on" and not _scan_pid():
                env = {**os.environ, "TLV_APT_FORCE": "1", "TLV_APT_FOREGROUND": "1"}
                proc = subprocess.Popen(
                    [str(SCRIPT_DIR / "run_monitor.sh")],
                    cwd=str(SCRIPT_DIR),
                    env=env,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
                SCAN_PID_FILE.write_text(str(proc.pid))
                return redirect("/")

            success = "Settings saved."
        except (KeyError, ValueError, GeoJSONError, FacebookURLError) as e:
            error = str(e)

    s = load_settings()
    return render_template(
        "settings.html",
        current_tab="settings",
        settings=s,
        polygon_geojson=_polygon_to_geojson(s["polygon"]),
        yad2_urls=s["yad2_search_urls"],
        starter_facebook_groups=STARTER_FACEBOOK_GROUPS,
        error=error,
        success=success,
    )


if __name__ == "__main__":
    port = int(os.environ.get("APT_RADAR_PORT", "5055"))
    app.run(debug=True, host="0.0.0.0", port=port)

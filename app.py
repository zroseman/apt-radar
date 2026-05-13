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

from db import archive_all_pending, get_listings, update_listing_status
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


def _current_search_config_id() -> int:
    """Read the current search config id fresh on each request — settings
    may have been bumped via save_settings since process start."""
    return int(load_settings().get("search_config_id", 1))


def _listings_for_tab(status: str, include_previous: bool):
    return get_listings(
        status=status,
        search_config_id=_current_search_config_id(),
        include_previous=include_previous,
    )


def _previous_count(status: str) -> int:
    """How many listings of this status are from an older search config?
    Used to surface a 'show previous' banner on the dashboard."""
    all_count = len(get_listings(status=status, include_previous=True))
    current_count = len(get_listings(
        status=status, search_config_id=_current_search_config_id(),
        include_previous=False,
    ))
    return max(0, all_count - current_count)


@app.route("/")
def index():
    show_previous = request.args.get("show_previous") == "1"
    listings = _listings_for_tab("pending", show_previous)
    return render_template(
        "index.html",
        listings=listings, current_tab="pending",
        show_previous=show_previous,
        previous_count=_previous_count("pending"),
    )


@app.route("/saved")
def saved():
    show_previous = request.args.get("show_previous") == "1"
    listings = _listings_for_tab("saved", show_previous)
    return render_template(
        "index.html",
        listings=listings, current_tab="saved",
        show_previous=show_previous,
        previous_count=_previous_count("saved"),
    )


@app.route("/rejected")
def rejected():
    show_previous = request.args.get("show_previous") == "1"
    listings = _listings_for_tab("rejected", show_previous)
    return render_template(
        "index.html",
        listings=listings, current_tab="rejected",
        show_previous=show_previous,
        previous_count=_previous_count("rejected"),
    )


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


@app.route("/api/listings/archive-pending", methods=["POST"])
def api_archive_pending():
    """Soft-hide every currently-Pending listing. Future scans still see
    the post_id in seen_posts so duplicates won't reappear."""
    count = archive_all_pending(search_config_id=_current_search_config_id())
    return jsonify({"ok": True, "archived": count})


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
    env = {**os.environ, "TLV_APT_FOREGROUND": "1"}

    # Bootstrap mode marks everything as seen without surfacing — used on
    # first install so the user only sees genuinely-new listings going forward.
    bootstrap = (request.args.get("bootstrap") == "1") or (
        request.form.get("bootstrap") == "1"
    )

    # max_pages cap (used by the wizard on the first scan: ?max_pages=1
    # keeps it to one Yad2 page so the user sees results in ~15s, not 80s).
    max_pages_raw = request.args.get("max_pages") or request.form.get("max_pages")
    if max_pages_raw:
        try:
            mp = int(max_pages_raw)
            if 1 <= mp <= 20:
                env["APT_RADAR_MAX_PAGES"] = str(mp)
        except ValueError:
            pass

    args = [str(SCRIPT_DIR / "run_monitor.sh")]
    if bootstrap:
        args.append("--bootstrap")
    SCAN_PID_FILE.write_text("starting")
    try:
        proc = subprocess.Popen(
            args, cwd=str(SCRIPT_DIR), env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        SCAN_PID_FILE.write_text(str(proc.pid))
        return jsonify({
            "ok": True, "pid": proc.pid, "bootstrap": bootstrap,
            "max_pages": env.get("APT_RADAR_MAX_PAGES"),
        })
    except Exception as e:
        SCAN_PID_FILE.unlink(missing_ok=True)
        return jsonify({"ok": False, "error": str(e)}), 500


def _read_last_scan_summary() -> dict | None:
    """Read .last_scan.json (written by monitor.py at end of each scan).
    Wizard polls this to report scan results back to the user in chat."""
    path = SCRIPT_DIR / ".last_scan.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


@app.route("/api/scan/status")
def api_scan_status():
    pid = _scan_pid()
    return jsonify({
        "running": pid is not None,
        "pid": pid,
        "last_scan": _read_last_scan_summary(),
    })


# --- Settings page ---


def _polygon_to_geojson(polygon: list) -> str:
    """Render the stored polygon back into a GeoJSON FeatureCollection
    so the user can edit it on geojson.io if they want.
    Returns '' for empty or malformed polygons rather than crashing the
    settings page."""
    if not polygon:
        return ""
    try:
        # Polygon is stored as [lat, lng]; GeoJSON wants [lng, lat]
        ring = [[float(p[1]), float(p[0])] for p in polygon]
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
    except (TypeError, ValueError, IndexError):
        return ""


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
            target_area_description = (form.get("target_area_description") or "").strip()

            # Cross-field validation
            if min_rent < 0 or max_rent < 0 or ideal_max_rent < 0:
                raise ValueError("Prices cannot be negative")
            if min_rent >= max_rent:
                raise ValueError("min_rent must be less than max_rent")
            if not (min_rent <= ideal_max_rent <= max_rent):
                raise ValueError("ideal_max_rent must be between min_rent and max_rent")
            if min_rooms <= 0 or min_sqm <= 0 or min_bathrooms <= 0:
                raise ValueError("Rooms / sqm / bathrooms must be positive")

            current = load_settings()
            # Polygon is no longer surfaced in the UI — Yad2 uses its URL's
            # built-in bBox as the geographic filter. Preserve any existing
            # polygon in settings.json untouched for back-compat.
            polygon = current.get("polygon", [])

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
                "target_area_description": target_area_description,
            })

            # Optional: trigger a scan immediately after save (default behavior).
            if form.get("run_after_save") == "on" and not _scan_pid():
                env = {**os.environ, "TLV_APT_FOREGROUND": "1"}
                SCAN_PID_FILE.write_text("starting")
                try:
                    proc = subprocess.Popen(
                        [str(SCRIPT_DIR / "run_monitor.sh")],
                        cwd=str(SCRIPT_DIR),
                        env=env,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        start_new_session=True,
                    )
                    SCAN_PID_FILE.write_text(str(proc.pid))
                except Exception:
                    SCAN_PID_FILE.unlink(missing_ok=True)
                    raise
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


def _parse_port(raw: str) -> int:
    """Tolerate stray whitespace; fall back to 5055 on garbage input."""
    try:
        return int((raw or "").strip())
    except (ValueError, TypeError):
        return 5055


@app.before_request
def _block_cross_origin_state_changes():
    """Reject state-mutating requests from foreign origins.
    Local web pages can otherwise fetch() our endpoints (cookies/CORS not
    required for state-mutating POSTs in the no-cors mode)."""
    if request.method not in ("POST", "PUT", "DELETE", "PATCH"):
        return
    origin = request.headers.get("Origin", "")
    referer = request.headers.get("Referer", "")
    same_origin_prefix = request.host_url.rstrip("/")
    if origin and not origin.startswith(same_origin_prefix):
        return ("Cross-origin requests rejected", 403)
    if not origin and referer and not referer.startswith(same_origin_prefix):
        return ("Cross-origin requests rejected", 403)


if __name__ == "__main__":
    port = _parse_port(os.environ.get("APT_RADAR_PORT", "5055"))
    # debug=False — Werkzeug's debugger is RCE; host=127.0.0.1 — keep off the LAN.
    # Friends who want LAN access can change this manually and accept the risk.
    app.run(debug=False, host="127.0.0.1", port=port)

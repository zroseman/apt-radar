#!/usr/bin/env python3
"""
Tel Aviv Apartment Monitor — Main Orchestrator

Scrapes configured Facebook groups for apartment listings, filters them
against search criteria, deduplicates, and sends matches to Slack.

Usage:
    python3 monitor.py              # Run one full scan
    python3 monitor.py --dry-run    # Scan and parse, but don't notify
    python3 monitor.py --stats      # Show database stats
"""

import argparse
import logging
import sys
from datetime import datetime
from logging.handlers import RotatingFileHandler

from config import FACEBOOK_ENABLED, FACEBOOK_GROUPS, LOG_PATH, SEARCH_CONFIG_ID, YAD2_SEARCH_URLS
from chrome_scraper import scrape_all_groups
from post_parser import parse_and_filter_posts
from yad2_scraper import scrape_yad2_searches, yad2_cards_to_listings
from db import filter_new_posts, mark_posts_seen, mark_seen, get_stats, compute_fingerprint, is_fingerprint_rejected, save_listing, get_all_seen_ids
from slack_notifier import send_listings, reset_alerts

# Set up logging — rotate to keep monitor.log from growing unbounded.
# 5MB per file, keep 3 rotations (15MB total ceiling).
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        RotatingFileHandler(str(LOG_PATH), maxBytes=5 * 1024 * 1024, backupCount=3),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("monitor")


def _process_matching(matching, source_label: str, dry_run: bool):
    """Shared tail: fingerprint-dedupe, persist, and notify a list of ApartmentListings."""
    if not matching:
        logger.info("[%s] No matching apartments.", source_label)
        return

    filtered = []
    for m in matching:
        fp = compute_fingerprint(m)
        if is_fingerprint_rejected(fp):
            logger.info("[%s] Skipping rejected fingerprint for %s", source_label, m.post_id[:50])
            continue
        # Persist to listings table only on real runs — dry runs must
        # leave the DB untouched
        if not dry_run:
            save_listing({
                "post_id": m.post_id,
                "group_url": m.group_url,
                "permalink": m.permalink,
                "raw_text": m.raw_text,
                "price": m.price,
                "rooms": m.rooms,
                "sqm": m.sqm,
                "bathrooms": m.bathrooms,
                "location_name": m.location_name,
                "in_target_area": m.in_target_area,
                "has_mamad": m.has_mamad,
                "has_parking": m.has_parking,
                "is_sublet": m.is_sublet,
                "summary": m.summary,
                "highlights": m.highlights,
                "fingerprint": fp,
                "status": "pending",
                "search_config_id": SEARCH_CONFIG_ID,
            })
        filtered.append(m)
    logger.info("[%s] After rejection filter: %d listings", source_label, len(filtered))

    if not filtered:
        return

    if dry_run:
        logger.info("[%s] DRY RUN — would send %d notifications:", source_label, len(filtered))
        for m in filtered:
            logger.info(
                "  - Price: %s, Rooms: %s, Location: %s, Link: %s",
                f"{m.price:,}₪" if m.price else "?",
                m.rooms or "?",
                m.location_name or "?",
                m.permalink or "(no link)",
            )
            for h in m.highlights:
                logger.info("    • %s", h)
    else:
        sent = send_listings(filtered)
        for m in filtered:
            mark_seen(m.post_id, m.group_url, notified=True)
        logger.info("[%s] Sent %d Slack notifications", source_label, sent)


def _cdp_reachable() -> bool:
    """Quick check that the debug Chrome is running on port 9222."""
    import urllib.request
    try:
        urllib.request.urlopen("http://127.0.0.1:9222/json/version", timeout=2).read()
        return True
    except Exception:
        return False


def run_facebook_scan(dry_run: bool = False, bootstrap: bool = False):
    """Scrape Facebook groups, parse with Claude, notify on matches.

    bootstrap=True: mark every scraped post as seen and skip notifications.
    Used once on initial deployment to clear out the existing backlog.
    """
    if not FACEBOOK_ENABLED:
        logger.info("Facebook scanning disabled in settings — skipping FB scan.")
        return

    if not FACEBOOK_GROUPS:
        logger.info("No Facebook groups configured — skipping FB scan.")
        return

    logger.info("Scraping %d Facebook groups...", len(FACEBOOK_GROUPS))
    # Pass the set of already-seen post IDs so each group's scroll loop
    # can short-circuit once we're seeing only stale content.
    seen_fb_ids = get_all_seen_ids()
    all_posts = scrape_all_groups(FACEBOOK_GROUPS, seen_ids=seen_fb_ids)
    logger.info("Total posts scraped: %d", len(all_posts))

    stats = {"scraped": len(all_posts), "passed_filter": 0}

    if not all_posts:
        logger.warning("No FB posts scraped. Check Chrome and Facebook login.")
        return stats

    if bootstrap:
        mark_posts_seen(all_posts)
        logger.info("[BOOTSTRAP] Marked %d FB posts as seen, no notifications sent", len(all_posts))
        return stats

    new_posts = filter_new_posts(all_posts)
    logger.info("New (unseen) FB posts: %d", len(new_posts))

    if not new_posts:
        logger.info("No new FB posts since last scan.")
        return stats

    matching = parse_and_filter_posts(new_posts)
    logger.info("FB posts matching criteria: %d", len(matching))
    stats["passed_filter"] = len(matching)

    # Mark ALL new posts as seen (whether they matched or not),
    # but never on dry runs — those should leave no DB residue
    if not dry_run:
        mark_posts_seen(new_posts)

    _process_matching(matching, source_label="facebook", dry_run=dry_run)
    return stats


def run_yad2_scan(dry_run: bool = False, bootstrap: bool = False):
    """Scrape Yad2 search URLs and notify on new listings.

    bootstrap=True: mark every scraped card as seen and skip both
    notifications and the per-listing coordinate fetch (which is the
    slow part). Used once on initial deployment.
    """
    if not YAD2_SEARCH_URLS:
        logger.info("No Yad2 search URLs configured, skipping.")
        return

    logger.info("Scraping %d Yad2 search URLs...", len(YAD2_SEARCH_URLS))
    seen_yad2_ids = get_all_seen_ids()
    # Wizard / API can shortcut a scan to just page 1 via APT_RADAR_MAX_PAGES.
    # Default 10 is the steady-state behavior; first-scan wizard sets it to 1.
    import os
    try:
        max_pages = int(os.environ.get("APT_RADAR_MAX_PAGES", "10"))
    except ValueError:
        max_pages = 10
    if max_pages != 10:
        logger.info("Yad2: max_pages overridden to %d via APT_RADAR_MAX_PAGES", max_pages)
    cards = scrape_yad2_searches(
        YAD2_SEARCH_URLS,
        seen_ids=seen_yad2_ids,
        max_pages_per_url=max_pages,
    )
    logger.info("Total Yad2 cards: %d", len(cards))

    stats = {"scraped": len(cards), "passed_filter": 0}

    if not cards:
        return stats

    pseudo_posts = [
        {"id": c.get("id", ""), "group_url": "yad2", "permalink": c.get("url", "")}
        for c in cards
        if c.get("id")
    ]

    if bootstrap:
        mark_posts_seen(pseudo_posts)
        logger.info("[BOOTSTRAP] Marked %d Yad2 cards as seen, no notifications sent", len(pseudo_posts))
        return stats

    new_pseudo = filter_new_posts(pseudo_posts)
    logger.info("New (unseen) Yad2 cards: %d", len(new_pseudo))

    if not new_pseudo:
        return stats

    new_ids = {p["id"] for p in new_pseudo}
    new_cards = [c for c in cards if c.get("id") in new_ids]

    matching = yad2_cards_to_listings(new_cards, fetch_coords=True)
    stats["passed_filter"] = len(matching)

    if not dry_run:
        mark_posts_seen(new_pseudo)

    _process_matching(matching, source_label="yad2", dry_run=dry_run)
    return stats


def _write_scan_summary(summary: dict) -> None:
    """Persist last-scan stats to .last_scan.json so the dashboard / wizard
    can show what happened ("found N, M passed filter") without grepping the log."""
    try:
        from config import PROJECT_DIR
        path = PROJECT_DIR / ".last_scan.json"
        import json as _json
        path.write_text(_json.dumps(summary, indent=2, default=str))
    except Exception:
        logger.exception("Failed to write .last_scan.json")


def run_scan(dry_run: bool = False, bootstrap: bool = False):
    """Execute one full scan: Yad2 searches + (optional) Facebook groups.

    bootstrap=True: marks everything as seen without notifying or saving to
    listings table. Use on first install to skip the backlog so the user
    only sees genuinely-new listings going forward.
    """
    start = datetime.now()
    mode = "BOOTSTRAP" if bootstrap else ("DRY RUN" if dry_run else "LIVE")
    logger.info("=== Starting apartment scan at %s [%s] ===", start.isoformat(), mode)

    reset_alerts()

    summary: dict = {
        "started_at": start.isoformat(),
        "mode": mode,
        "bootstrap": bootstrap,
        "errors": [],
        "yad2": {"scraped": 0, "passed_filter": 0, "skipped": False},
        "facebook": {"scraped": 0, "passed_filter": 0, "skipped": False},
    }

    # Both scrapers go through the user's logged-in Chrome via CDP. If that
    # Chrome isn't running, log a clear message and skip.
    if not _cdp_reachable():
        msg = "Chrome debug port 9222 not reachable — both scrapes skipped."
        logger.warning(msg)
        summary["errors"].append(msg)
        summary["yad2"]["skipped"] = True
        summary["facebook"]["skipped"] = True
        elapsed = (datetime.now() - start).total_seconds()
        summary["completed_at"] = datetime.now().isoformat()
        summary["duration_seconds"] = round(elapsed, 1)
        logger.info("=== Scan aborted in %.1fs (Chrome debug unreachable) ===", elapsed)
        _write_scan_summary(summary)
        return

    # Yad2 first (fast, always reliable). Facebook second (slow, opt-in).
    try:
        yad2_stats = run_yad2_scan(dry_run=dry_run, bootstrap=bootstrap) or {}
        summary["yad2"].update(yad2_stats)
    except Exception as e:
        logger.exception("Yad2 scan failed")
        summary["errors"].append(f"yad2: {e}")

    try:
        fb_stats = run_facebook_scan(dry_run=dry_run, bootstrap=bootstrap) or {}
        summary["facebook"].update(fb_stats)
    except Exception as e:
        logger.exception("Facebook scan failed")
        summary["errors"].append(f"facebook: {e}")

    elapsed = (datetime.now() - start).total_seconds()
    summary["completed_at"] = datetime.now().isoformat()
    summary["duration_seconds"] = round(elapsed, 1)
    logger.info("=== Scan completed in %.1f seconds ===", elapsed)
    _write_scan_summary(summary)


def show_stats():
    """Display database statistics."""
    stats = get_stats()
    print(f"Total posts seen:     {stats['total_seen']}")
    print(f"Total notified:       {stats['total_notified']}")


def main():
    parser = argparse.ArgumentParser(description="Tel Aviv Apartment Monitor")
    parser.add_argument("--dry-run", action="store_true", help="Scan without sending Slack notifications")
    parser.add_argument(
        "--bootstrap",
        action="store_true",
        help="One-time: scrape and mark every result as seen without notifying. "
             "Skips per-listing coordinate fetches for speed.",
    )
    parser.add_argument("--stats", action="store_true", help="Show database statistics")
    args = parser.parse_args()

    if args.stats:
        show_stats()
    else:
        run_scan(dry_run=args.dry_run, bootstrap=args.bootstrap)


if __name__ == "__main__":
    main()

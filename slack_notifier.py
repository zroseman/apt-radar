"""
Send matching apartment listings to Slack via incoming webhook.
"""

import json
import logging
import ssl
import urllib.request
import urllib.error

import certifi

from config import SLACK_WEBHOOK_URL

# Build an SSL context using certifi's CA bundle (fixes macOS Python SSL issues)
_SSL_CTX = ssl.create_default_context(cafile=certifi.where())
from post_parser import ApartmentListing

logger = logging.getLogger(__name__)


def _format_listing(listing: ApartmentListing) -> dict:
    """Format a listing as a Slack message block."""
    # Build the summary line
    parts = []
    if listing.price is not None:
        parts.append(f"*{listing.price:,}₪/mo*")
    if listing.rooms is not None:
        parts.append(f"{listing.rooms} rooms")
    if listing.sqm is not None:
        parts.append(f"{listing.sqm}m²")
    if listing.bathrooms is not None:
        parts.append(f"{listing.bathrooms} bath")
    if listing.location_name:
        parts.append(f"📍 {listing.location_name}")

    summary = " | ".join(parts) if parts else "Details in post"

    # Highlights
    highlight_text = "\n".join(f"• {h}" for h in listing.highlights) if listing.highlights else ""

    # Truncate raw text for preview
    preview = listing.raw_text[:500].replace("\n", " ")
    if len(listing.raw_text) > 500:
        preview += "..."

    # Build Slack blocks
    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": "🏠 New Apartment Match",
            },
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": summary,
            },
        },
    ]

    if highlight_text:
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": highlight_text,
            },
        })

    blocks.append({
        "type": "section",
        "text": {
            "type": "mrkdwn",
            "text": f"```{preview}```",
        },
    })

    if listing.permalink:
        if "yad2.co.il" in listing.permalink or listing.group_url == "yad2":
            link_label = "View on Yad2"
        else:
            link_label = "View Post on Facebook"
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"<{listing.permalink}|{link_label}>",
            },
        })

    blocks.append({"type": "divider"})

    return {"blocks": blocks}


def send_listing(listing: ApartmentListing) -> bool:
    """Send a single listing to Slack. Returns True on success."""
    if not SLACK_WEBHOOK_URL:
        logger.error(
            "SLACK_WEBHOOK_URL not configured. "
            "Set APT_RADAR_SLACK_WEBHOOK in your .env (or skip Slack — listings show in the dashboard)."
        )
        return False

    payload = _format_listing(listing)

    try:
        req = urllib.request.Request(
            SLACK_WEBHOOK_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10, context=_SSL_CTX) as resp:
            if resp.status == 200:
                logger.info("Sent Slack notification for %s", listing.post_id[:50])
                return True
            else:
                logger.error("Slack returned status %d", resp.status)
                return False
    except urllib.error.URLError as e:
        logger.error("Failed to send Slack notification: %s", e)
        return False


def send_listings(listings: list[ApartmentListing]) -> int:
    """Send multiple listings to Slack. Returns count of successfully sent."""
    sent = 0
    for listing in listings:
        if send_listing(listing):
            sent += 1
    logger.info("Sent %d/%d listings to Slack", sent, len(listings))
    return sent


# --- Operational alerts ---
# Module-level dedup so the same alert isn't sent twice in one scan
# (e.g., when both Yad2 URLs hit a captcha simultaneously). Reset by
# monitor.py at the start of each scan via reset_alerts().
_alerts_sent_this_run: set[str] = set()


def reset_alerts() -> None:
    """Clear the per-run alert dedup set. Call at the start of each scan."""
    _alerts_sent_this_run.clear()


def send_alert(key: str, title: str, body: str) -> bool:
    """
    Send an operational alert to Slack (not a listing — a system warning).
    `key` is used to dedup within a single run.
    """
    if key in _alerts_sent_this_run:
        logger.debug("Skipping duplicate alert: %s", key)
        return False
    _alerts_sent_this_run.add(key)

    if not SLACK_WEBHOOK_URL:
        logger.error("Cannot send alert (%s): SLACK_WEBHOOK_URL not configured", key)
        return False

    payload = {
        "blocks": [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": f"⚠️ {title}"},
            },
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": body},
            },
            {"type": "divider"},
        ]
    }

    try:
        req = urllib.request.Request(
            SLACK_WEBHOOK_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10, context=_SSL_CTX) as resp:
            if resp.status == 200:
                logger.info("Sent Slack alert: %s", key)
                return True
            logger.error("Slack alert returned status %d", resp.status)
            return False
    except urllib.error.URLError as e:
        logger.error("Failed to send Slack alert: %s", e)
        return False

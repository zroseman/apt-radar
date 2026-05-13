"""
Yad2 (yad2.co.il) scraper.

Reuses the Chrome/AppleScript infrastructure from chrome_scraper to load
Yad2 search-result pages in the user's logged-in real Chrome (which is
the most reliable way past Yad2's bot protection).

Each listing card is extracted from the rendered DOM, then a regex parser
pulls out price / rooms / sqm / location from the card text. Claude
classification is intentionally skipped — Yad2 returns structured data and
the search URLs already pre-filter by price, rooms, and bounding box.
"""

import json
import logging
import re
import time

from chrome_scraper import (
    _close_tab,
    _exec_js,
    _open_tab,
    _wait_for_load,
)
from config import CRITERIA, INCLUSION_POLYGON
from geo_filter import is_in_target_area
from post_parser import ApartmentListing
from settings import bbox_from_yad2_url
from slack_notifier import send_alert

logger = logging.getLogger(__name__)


# JS that walks the rendered Yad2 search-results DOM and returns one entry
# per listing card. For each /item/ anchor we walk up parents until we find
# the smallest ancestor that contains both ₪ and חדרים — that's the card
# boundary. We cap card text at 600 chars to ensure we never accidentally
# capture a parent that holds multiple cards.
# JS that extracts {lat, lon} from a Yad2 listing detail page. Coordinates
# live in __NEXT_DATA__ at a deep path like
# props.pageProps.dehydratedState.queries[].state.data.address.coords —
# rather than hard-code that path, we walk the JSON for any object shaped
# like {lat: number, lon: number}.
# Walks the search-results page __NEXT_DATA__ for every listing and returns
# a token -> {lat, lon} map. This lets us skip per-listing detail fetches
# entirely (which were both slow and risky — Yad2 anti-bot kicks in after a
# handful of hits).
EXTRACT_YAD2_COORDS_MAP_JS = r"""
(function() {
    var s = document.getElementById('__NEXT_DATA__');
    if (!s) return JSON.stringify({error: 'no __NEXT_DATA__', map: {}});
    var data;
    try { data = JSON.parse(s.textContent); }
    catch(e) { return JSON.stringify({error: 'parse failed', map: {}}); }

    var out = {};
    function walk(obj, depth) {
        if (!obj || typeof obj !== 'object' || depth > 30) return;
        if (Array.isArray(obj)) {
            for (var i = 0; i < obj.length; i++) walk(obj[i], depth + 1);
            return;
        }
        // Listing-shaped object: has a token + address with coords
        if (obj.token && obj.address && obj.address.coords) {
            var c = obj.address.coords;
            if (typeof c.lat === 'number' && typeof c.lon === 'number') {
                out[obj.token] = {lat: c.lat, lon: c.lon};
            }
        }
        for (var k in obj) {
            if (Object.prototype.hasOwnProperty.call(obj, k)) walk(obj[k], depth + 1);
        }
    }
    walk(data, 0);
    return JSON.stringify({map: out, count: Object.keys(out).length});
})();
"""


EXTRACT_YAD2_CARDS_JS = r"""
(function() {
    var listings = [];
    var seen = {};

    var anchors = document.querySelectorAll('a[href*="/item/"]');
    for (var i = 0; i < anchors.length; i++) {
        var a = anchors[i];
        var href = (a.getAttribute('href') || '').split('?')[0];
        if (!href || href.indexOf('/item/') === -1) continue;

        // Token = last non-empty path segment (handles both /item/TOKEN
        // and /item/area-name/TOKEN URL shapes)
        var parts = href.split('/').filter(function(s) { return s.length > 0; });
        var token = parts[parts.length - 1];
        if (!token || token.length < 4) continue;
        if (seen[token]) continue;

        // Walk up to find the smallest ancestor that contains both ₪ and חדרים
        var card = a.parentElement;
        var found = null;
        for (var j = 0; j < 10 && card; j++) {
            var t = card.innerText || '';
            if (t.length < 600 && t.indexOf('₪') !== -1 && t.indexOf('חדרים') !== -1) {
                found = card;
                break;
            }
            card = card.parentElement;
        }
        if (!found) continue;

        var text = found.innerText.trim();
        if (text.length < 10) continue;

        seen[token] = true;
        var url = href.startsWith('http') ? href : 'https://www.yad2.co.il' + href;

        listings.push({
            id: 'yad2_' + token,
            token: token,
            url: url,
            text: text.substring(0, 600)
        });
    }

    // Only flag captcha when content is MISSING and a real challenge is
    // blocking the page. PerimeterX embeds a hidden captcha iframe on every
    // Yad2 page as a monitoring sentinel — its presence alone doesn't mean
    // the user is being challenged.
    var captcha = false;
    if (listings.length === 0) {
        var bodyText = (document.body.innerText || '').toLowerCase();
        captcha = bodyText.indexOf('are you a human') !== -1
            || bodyText.indexOf('press & hold') !== -1
            || bodyText.indexOf('perimeterx') !== -1;
    }

    return JSON.stringify({listings: listings, captcha: captcha});
})();
"""


# --- Regex parsers ---

# Price: handles "18,500 ₪", "₪18,500", "18500 ש"ח", "18,500 NIS"
_PRICE_RE_TRAILING = re.compile(
    r"(\d{1,3}(?:[,\u066c]\d{3})+|\d{4,6})\s*(?:₪|ש[\"'״]?ח|NIS)",
    re.IGNORECASE,
)
_PRICE_RE_LEADING = re.compile(
    r"(?:₪|ש[\"'״]?ח|NIS)\s*(\d{1,3}(?:[,\u066c]\d{3})+|\d{4,6})",
    re.IGNORECASE,
)

# Rooms: "5 חדרים", "4.5 חד'", "5 rooms"
_ROOMS_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(?:חדרים|חד[\"'״]?|rooms?)",
    re.IGNORECASE,
)

# Sqm: '120 מ"ר', "120 מטר", "120 sqm", "120 m²"
_SQM_RE = re.compile(
    r"(\d{2,4})\s*(?:מ[\"'״]?ר|מטר|sqm|m²)",
    re.IGNORECASE,
)


def parse_yad2_card(text: str) -> dict:
    """Extract structured fields from a Yad2 card's rendered text.

    Yad2 main-results cards have a consistent structure:
        ₪ 25,000
        ירמיהו                                    ← street
        גג/ פנטהאוז, הצפון הישן - צפון, תל אביב יפו ← type + neighborhood
        5 חדרים • קומה 6 • 210 מ״ר
    """
    out = {
        "price": None,
        "rooms": None,
        "sqm": None,
        "location_name": None,
        "is_recommendation": False,
        "_price_str": None,  # raw matched string, used to anchor location parsing
    }

    # "Similar properties" recommendation cards include "מחוז תל אביב"
    # (Tel Aviv district) — main results don't. Mark these so we can drop them.
    if "מחוז תל אביב" in text or "נכסים דומים" in text:
        out["is_recommendation"] = True

    # Collect ALL price candidates and take the largest. This handles cases
    # like "ירד ב-3,000 ₪ ... ₪ 17,000" where a price-drop banner precedes
    # the actual price.
    price_candidates = []
    for m in _PRICE_RE_TRAILING.finditer(text):
        try:
            price_candidates.append((int(re.sub(r"[,\u066c]", "", m.group(1))), m.group(0)))
        except ValueError:
            pass
    for m in _PRICE_RE_LEADING.finditer(text):
        try:
            price_candidates.append((int(re.sub(r"[,\u066c]", "", m.group(1))), m.group(0)))
        except ValueError:
            pass
    if price_candidates:
        price_candidates.sort(key=lambda p: p[0], reverse=True)
        out["price"] = price_candidates[0][0]
        out["_price_str"] = price_candidates[0][1]

    rooms_match = _ROOMS_RE.search(text)
    if rooms_match:
        try:
            out["rooms"] = float(rooms_match.group(1))
        except ValueError:
            pass

    sqm_match = _SQM_RE.search(text)
    if sqm_match:
        try:
            out["sqm"] = int(sqm_match.group(1))
        except ValueError:
            pass

    # Location: prefer the line immediately after the price line, then
    # combine with the next line if it adds neighborhood info. Anchor on
    # the line containing the chosen price (not just the first ₪) so
    # discount banners don't throw off the offset.
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    price_idx = None
    if out["_price_str"]:
        for idx, line in enumerate(lines):
            if out["_price_str"] in line:
                price_idx = idx
                break
    if price_idx is None:
        for idx, line in enumerate(lines):
            if "₪" in line:
                price_idx = idx
                break

    if price_idx is not None:
        candidate_lines = []
        for offset in (1, 2):
            j = price_idx + offset
            if j >= len(lines):
                break
            line = lines[j]
            # Skip the rooms/floor/sqm summary line
            if _ROOMS_RE.search(line) or "קומה" in line or "מ״ר" in line or 'מ"ר' in line:
                break
            if not re.search(r"[\u0590-\u05FF]", line):
                continue
            candidate_lines.append(line)
        if candidate_lines:
            out["location_name"] = ", ".join(candidate_lines)[:120]

    out.pop("_price_str", None)
    return out


def scrape_yad2_search(url: str) -> list[dict]:
    """Open a Yad2 search URL in real Chrome and extract listing cards."""
    tab = _open_tab(url)
    if not tab or tab.get("client") is None:
        logger.error("Yad2: failed to open CDP tab for %s", url)
        return []

    logger.info("Yad2: opening %s in CDP tab %s", url, tab.get("target_id"))

    try:
        # Yad2 is a SPA — give it longer than the FB delay to hydrate
        time.sleep(8)
        _wait_for_load(tab, timeout=20)
        time.sleep(2)

        raw = _exec_js(tab, EXTRACT_YAD2_CARDS_JS)
        if not raw:
            logger.warning("Yad2: empty JS result for %s", url)
            return []

        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("Yad2: failed to parse JS result for %s", url)
            return []

        if payload.get("captcha"):
            logger.error(
                "Yad2: captcha / bot challenge detected on %s — solve it manually in Chrome",
                url,
            )
            send_alert(
                key="yad2_captcha",
                title="Yad2 captcha — manual action needed",
                body=(
                    "Yad2 is showing a bot challenge. Open Chrome, navigate to "
                    "any Yad2 page, solve the captcha, then the next scheduled "
                    f"scan will recover automatically.\n\nURL: <{url}|search page>"
                ),
            )
            return []

        listings = payload.get("listings", [])
        if not listings:
            logger.warning(
                "Yad2: 0 cards extracted from %s — DOM selectors may be stale",
                url,
            )
            send_alert(
                key="yad2_no_cards",
                title="Yad2 returned 0 cards",
                body=(
                    "A Yad2 search page returned zero listings. This usually means "
                    "either there are genuinely no matches *or* Yad2 changed their "
                    "DOM and `yad2_scraper.py` needs the selectors updated.\n\n"
                    f"URL: <{url}|search page>"
                ),
            )
            return listings

        logger.info("Yad2: extracted %d cards from %s", len(listings), url)

        # Pull coords for every listing from __NEXT_DATA__ on the same page —
        # much faster (and safer for anti-bot) than opening each detail page.
        coords_raw = _exec_js(tab, EXTRACT_YAD2_COORDS_MAP_JS)
        coords_map: dict[str, dict] = {}
        if coords_raw:
            try:
                coords_payload = json.loads(coords_raw)
                coords_map = coords_payload.get("map", {}) or {}
            except json.JSONDecodeError:
                logger.warning("Yad2: failed to parse __NEXT_DATA__ coords for %s", url)
        logger.info(
            "Yad2: coords from __NEXT_DATA__ for %d of %d listings",
            sum(1 for l in listings if l.get("token") in coords_map),
            len(listings),
        )

        # Attach lat/lon to each card so yad2_cards_to_listings doesn't have
        # to fetch detail pages. Also tag each card with the bBox extracted
        # from the source URL — used as an automatic geographic filter when
        # the user hasn't drawn a custom polygon.
        source_bbox = bbox_from_yad2_url(url)
        if source_bbox:
            logger.info(
                "Yad2: source bBox = lat[%.4f, %.4f] lng[%.4f, %.4f]",
                source_bbox[0], source_bbox[2], source_bbox[1], source_bbox[3],
            )
        else:
            logger.info("Yad2: no bBox in URL %s — no auto geographic filter", url)
        for l in listings:
            tok = l.get("token")
            c = coords_map.get(tok) if tok else None
            if c:
                l["lat"] = c.get("lat")
                l["lon"] = c.get("lon")
            if source_bbox:
                l["source_bbox"] = source_bbox

        return listings

    finally:
        _close_tab(tab)


def fetch_listing_coords(url: str) -> tuple[float, float] | None:
    """
    Open a single Yad2 listing detail page in real Chrome and extract its
    (lat, lon) from __NEXT_DATA__. Returns None if extraction fails.
    """
    tab = _open_tab(url)
    if not tab or tab.get("client") is None:
        logger.error("Yad2 coords: failed to open CDP tab for %s", url)
        return None

    try:
        time.sleep(6)
        _wait_for_load(tab, timeout=15)
        time.sleep(1)

        raw = _exec_js(tab, EXTRACT_YAD2_COORDS_JS)
        if not raw:
            logger.warning("Yad2 coords: empty JS result for %s", url)
            return None
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("Yad2 coords: JSON parse failed for %s", url)
            return None
        if "error" in payload:
            logger.warning("Yad2 coords: %s for %s", payload["error"], url)
            return None
        return (float(payload["lat"]), float(payload["lon"]))
    finally:
        _close_tab(tab)


def scrape_yad2_searches(urls: list[str]) -> list[dict]:
    """Scrape multiple Yad2 search URLs and return a deduplicated list of cards."""
    by_token: dict[str, dict] = {}
    for url in urls:
        try:
            cards = scrape_yad2_search(url)
            for c in cards:
                token = c.get("token")
                if token and token not in by_token:
                    by_token[token] = c
        except Exception:
            logger.exception("Yad2: error scraping %s", url)
        time.sleep(2)
    logger.info("Yad2: %d unique cards across %d search URLs", len(by_token), len(urls))
    return list(by_token.values())


def yad2_cards_to_listings(
    cards: list[dict],
    fetch_coords: bool = True,
) -> list[ApartmentListing]:
    """
    Convert raw Yad2 cards into ApartmentListing objects, applying:
      1. Recommendations filter ("similar properties" cards are dropped)
      2. Price/rooms hard filters (defensive — URL should pre-filter)
      3. Polygon filter using coords embedded in each card (from __NEXT_DATA__)

    Coords now come inline with the card (added by scrape_yad2_search via
    __NEXT_DATA__) — no more per-listing detail page fetches. Setting
    fetch_coords=False skips the polygon check — used in --bootstrap mode
    where we just want to mark everything as seen quickly.
    """
    results: list[ApartmentListing] = []
    missing_coords = 0

    for card in cards:
        text = card.get("text", "")
        parsed = parse_yad2_card(text)

        if parsed.get("is_recommendation"):
            logger.debug(
                "Yad2: skipping recommendation card (outside searched bBox): %s",
                card.get("token"),
            )
            continue

        listing = ApartmentListing(
            post_id=card.get("id", ""),
            raw_text=text,
            permalink=card.get("url", ""),
            group_url="yad2",
            price=parsed["price"],
            rooms=parsed["rooms"],
            sqm=parsed["sqm"],
            bathrooms=None,
            location_name=parsed["location_name"],
            in_target_area=True,
            has_mamad=None,
            has_parking=None,
            is_sublet=False,
            summary="",
        )

        # Defensive filters — should be no-ops because the URL pre-filtered,
        # but Yad2 occasionally returns "promoted" cards outside the filter.
        if listing.price is None:
            logger.debug("Yad2: skipping card with no parseable price: %s", card.get("token"))
            continue
        if listing.price > CRITERIA["max_rent"] or listing.price < CRITERIA["min_rent"]:
            logger.debug(
                "Yad2: skipping out-of-range price %s for %s",
                listing.price, card.get("token"),
            )
            continue
        if listing.rooms is not None and listing.rooms < CRITERIA["min_rooms"]:
            logger.debug(
                "Yad2: skipping under-rooms %s for %s",
                listing.rooms, card.get("token"),
            )
            continue

        # Geographic filter using inline coords from __NEXT_DATA__.
        # Priority:
        #   1. User polygon (strict, custom shape) if set in settings
        #   2. bBox from the user's Yad2 URL (auto-derived rectangle)
        #   3. No filter (everything passes)
        if fetch_coords:
            lat = card.get("lat")
            lon = card.get("lon")
            if lat is None or lon is None:
                missing_coords += 1
                logger.warning(
                    "Yad2: dropping %s — no coords in search-results __NEXT_DATA__",
                    card.get("token"),
                )
                continue

            if INCLUSION_POLYGON:
                # User has a custom polygon — that's the filter.
                if not is_in_target_area(lat, lon):
                    logger.info(
                        "Yad2: dropping %s — outside polygon (%.6f, %.6f)",
                        card.get("token"), lat, lon,
                    )
                    continue
                logger.debug("Yad2: %s passed polygon (%.6f, %.6f)",
                             card.get("token"), lat, lon)
            else:
                # Fall back to the bBox from the source URL.
                source_bbox = card.get("source_bbox")
                if source_bbox:
                    min_lat, min_lng, max_lat, max_lng = source_bbox
                    if not (min_lat <= lat <= max_lat and min_lng <= lon <= max_lng):
                        logger.info(
                            "Yad2: dropping %s — outside source-URL bBox (%.6f, %.6f)",
                            card.get("token"), lat, lon,
                        )
                        continue
                    logger.debug("Yad2: %s passed source bBox (%.6f, %.6f)",
                                 card.get("token"), lat, lon)
                else:
                    # No polygon, no source bBox — surface everything that
                    # passed the price/rooms filters.
                    logger.debug("Yad2: %s — no geo filter applied (%.6f, %.6f)",
                                 card.get("token"), lat, lon)

        # Highlights
        if listing.price <= CRITERIA["ideal_max_rent"]:
            listing.highlights.append(f"Within ideal budget ({listing.price:,}₪)")
        else:
            listing.highlights.append(
                f"Above ideal ({listing.price:,}₪ vs {CRITERIA['ideal_max_rent']:,}₪) — negotiate?"
            )
        if listing.location_name:
            listing.highlights.append(f"Location: {listing.location_name}")
        listing.highlights.append("Source: Yad2")

        listing.passes_filter = True
        results.append(listing)

    logger.info("Yad2: %d cards → %d listings after filter", len(cards), len(results))

    if missing_coords >= 3 and missing_coords * 2 >= len(cards):
        send_alert(
            key="yad2_coord_failures",
            title="Yad2 coords missing from search results",
            body=(
                f"{missing_coords}/{len(cards)} listings had no lat/lon in the "
                "search-results __NEXT_DATA__. Yad2 may have changed the page "
                "structure — `EXTRACT_YAD2_COORDS_MAP_JS` in `yad2_scraper.py` "
                "may need updating."
            ),
        )

    return results

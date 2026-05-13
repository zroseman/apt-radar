"""
Parse Facebook group posts for apartment listing details (Hebrew + English).
Uses an LLM (Claude or OpenAI) to understand Hebrew text and classify posts.

Provider selection (in priority order):
  1. If ANTHROPIC_API_KEY is set → use Claude (default; tested path)
  2. Else if OPENAI_API_KEY is set → use OpenAI gpt-4o-mini
  3. Else → raise on first parse attempt
"""

import json
import logging
import os
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


def _llm_provider() -> str:
    """Return 'anthropic' or 'openai' depending on which key is set."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "anthropic"
    if os.environ.get("OPENAI_API_KEY"):
        return "openai"
    raise RuntimeError(
        "No LLM API key set. Add ANTHROPIC_API_KEY or OPENAI_API_KEY to .env. "
        "Anthropic is recommended (it's what's been tested)."
    )


# Lazily-constructed clients (only the chosen provider's client is instantiated).
_anthropic_client = None
_openai_client = None


def _get_anthropic_client():
    global _anthropic_client
    if _anthropic_client is None:
        import anthropic
        _anthropic_client = anthropic.Anthropic()
    return _anthropic_client


def _get_openai_client():
    global _openai_client
    if _openai_client is None:
        from openai import OpenAI
        _openai_client = OpenAI()
    return _openai_client

def _build_classification_prompt() -> str:
    """Build the classifier prompt, injecting the user's target-area description.

    Loaded lazily on each call so settings changes are picked up without a
    process restart. Not cached intentionally — the call is cheap and FB
    scans are infrequent."""
    from config import TARGET_AREA_DESCRIPTION
    target_area = (TARGET_AREA_DESCRIPTION or "").strip()
    if not target_area:
        target_area = "(No target area configured. Set in_target_area to null and let downstream filters decide.)"
    return f"""You are classifying a Facebook group post about apartments.
Read the post carefully (it may be in Hebrew, English, or both) and extract structured data.

IMPORTANT RULES:
1. If the post is someone LOOKING FOR / SEARCHING FOR an apartment (demand), set post_type to "demand".
   Hebrew cues: מחפש/מחפשת/מחפשים דירה/דירת, דרושה דירה, מישהו מכיר
2. If the post is OFFERING an apartment for rent (supply), set post_type to "supply".
3. If it's a commercial/office space listing, set post_type to "commercial".
4. If it's about a room in a shared apartment (roommate search), set post_type to "room_rental".

For LOCATION, decide whether the post's location matches the user's target area:

--- USER'S TARGET AREA ---
{target_area}
--- END TARGET AREA ---

If the post mentions a specific street, neighborhood, or address, match it against the area description above.
If the post's location is unclear or unmentioned, set in_target_area to null (so it can be reviewed manually).

For PRICE, look for:
- Numbers near ₪, ש"ח, שח, שקל, NIS
- "X אלף" means X thousand (e.g., "22 אלף" = 22,000)
- Distinguish monthly rent from sale price (sale prices are in millions)

Respond with ONLY a JSON object (no markdown, no explanation):
{{
  "post_type": "supply" | "demand" | "commercial" | "room_rental" | "other",
  "price_nis": <number or null>,
  "rooms": <number or null>,
  "sqm": <number or null>,
  "bathrooms": <number or null>,
  "location_name": "<neighborhood or street>" or null,
  "in_target_area": true | false | null,
  "has_mamad": true | false | null,
  "has_parking": true | false | null,
  "is_sublet": true | false,
  "rejection_reason": "<brief reason if this clearly doesn't match>" or null,
  "summary": "<1-line Hebrew or English summary of what this post is about>"
}}
"""


@dataclass
class ApartmentListing:
    """Parsed apartment listing from a Facebook post."""
    post_id: str
    raw_text: str
    permalink: str
    group_url: str

    # Parsed fields (None = not found in post)
    price: int | None = None
    rooms: float | None = None
    sqm: int | None = None
    bathrooms: float | None = None
    location_name: str | None = None
    in_target_area: bool | None = None
    has_mamad: bool | None = None
    has_parking: bool | None = None
    is_sublet: bool = False
    summary: str = ""

    # Filter result
    passes_filter: bool = False
    rejection_reason: str | None = None
    highlights: list[str] = field(default_factory=list)


def classify_post(text: str) -> dict | None:
    """Use the configured LLM to classify and extract data from a post."""
    prompt = f"{_build_classification_prompt()}\n\nPOST TEXT:\n{text[:2000]}"
    try:
        provider = _llm_provider()
        if provider == "anthropic":
            model = os.environ.get("APT_RADAR_ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")
            response = _get_anthropic_client().messages.create(
                model=model,
                max_tokens=500,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = response.content[0].text.strip()
        else:  # openai
            model = os.environ.get("APT_RADAR_OPENAI_MODEL", "gpt-4o-mini")
            response = _get_openai_client().chat.completions.create(
                model=model,
                max_tokens=500,
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
            )
            raw = response.choices[0].message.content.strip()
        # Strip markdown code fences if the model wrapped output (defensive).
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        return json.loads(raw)
    except Exception:
        logger.exception("LLM classification failed")
        return None


def parse_and_filter_posts(posts: list[dict]) -> list[ApartmentListing]:
    """
    Classify all posts via Claude API, then filter against criteria.
    Returns only listings that match.
    """
    from config import CRITERIA

    results = []

    for post in posts:
        text = post.get("text", "")
        post_id = post.get("id", "")

        if len(text) < 30:
            continue

        # Classify via Claude API
        data = classify_post(text)
        if data is None:
            logger.warning("Failed to classify post %s, skipping", post_id[:50])
            continue

        post_type = data.get("post_type", "other")

        # Skip non-supply posts
        if post_type != "supply":
            logger.debug("Skipping %s post: %s", post_type, post_id[:50])
            continue

        listing = ApartmentListing(
            post_id=post_id,
            raw_text=text,
            permalink=post.get("permalink", ""),
            group_url=post.get("group_url", ""),
            price=data.get("price_nis"),
            rooms=data.get("rooms"),
            sqm=data.get("sqm"),
            bathrooms=data.get("bathrooms"),
            location_name=data.get("location_name"),
            in_target_area=data.get("in_target_area"),
            has_mamad=data.get("has_mamad"),
            has_parking=data.get("has_parking"),
            is_sublet=data.get("is_sublet", False),
            summary=data.get("summary", ""),
        )

        # --- Apply hard filters ---

        # Must have price, rooms, AND confirmed location to be actionable
        if listing.price is None:
            listing.rejection_reason = "Price not found — not actionable"
            logger.debug("Filtered out (no price): %s", post_id[:50])
            continue
        if listing.rooms is None:
            listing.rejection_reason = "Room count not found — not actionable"
            logger.debug("Filtered out (no rooms): %s", post_id[:50])
            continue
        if listing.in_target_area is not True:
            listing.rejection_reason = f"Location not confirmed in target area: {listing.location_name}"
            logger.debug("Filtered out (location): %s — %s", post_id[:50], listing.rejection_reason)
            continue

        # Price
        if listing.price > CRITERIA["max_rent"]:
            listing.rejection_reason = f"Price {listing.price:,}₪ exceeds max {CRITERIA['max_rent']:,}₪"
            logger.debug("Filtered out (price): %s — %s", post_id[:50], listing.rejection_reason)
            continue
        if listing.price < CRITERIA["min_rent"]:
            listing.rejection_reason = f"Price {listing.price:,}₪ below min {CRITERIA['min_rent']:,}₪ (likely room/scam/mislabeled)"
            logger.debug("Filtered out (min price): %s — %s", post_id[:50], listing.rejection_reason)
            continue
        if listing.price <= CRITERIA["ideal_max_rent"]:
            listing.highlights.append(f"Within ideal budget ({listing.price:,}₪)")
        else:
            listing.highlights.append(
                f"Above ideal ({listing.price:,}₪ vs {CRITERIA['ideal_max_rent']:,}₪) — negotiate?"
            )

        # Rooms
        if listing.rooms < CRITERIA["min_rooms"]:
            listing.rejection_reason = f"{listing.rooms} rooms, need {CRITERIA['min_rooms']}+"
            logger.debug("Filtered out (rooms): %s — %s", post_id[:50], listing.rejection_reason)
            continue

        # Square meters (only exclude if explicitly below minimum)
        if listing.sqm is not None:
            if listing.sqm < CRITERIA["min_sqm"]:
                listing.rejection_reason = f"{listing.sqm}sqm below minimum {CRITERIA['min_sqm']}sqm"
                logger.debug("Filtered out (sqm): %s — %s", post_id[:50], listing.rejection_reason)
                continue

        # Bathrooms (only exclude if explicitly below minimum)
        if listing.bathrooms is not None:
            if listing.bathrooms < CRITERIA["min_bathrooms"]:
                listing.rejection_reason = f"{listing.bathrooms} bathrooms, need {CRITERIA['min_bathrooms']}+"
                logger.debug("Filtered out (baths): %s — %s", post_id[:50], listing.rejection_reason)
                continue

        # --- Highlights ---
        if listing.location_name:
            listing.highlights.append(f"Location: {listing.location_name}")
        if listing.has_mamad:
            listing.highlights.append("Has mamad")
        if listing.has_parking:
            listing.highlights.append("Has parking")
        if listing.is_sublet:
            listing.highlights.append("Sublet — confirm lease is extendable")
        if listing.summary:
            listing.highlights.append(f"Summary: {listing.summary}")

        listing.passes_filter = True
        results.append(listing)

    logger.info("Parsed %d posts, %d passed filter", len(posts), len(results))
    return results

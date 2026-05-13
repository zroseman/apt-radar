"""
Chrome scraper using Chrome DevTools Protocol (CDP) on port 9222.

Connects to the existing Chrome debug browser (launched with
--remote-debugging-port=9222) and opens every scraping tab in the
BACKGROUND via Target.createTarget with background=true, so the scraper
never foregrounds Chrome and interrupts what the user is doing.

PREREQUISITES:
  1. Chrome running with --remote-debugging-port=9222
     (e.g. `chrome-debug` alias from ~/.zshrc)
  2. That Chrome is logged into Facebook (and Yad2 captcha solved once
     if challenged)
"""

import json
import logging
import os
import re
import time
from typing import Optional

import requests

try:
    from websocket import create_connection  # websocket-client
except ImportError as e:
    raise ImportError(
        "websocket-client not installed. Run: pip install websocket-client"
    ) from e

from config import (
    PAGE_LOAD_DELAY,
    SCROLL_COUNT,
    SCROLL_DELAY,
)

logger = logging.getLogger(__name__)

CDP_BASE = "http://127.0.0.1:9222"


class CDPClient:
    """Minimal CDP client bound to one WebSocket endpoint (browser or tab)."""

    def __init__(self, ws_url: str, timeout: int = 30):
        self.ws = create_connection(ws_url, timeout=timeout)
        self._msg_id = 0

    def send(self, method: str, params: Optional[dict] = None, timeout: int = 30) -> dict:
        self._msg_id += 1
        msg_id = self._msg_id
        payload = {"id": msg_id, "method": method, "params": params or {}}
        self.ws.send(json.dumps(payload))

        deadline = time.time() + timeout
        while time.time() < deadline:
            self.ws.settimeout(max(0.5, deadline - time.time()))
            try:
                raw = self.ws.recv()
            except Exception:
                continue
            if not raw:
                continue
            data = json.loads(raw)
            if data.get("id") == msg_id:
                if "error" in data:
                    raise RuntimeError(f"CDP error: {data['error']}")
                return data.get("result", {})
        raise TimeoutError(f"CDP timeout waiting for {method}")

    def eval(self, js: str, await_promise: bool = False, timeout: int = 30) -> dict:
        return self.send(
            "Runtime.evaluate",
            {
                "expression": js,
                "returnByValue": True,
                "awaitPromise": await_promise,
            },
            timeout=timeout,
        )

    def close(self):
        try:
            self.ws.close()
        except Exception:
            pass


def _get_browser_ws() -> str:
    """Browser-level WebSocket URL, needed for Target.createTarget."""
    r = requests.get(f"{CDP_BASE}/json/version", timeout=3)
    r.raise_for_status()
    return r.json()["webSocketDebuggerUrl"]


def _open_tab(url: str, force_background: bool = False) -> dict:
    """
    Open a new tab. Returns {target_id, ws_url, client} or {} on failure.

    By default, foreground vs background is controlled by the
    TLV_APT_FOREGROUND env var (FB requires foreground because its feed
    virtualizes off-screen posts). Pass force_background=True from scrapers
    that can read SSR-rendered data without the tab being visible (Yad2 is
    the main one) — that way Yad2 scans don't disrupt the user's screen
    even when FB is enabled.
    """
    try:
        browser_ws = _get_browser_ws()
    except Exception:
        logger.exception(
            "CDP: cannot reach Chrome on %s — is it running with "
            "--remote-debugging-port=9222?",
            CDP_BASE,
        )
        return {}

    if force_background:
        background = True
    else:
        # TLV_APT_FOREGROUND=1 forces foreground (set by app.py and the
        # scheduled launchd job). Otherwise default to background.
        background = os.environ.get("TLV_APT_FOREGROUND") != "1"
    browser = CDPClient(browser_ws)
    try:
        result = browser.send(
            "Target.createTarget",
            {"url": url, "background": background},
        )
        target_id = result["targetId"]
    finally:
        browser.close()

    # Poll briefly for the new target to show up in /json
    ws_url = None
    for _ in range(10):
        try:
            r = requests.get(f"{CDP_BASE}/json", timeout=5)
            r.raise_for_status()
            for target in r.json():
                if target.get("id") == target_id:
                    ws_url = target.get("webSocketDebuggerUrl")
                    break
            if ws_url:
                break
        except Exception:
            pass
        time.sleep(0.2)

    if not ws_url:
        ws_url = f"ws://127.0.0.1:9222/devtools/page/{target_id}"

    try:
        client = CDPClient(ws_url)
    except Exception:
        logger.exception("CDP: failed to attach to new tab %s", target_id)
        return {"target_id": target_id, "ws_url": ws_url, "client": None}

    return {"target_id": target_id, "ws_url": ws_url, "client": client}


def _close_tab(tab: dict):
    """Close the tab's WebSocket and then close the target itself."""
    if not tab:
        return
    client = tab.get("client")
    if client is not None:
        client.close()
    target_id = tab.get("target_id")
    if not target_id:
        return
    try:
        browser = CDPClient(_get_browser_ws())
        try:
            browser.send("Target.closeTarget", {"targetId": target_id})
        finally:
            browser.close()
    except Exception:
        logger.exception("CDP: failed to close tab %s", target_id)


def _exec_js(tab: dict, js: str) -> str:
    """Evaluate JS in the tab and return its result coerced to a string."""
    if not tab:
        return ""
    client = tab.get("client")
    if client is None:
        return ""
    try:
        result = client.eval(js)
        value = result.get("result", {}).get("value")
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        return str(value)
    except Exception:
        logger.exception("CDP: JS eval failed")
        return ""


def _wait_for_load(tab: dict, timeout: float = 30) -> bool:
    """Poll document.readyState until 'complete' or timeout."""
    start = time.time()
    while time.time() - start < timeout:
        status = _exec_js(tab, "document.readyState")
        if status == "complete":
            return True
        time.sleep(1)
    logger.warning("Page load timed out after %ss", timeout)
    return False


# JavaScript to extract currently-visible posts from a Facebook group page.
EXTRACT_VISIBLE_POSTS_JS = r"""
(function() {
    var posts = [];
    var seen = {};
    var feed = document.querySelector('[role="feed"]');
    if (!feed) return JSON.stringify([]);

    var children = feed.children;
    for (var i = 0; i < children.length; i++) {
        var child = children[i];

        var textEls = child.querySelectorAll('[dir="auto"]');
        var usedEls = new Set();
        var textParts = [];
        for (var j = 0; j < textEls.length; j++) {
            var el = textEls[j];
            var dominated = false;
            for (var k = 0; k < textEls.length; k++) {
                if (k !== j && usedEls.has(k) && textEls[k].contains(el)) {
                    dominated = true;
                    break;
                }
            }
            if (dominated) continue;

            var t = (el.innerText || el.textContent || '').trim();
            if (t.length < 5 || t === 'Facebook' || /^(Like|Comment|Share|Send|Save|See more|See translation)$/i.test(t)) continue;

            var dup = false;
            for (var m = 0; m < textParts.length; m++) {
                if (textParts[m].indexOf(t) !== -1 || t.indexOf(textParts[m]) !== -1) {
                    if (t.length > textParts[m].length) textParts[m] = t;
                    dup = true;
                    break;
                }
            }
            if (!dup) textParts.push(t);
            usedEls.add(j);
        }

        var text = textParts.join('\n');
        if (text.length < 30) continue;

        // Find links — prioritize real post permalinks over commerce/listing URLs
        var allLinks = child.querySelectorAll('a[href]');
        var postPermalink = '';   // actual group post link (works when opened)
        var commerceLink = '';     // commerce/listing link (for dedup ID only)

        for (var k = 0; k < allLinks.length; k++) {
            var href = allLinks[k].getAttribute('href') || '';
            if (href.includes('/posts/') || href.includes('/permalink/')) {
                postPermalink = href.split('?')[0];
                break;
            }
            if (!commerceLink && href.includes('/commerce/listing/')) {
                // Keep the full URL including query params — Facebook needs them to resolve the listing
                commerceLink = href;
            }
        }
        // Fallback: look for post ID in photo links (set=pcb.POST_ID)
        if (!postPermalink) {
            for (var k = 0; k < allLinks.length; k++) {
                var href = allLinks[k].getAttribute('href') || '';
                if (href.includes('/groups/') && href.includes('/posts/')) {
                    postPermalink = href.split('?')[0];
                    break;
                }
            }
        }
        if (!postPermalink) {
            // Photo URLs contain set=pcb.{postId} — extract the post ID
            var photoLinks = child.querySelectorAll('a[href*="set=pcb."]');
            if (photoLinks.length > 0) {
                var photoHref = photoLinks[0].getAttribute('href') || '';
                var pcbMatch = photoHref.match(/set=pcb\.(\d+)/);
                if (pcbMatch) {
                    // Extract group ID from the current page URL
                    var groupMatch = window.location.href.match(/\/groups\/([^\/\?]+)/);
                    if (groupMatch) {
                        postPermalink = '/groups/' + groupMatch[1] + '/posts/' + pcbMatch[1] + '/';
                    }
                }
            }
        }

        // Use commerce link as stable ID for dedup, but build a working permalink for the user
        // For dedup ID, use a clean version (no query params)
        var idSource = postPermalink || (commerceLink ? commerceLink.split('?')[0] : '');
        var key = idSource || text.substring(0, 150);
        if (seen[key]) continue;
        seen[key] = true;

        var postId = idSource || ('hash_' + text.substring(0, 100).replace(/[^a-zA-Z0-9\u0590-\u05FF]/g, '_'));

        // Build a working permalink:
        // 1. /posts/ or /permalink/ URL — use directly
        // 2. /commerce/listing/ URL with query params — use as-is (Facebook needs the params)
        // 3. Fall back to the group URL
        var permalink = postPermalink || commerceLink || window.location.href.split('?')[0];
        if (permalink && !permalink.startsWith('http')) {
            permalink = 'https://www.facebook.com' + permalink;
        }

        posts.push({
            id: postId,
            text: text.substring(0, 3000),
            permalink: permalink ? (permalink.startsWith('http') ? permalink : 'https://www.facebook.com' + permalink) : ''
        });
    }

    return JSON.stringify(posts);
})();
"""

EXPAND_SEE_MORE_JS = r"""
(function() {
    var expanded = 0;
    var allLinks = document.querySelectorAll('[role="feed"] [role="button"], [role="feed"] a');
    for (var i = 0; i < allLinks.length; i++) {
        var t = (allLinks[i].innerText || '').trim().toLowerCase();
        if (t === 'see more' || t === '...see more' || t === '\u05e8\u05d0\u05d4 \u05e2\u05d5\u05d3' || t === '\u05e7\u05e8\u05d0 \u05e2\u05d5\u05d3') {
            allLinks[i].click();
            expanded++;
        }
    }
    return expanded;
})();
"""


def _clean_post_text(text: str) -> str:
    """Remove Facebook UI artifacts from extracted post text."""
    lines = text.split("\n")
    cleaned = []
    for line in lines:
        line = line.strip()
        if re.match(r'^[a-zA-Z0-9]{20,}$', line.replace(" ", "")):
            continue
        if re.match(r'^[a-zA-Z0-9]{4,12}\.(com|net|org)$', line):
            continue
        if line in ("Message", "Send", "Save", "Share", "Hide"):
            continue
        if line:
            cleaned.append(line)
    return "\n".join(cleaned)


def scrape_group(
    group_url: str,
    seen_ids: set[str] | None = None,
    max_consecutive_zero_new_scrolls: int = 2,
) -> list[dict]:
    """
    Scrape recent posts from a Facebook group.
    Returns a list of dicts with keys: id, text, permalink, group_url.

    Short-circuit: if `seen_ids` is provided, the scroll loop stops early
    when `max_consecutive_zero_new_scrolls` scrolls in a row reveal only
    posts we've already processed. Saves time + avoids unnecessary FB load.
    """
    seen_ids = seen_ids or set()

    tab = _open_tab(group_url)
    if not tab or tab.get("client") is None:
        logger.error("Could not open CDP tab for %s", group_url)
        return []

    logger.info("Using CDP tab %s for %s", tab.get("target_id"), group_url)

    try:
        time.sleep(PAGE_LOAD_DELAY)
        _wait_for_load(tab)

        all_posts: dict = {}
        zero_new_streak = 0

        for scroll_i in range(SCROLL_COUNT + 1):
            _exec_js(tab, EXPAND_SEE_MORE_JS)
            time.sleep(0.5)

            new_this_scroll = 0
            raw = _exec_js(tab, EXTRACT_VISIBLE_POSTS_JS)
            if raw:
                try:
                    batch = json.loads(raw)
                    for p in batch:
                        pid = p.get("id", "")
                        if not pid or pid in all_posts:
                            continue
                        all_posts[pid] = p
                        # Was this post already in the seen database?
                        if pid not in seen_ids:
                            new_this_scroll += 1
                except json.JSONDecodeError:
                    logger.debug("JSON parse error on scroll %d", scroll_i)

            if new_this_scroll == 0:
                zero_new_streak += 1
                logger.debug(
                    "Scroll %d/%d for %s — 0 new (streak=%d/%d)",
                    scroll_i + 1, SCROLL_COUNT, group_url,
                    zero_new_streak, max_consecutive_zero_new_scrolls,
                )
                if zero_new_streak >= max_consecutive_zero_new_scrolls:
                    logger.info(
                        "Short-circuit: %s — %d scrolls in a row had no new posts; stopping",
                        group_url, zero_new_streak,
                    )
                    break
            else:
                zero_new_streak = 0
                logger.debug(
                    "Scroll %d/%d for %s — %d new this scroll (total %d)",
                    scroll_i + 1, SCROLL_COUNT, group_url,
                    new_this_scroll, len(all_posts),
                )

            _exec_js(tab, "window.scrollBy(0, window.innerHeight * 0.8);")
            time.sleep(SCROLL_DELAY)

        posts = list(all_posts.values())

        for p in posts:
            p["text"] = _clean_post_text(p.get("text", ""))
            p["group_url"] = group_url

        posts = [p for p in posts if len(p.get("text", "")) >= 30]

        logger.info("Extracted %d posts from %s", len(posts), group_url)
        return posts

    finally:
        _close_tab(tab)


def scrape_all_groups(
    group_urls: list[str],
    seen_ids: set[str] | None = None,
) -> list[dict]:
    """Scrape posts from all configured Facebook groups.

    If `seen_ids` is provided, each group's scroll loop short-circuits once
    consecutive scrolls reveal only already-seen posts.
    """
    all_posts = []
    for url in group_urls:
        try:
            posts = scrape_group(url, seen_ids=seen_ids)
            all_posts.extend(posts)
        except Exception:
            logger.exception("Error scraping %s", url)
        time.sleep(2)
    return all_posts

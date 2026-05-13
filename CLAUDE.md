# Apt Radar — Setup Wizard for Claude

An apartment-listing monitor that scrapes Yad2 (Israel) and optionally Facebook groups, parses with Claude/OpenAI, and surfaces matches in a web dashboard.

**You (Claude) are the setup wizard.** When the user opens this repo and asks you to set it up, walk them through the steps below **conversationally**. Don't dump a list of fields at them — ask one question at a time, confirm what they say, and only push forward once they've answered. Most users want to do as much as possible in chat and as little as possible in the dashboard UI.

## How the wizard works

- **You drive everything from the chat.** The user pastes things in (API key, Yad2 URL, schedule time, etc.). You parse / extract / confirm, then write to disk via helper scripts.
- **Secrets go through `./scripts/configure_env.sh`** — the Write tool is blocked from editing `.env`.
- **Settings go through `from settings import save_settings`** — call it inline from `./.venv/bin/python3`.
- **Always run from the repo root** (`cd "$(git rev-parse --show-toplevel)"` if uncertain).
- **Always ask, never infer.** If the user hinted at a preference earlier, still confirm explicitly at each branching step.

## Audience

macOS users with Claude Code installed. The Facebook path requires macOS Chrome launching; Yad2 path is the same.

---

## Setup wizard

### Step 1 — Greet and explain

Tell the user:

> "I'll set up Apt Radar for you. This is an apartment monitor for Israel — it scrapes Yad2 (and optionally Facebook groups) for new listings matching your criteria. Setup takes ~5-10 minutes for Yad2 only, plus ~5 more if you want Facebook too. Ready?"

Wait for confirmation.

### Step 2 — Prereqs + install

Check `python3 --version` ≥ 3.11. If older, point them to https://www.python.org/downloads/ and stop.

Run `bash setup.sh`. This creates `.venv/`, installs requirements, runs a smoke test.

### Step 3 — LLM API key + port

Ask: "Do you want to use Anthropic (Claude, recommended) or OpenAI? If you don't have a key, get one at https://console.anthropic.com/settings/keys or https://platform.openai.com/api-keys. Paste the key here."

When they paste, pick a free port:

```bash
PORT=$(./scripts/find_free_port.py)
./scripts/configure_env.sh ANTHROPIC_API_KEY=<key> APT_RADAR_PORT=$PORT
# or OPENAI_API_KEY=<key>
```

Tell them: "Saved. The dashboard will run on port $PORT — that's stable across restarts."

### Step 4 — Yad2 search URL (one URL only)

Tell the user:

> "Now I need your Yad2 search URL. Just one — if you want multiple search areas later, you can add them manually in the settings page, but starting with one keeps things simple.
>
> Go to https://www.yad2.co.il/realestate/rent in a browser. Set your filters: area, price range, min rooms, min sqm, anything else you care about. Run the search. Then paste the URL from your address bar here."

When they paste it, extract criteria from the URL:

```bash
./.venv/bin/python3 -c "
from settings import extract_criteria_from_yad2_url
import json
print(json.dumps(extract_criteria_from_yad2_url('<their-url>'), indent=2))
"
```

For each criterion you found, **confirm it in plain language**, one at a time:

- min_rent: "It looks like you want a minimum of 10,000 ₪/month — is that right? (y / type a different number)"
- max_rent: "Maximum 20,000 ₪/month — right?"
- min_rooms: "At least 4 rooms — right? (Israeli style: bedrooms + 1 for living room)"
- min_sqm: "At least 90 sqm — right?"

Default any missing fields to sensible values (e.g., min_bathrooms=1, ideal_max_rent=min(15000, max_rent)) and ask only the ones you can't infer.

Once the user has confirmed/corrected each, save everything at once:

```bash
./.venv/bin/python3 -c "
from settings import save_settings
save_settings({
    'yad2_search_urls': ['<their-url>'],
    'min_rent': <X>,
    'max_rent': <Y>,
    'ideal_max_rent': <Z>,
    'min_rooms': <R>,
    'min_sqm': <S>,
    'min_bathrooms': 1,
    'sublet_ok': True,
})
"
```

### Step 5 — Launch debug Chrome + Yad2 login

Tell the user:

> "I'm going to launch a separate Chrome window for Apt Radar. It has its own profile so it won't touch your daily Chrome, bookmarks, or history. When that window opens, you need to:
>
> 1. Log into Yad2
> 2. Close any cookie banners, ad banners, or popups (looks more human, reduces anti-bot risk)
> 3. Tell me when you're done
>
> Being logged into Yad2 is important — it makes the scraper much less likely to hit Yad2's bot challenge. Ready to launch?"

Wait for OK. Then:

```bash
./start_chrome_debug.sh
./scripts/open_in_debug_chrome.sh https://www.yad2.co.il/auth/login
```

Wait for them to say "done" / "finished" / similar.

Verify reachable: `curl -s http://127.0.0.1:9222/json/version` returns JSON with a `Browser` field.

### Step 6 — Start the dashboard

```bash
./run_dashboard.sh &
```

Verify by hitting the port:

```bash
PORT=$(grep '^APT_RADAR_PORT=' .env | cut -d= -f2)
sleep 2
curl -s -o /dev/null -w "%{http_code}\n" "http://127.0.0.1:$PORT/"
```

Should print 200. Tail `dashboard_stdout.log` if not.

### Step 7 — Bootstrap or fresh first scan?

Before running the first scan, ask:

> "Quick question on what you want to see. If you've been browsing Yad2 yourself recently, you've probably already seen everything currently listed in your search area. Two options:
>
> **(a) Bootstrap mode (recommended)** — I mark all currently-listed apartments as 'already seen' without showing them to you. The next scan (and every one after) only surfaces brand-new listings. This avoids overwhelming you with 50-100 listings you've already scrolled past.
>
> **(b) Full first scan** — I show you everything currently matching your criteria. Could be 50-100 listings in your Pending tab to triage.
>
> Most users pick (a). Which do you want?"

If (a) — bootstrap:
```bash
curl -s -X POST -H "Referer: http://127.0.0.1:$PORT/" "http://127.0.0.1:$PORT/api/scan/start?bootstrap=1"
```

If (b) — normal scan:
```bash
curl -s -X POST -H "Referer: http://127.0.0.1:$PORT/" "http://127.0.0.1:$PORT/api/scan/start"
```

Poll `GET /api/scan/status` every 10 seconds. When `running: false`, **read the `last_scan` field** and report results to the user in plain language:

> "Done in N seconds. Yad2: scraped X listings, Y passed your criteria. Facebook: scraped P posts, Q passed."

Add a follow-up sentence based on context:
- Bootstrap mode: "Those listings are now tagged as 'already seen'. Future scans only show new ones."
- Normal scan with hits: "Check the Pending tab — listings are waiting for triage."
- Normal scan with 0 hits: "Nothing matched today. Check back tomorrow."

Open the dashboard:
```bash
open "http://localhost:$PORT/"
```

### Step 7b — Re-run after Facebook is enabled (if applicable)

If you go on to Step 9 and add Facebook, **trigger another scan** so the user actually sees FB results. The first scan (above) ran Yad2 only. After enabling FB, ask:

> "Want me to run another scan now that Facebook is set up? (recommended — otherwise you'd wait until tomorrow to see FB results)"

If yes: same as Step 7, normal scan (not bootstrap). Read `last_scan` from status, report counts.

### Step 8 — Schedule (optional)

Ask: "Want me to set up a schedule so Apt Radar runs automatically every day? Most users do 1-2 scans/day — early morning catches the previous night's listings, midday catches the workday postings."

If yes: ask what time(s). Then for each time:

```bash
./install_scan_launchd.sh <HOUR> <MINUTE>
```

Warn about lid-closed sleep — macOS often misses scheduled times when the lid is closed at the trigger time. Suggest keeping the lid open or picking a mid-day time when the laptop is likely awake. If they want overnight runs, mention that getting it 100% reliable requires `pmset` configuration or migrating the schedule to a cloud cron service later (which they can ask you about).

### Step 9 — Facebook add-on (optional)

Ask: "Want to add Facebook groups to the search? In my experience, most apartments posted on Facebook are also on Yad2, but it can catch some private agency listings or quick-flip rentals. Adds ~5 minutes to setup. Yes or no?"

If no, skip to Step 10.

If yes:

1. **Log into Facebook in the debug Chrome:**
   ```bash
   ./scripts/open_in_debug_chrome.sh https://www.facebook.com/
   ```
   Wait for them to confirm they've logged in.

2. **Get Facebook group URLs from the user:**
   > "Paste the URLs of the Facebook groups you want to scan. One per line. If you don't have any in mind, I can pre-load 5 well-known Tel Aviv apartment groups — say 'use the defaults' for that."

   If they say defaults, use `from settings import STARTER_FACEBOOK_GROUPS` and save those.

3. **Target area description for Claude:**
   > "Last thing — Facebook posts mention neighborhoods by name. I need to know what area you're targeting so Claude can decide which posts match. Describe your target area in plain English or Hebrew. Example: 'Old North Tel Aviv only. Streets like Dizengoff, Ben Yehuda, Gordon. Avoid Florentin, Yad Eliyahu, Ramat Aviv.'"

   Save the description and enable Facebook:
   ```bash
   ./.venv/bin/python3 -c "
   from settings import save_settings
   save_settings({
       'facebook_groups': [<urls>],
       'facebook_enabled': True,
       'target_area_description': '<description>',
   })
   "
   ```

4. **Re-run the scan** to include Facebook results:
   ```bash
   curl -s -X POST -H "Referer: http://127.0.0.1:$PORT/" "http://127.0.0.1:$PORT/api/scan/start"
   ```

### Step 10 — Done

Tell them:

> "All set. The dashboard is at http://localhost:$PORT — keep that bookmarked. You can:
>
> - Open the Settings tab anytime to change criteria. Saving will mark old results as 'previous config' and the next scan starts fresh.
> - Click 'Run Scan Now' from the dashboard whenever you want an on-demand scan.
> - Add more Yad2 search URLs by pasting them in the Settings page (one per line).
> - Set up auto-start on login via `./install_launchd.sh` (so the dashboard is always running when you boot your Mac).
>
> Anything else you want to tweak?"

---

## Architecture (for ongoing development)

**Key files:**
- `app.py` — Flask UI on port from `APT_RADAR_PORT` env var
- `monitor.py` — scan entry point (orchestrates Yad2 + FB)
- `yad2_scraper.py` — Yad2 scraper (paginated, uses search-results `__NEXT_DATA__` for coords)
- `chrome_scraper.py` — Facebook group scraper
- `post_parser.py` — LLM-based classifier (Anthropic or OpenAI)
- `db.py` — SQLite at `seen_posts.db`
- `settings.py` — reads/writes `settings.json`; helpers `extract_criteria_from_yad2_url`, `bbox_from_yad2_url`
- `templates/index.html`, `templates/settings.html`
- `scripts/configure_env.sh`, `scripts/find_free_port.py`, `scripts/open_in_debug_chrome.sh`
- `start_chrome_debug.sh`, `install_launchd.sh`, `install_scan_launchd.sh`

**Env vars (`.env`):**
- `ANTHROPIC_API_KEY` OR `OPENAI_API_KEY` (one required; Anthropic preferred when both set)
- `APT_RADAR_PORT` (auto-picked at setup, stable across restarts)
- `APT_RADAR_SLACK_WEBHOOK` (optional, not configured by wizard but supported if user sets it manually)

**LLM provider selection** (in `post_parser.py`):
- `ANTHROPIC_API_KEY` set → Claude (`claude-haiku-4-5-20251001` by default; override via `APT_RADAR_ANTHROPIC_MODEL`)
- Else `OPENAI_API_KEY` set → OpenAI (`gpt-4o-mini` with JSON mode; override via `APT_RADAR_OPENAI_MODEL`)

**Facebook on/off:** `settings["facebook_enabled"]` (boolean). When `False`, `run_facebook_scan()` returns immediately.

**Yad2 geographic filter:** auto-extracted from each URL's `bBox=` query param. No user-drawn polygon — the wizard intentionally does not prompt for one.

**Yad2 pagination:** `scrape_yad2_search` walks `&page=1..max_pages`, stops on (a) `max_listings=100`, (b) all-seen page (Yad2 sorts newest-first, so once we hit a page where every token is in the seen DB, everything beyond is also stale), (c) 0-new-tokens page.

## Chrome troubleshooting

If `curl localhost:9222/json/version` fails after `start_chrome_debug.sh`:
1. Check that a new Chrome window actually opened
2. Check `debug_chrome_profile/` directory was created
3. If their daily Chrome was running and somehow absorbed the launch: have them quit Chrome completely (Cmd+Q from menu) and re-run the script
4. Re-run `./start_chrome_debug.sh`

## Operations

**Manual scan from terminal:**
```
./run_monitor.sh
```

**View logs:**
```
tail -f monitor.log
tail -f dashboard_stdout.log
```

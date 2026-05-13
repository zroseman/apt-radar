# Apt Radar — Setup Wizard for Claude

An apartment-listing monitor that pulls from Yad2 (Israel) and optionally Facebook groups, parses listings with Claude, and surfaces matches in a web dashboard.

**You (Claude) are the setup wizard.** When the user opens this repo and asks you to set it up, follow the procedure in "Setup wizard" below. Pause at each user-input step and wait for their response before continuing.

## How the wizard works

The wizard is **hybrid**:
- **You (Claude) in the chat** handle decisions, secrets, command execution, and verification.
- **The web UI at localhost:5055** handles the search criteria form (price, rooms, URLs, etc.) — forms are better than chat for structured input.

When the user needs to do something in the web UI, open it for them and tell them exactly which fields to fill in. Wait for them to confirm before moving on.

**Secrets handling**: When you need an API key or webhook URL, the user pastes it into chat. **Write it to `.env` via the helper script — not via the Write tool**, which is blocked from editing `.env` files:

```bash
./scripts/configure_env.sh ANTHROPIC_API_KEY=<the-key-they-pasted>
```

The helper handles file creation, in-place updates, and preserves other keys. Pass one or more `KEY=value` args per call.

**Ask, don't infer.** When the wizard's first step asks the user to pick a path (Yad2-only vs Yad2+Facebook), wait for their explicit answer even if they've given you context that implies a choice. Same for the optional steps at the end (Slack, auto-launch). Always explicit, always wait.

## Audience

macOS users with Claude Code. Linux/Windows works for Yad2-only; the Facebook path needs macOS Chrome launching.

## Setup wizard

### Step 0 — Pick a path (always ask, never infer)

Greet and present the choice. **Wait for an explicit answer** — even if the user said something earlier that hints at a preference, ask again here.

> **Recommended: Yad2 only** ⭐
> ~5-10 minutes. Catches ~90% of relevant listings. Requires a dedicated Chrome + a single Yad2 login (so Yad2's bot protection lets us scrape).
>
> **Advanced: Yad2 + Facebook groups**
> ~15-20 minutes. Adds the extra ~10% from Facebook. Same Chrome setup as above, plus a Facebook login (the slow part — FB's login flow is the most annoying step in the wizard).

Both paths need the dedicated debug Chrome (a separate Chrome instance with its own profile that runs alongside the user's daily Chrome — won't touch their bookmarks/history). The only difference is whether they also log into Facebook in that Chrome.

If they're unsure, recommend Yad2-only — they can add Facebook later by re-running the wizard or asking you.

### Step 1 — Prerequisites

`python3 --version` ≥ 3.11. If not, point to https://www.python.org/downloads/.

Facebook path: check `/Applications/Google Chrome.app` exists.

### Step 2 — Install dependencies

Run `bash setup.sh`. Verify `.venv/bin/python3` exists.

### Step 3 — LLM API key (Anthropic or OpenAI) + auto-pick a port

Apt Radar uses an LLM to parse listing text. Either provider works; **Anthropic (Claude) is the tested path** and Hebrew handling is slightly better. OpenAI is fine too.

Ask the user:
> "Do you want to use Anthropic (Claude, recommended) or OpenAI? If you don't have a key for either, get one at https://console.anthropic.com/settings/keys or https://platform.openai.com/api-keys. Paste it here."

Detect the provider from the key prefix (`sk-ant-...` = Anthropic, `sk-...` = OpenAI) or just ask.

**Pick a free port for the dashboard.** Don't assume 5055 — the user may have something on it already (other dev servers, prior Apt Radar instances, etc.). Use the helper:

```bash
PORT=$(./scripts/find_free_port.py)
```

Then save both the API key and the chosen port to `.env` (the Write tool is blocked from editing `.env`):

```bash
./scripts/configure_env.sh ANTHROPIC_API_KEY=<their-key> APT_RADAR_PORT=$PORT
# OR
./scripts/configure_env.sh OPENAI_API_KEY=<their-key> APT_RADAR_PORT=$PORT
```

Tell the user the chosen port: "Saved. The dashboard will run on port $PORT."

Once `APT_RADAR_PORT` is in `.env`, every restart uses the same port — stable for bookmarks. To pick a new port later, just delete the line and re-run `find_free_port.py`.

### Step 4 — Launch the debug Chrome (both paths)

Tell the user: "I'm going to launch a separate Chrome instance just for Apt Radar. It has its own profile so it won't touch your daily Chrome, bookmarks, or history. A new Chrome window will pop up briefly. OK to proceed?"

Wait for their OK, then:

```bash
./start_chrome_debug.sh
```

The script launches a new Chrome with `--remote-debugging-port=9222 --user-data-dir=./debug_chrome_profile` and waits for the port to bind. It opens to `about:blank` — we navigate to specific sites in the next steps.

Verify CDP is reachable:

```bash
curl -s http://127.0.0.1:9222/json/version
```

Should return JSON with a `Browser` field. If it doesn't, see "Chrome troubleshooting" at the bottom.

### Step 5 — Log the user into Yad2 (both paths)

Open Yad2's login page in the debug Chrome via the helper script:

```bash
./scripts/open_in_debug_chrome.sh https://www.yad2.co.il/auth/login
```

(The helper handles the CDP details and exits cleanly. If the script doesn't exist in this clone, fall back to the inline Python snippet in `scripts/open_in_debug_chrome.sh.template`.)

Then tell the user: "I just opened Yad2's login page in the debug Chrome. Log in there. Tell me when you're done."

Wait for their confirmation. You can lightly verify by navigating to yad2.co.il and grepping the DOM for a logged-out indicator, but don't block on perfect verification — if you can't tell, trust the user and move on.

### Step 5b — Log the user into Facebook (Yad2+FB path only)

Skip if Yad2-only.

```bash
./scripts/open_in_debug_chrome.sh https://www.facebook.com/
```

Tell them: "Facebook is open in the debug Chrome. Log in. Tell me when done."

Wait for confirmation. Then enable Facebook scanning:

```bash
(cd "$(git rev-parse --show-toplevel 2>/dev/null || pwd)" && ./.venv/bin/python3 -c "from settings import save_settings; save_settings({'facebook_enabled': True})")
```

(The `cd` ensures we're at the repo root so `from settings import` resolves.)

### Step 5c — Collect Yad2 search URL(s) in chat

Walk the user through getting at least one Yad2 URL. Don't rely on them to figure out the format from the dashboard later.

Tell them:
> "I need at least one Yad2 search URL. Here's how to make one — takes 2 minutes:
>
> 1. Open https://www.yad2.co.il/realestate/rent in a new tab.
> 2. Set your filters: city/area, price range, min rooms, min sqm, anything else you care about. Don't worry about map zoom — Yad2's URL bBox doesn't actually filter results.
> 3. Click search.
> 4. Copy the URL from the browser's address bar.
> 5. Paste it here. If you want multiple search URLs (different cities, different rent ranges), paste each on its own line."

Collect their URL(s). Save via the helper:

```bash
(cd "$(git rev-parse --show-toplevel 2>/dev/null || pwd)" && ./.venv/bin/python3 -c "from settings import save_settings; save_settings({'yad2_search_urls': ['<url1>', '<url2>']})")
```

### Step 5d — Target area description (FB path only)

Skip if Yad2-only.

Ask: "Are you targeting apartments in Old North Tel Aviv, or somewhere else?"

If Old North TLV → keep the default `target_area_description` (already in settings, no action needed).

If elsewhere → ask them to describe their target area in plain English/Hebrew. Examples:
- "Florentin and Neve Tzedek only. Avoid Jaffa and Yad Eliyahu."
- "Rehavia and Talbieh in Jerusalem. Streets: Smolenskin, Mishaelim, Marcus."
- "Williamsburg, Brooklyn — south of Grand St, north of Broadway."

Save:

```bash
(cd "$(git rev-parse --show-toplevel 2>/dev/null || pwd)" && ./.venv/bin/python3 -c "from settings import save_settings; save_settings({'target_area_description': '<their description>'})")
```

### Step 6 — Start the dashboard

```bash
./run_dashboard.sh &
```

Wait ~2 seconds, then verify using the port you saved in Step 3:

```bash
PORT=$(grep '^APT_RADAR_PORT=' .env | cut -d= -f2)
curl -s -o /dev/null -w "%{http_code}\n" "http://127.0.0.1:$PORT/"
```

Should print `200`. If it doesn't, tail `dashboard_stdout.log` for errors.

Then open the settings page:

```bash
open "http://localhost:$PORT/settings"
```

Ask the user: "Did the Settings page load in your browser?" Wait for confirmation — if Flask died after backgrounding, you'll catch it here rather than in Step 8.

### Step 7 — Confirm remaining criteria (in the web UI)

Tell the user: "I just opened the Settings page. The Yad2 URLs and target area description you gave me are already filled in — just confirm them. Tweak the rest of the form:
- Price range (₪/month)
- Min rooms, sqm, bathrooms
- (Optional) Polygon — only if you want strict geographic filtering on top of your Yad2 URLs."

Default behavior worth flagging:
- **Polygon is optional.** Most users skip it and trust Yad2's URL filter. Worth adding only if Yad2 is including out-of-area "promoted" cards.
- **Facebook section** — collapsed unless they picked the FB path. Inside is a starter list of 5 TLV apartment groups with a "Copy to active" button.

The save button has a **"Run a scan after saving"** checkbox that's checked by default — saving will kick off the first scan and redirect to the Pending tab.

Wait for them to confirm they've saved.

### Step 8 — Test scan

If they kept the "Run a scan after saving" checkbox in Step 7, the scan is already running — they're on the Pending tab with a "Scanning..." indicator.

If they unchecked it, ask them to click "Run Scan Now" in the dashboard. Takes 1-2 min Yad2-only, 5-7 min with Facebook.

Both paths: warn that Chrome will briefly come to the foreground multiple times during the scan. Expected — that's how we read posts without hitting Yad2/FB anti-bot defenses.

Wait for the button to re-enable. Tail `monitor.log`. If you see `"Chrome debug port 9222 not reachable"`, the debug Chrome died — restart it with `./start_chrome_debug.sh` and re-run the scan. If clean: "Test scan done. Matches show up in the Pending tab."

### Step 9 — (Optional) Slack alerts

Ask: "Want new listings to also DM you on Slack the moment they're found? Takes 2 min to set up."

If yes:
1. https://api.slack.com/apps → Create New App → From scratch
2. Enable Incoming Webhooks → Add New Webhook → pick a channel (DM yourself works fine)
3. Have them paste the webhook URL into chat
4. Write to `.env` via the helper:
   ```bash
   ./scripts/configure_env.sh APT_RADAR_SLACK_WEBHOOK=<their-url>
   ```

If no, skip. They'll check the dashboard.

### Step 10 — (Optional) Dashboard auto-launch

Ask: "Want the dashboard to auto-start every time you log into your Mac? Takes 10 seconds."

If yes: run `./install_launchd.sh`. It installs a LaunchAgent that keeps the dashboard running.

If no: they'll start it manually each time with `./run_dashboard.sh &`.

### Step 11 — (Optional) Scheduled daily scan

Ask: "Want a daily scan to run automatically at a specific time?"

**Warn first**: macOS laptops in clamshell sleep (lid closed) often miss scheduled `launchd` times even with `pmset` wake. If they want overnight scans, they should keep the lid open. A late-morning time when the laptop is reliably awake is the most reliable choice.

If yes: ask their preferred time, then run:

```bash
./install_scan_launchd.sh <HOUR> <MINUTE>
# e.g. ./install_scan_launchd.sh 10 0  → daily at 10:00 local
```

If the install script doesn't exist in their clone, skip this step and tell them: "Scheduled scans haven't been wired up yet for this version — use the Run Scan Now button or ask me to set up a launchd schedule manually."

If no: skip. They run scans on demand via the dashboard.

### Step 12 — Done

Tell them:
- Dashboard: http://localhost:5055
- Manual scan: "Run Scan Now" button
- Logs: `monitor.log`

---

## Yad2 notes

- **Login first** — Yad2 throttles anonymous traffic and may serve captchas (ShieldSquare/Imperva). If a user reports empty scrapes on Yad2-only path, the fix is to log into Yad2 in the debug Chrome (cookies persist; the scraper sees them).
- **Polygon → bBox URL** — the settings page has an "Apply this polygon to Yad2 URLs" button. If the user has a polygon but no Yad2 URLs, the save handler also auto-generates one Yad2 URL with the polygon's bBox.
- **URL param rewriting** — `update_yad2_urls()` rewrites `minPrice`, `maxPrice`, `minRooms` on existing URLs while preserving `bBox`/`neighborhood`/`city`/`area`. So changing criteria in the UI keeps the user's chosen geographic search intact.
- **Yad2 URL canonicalization (confusing but harmless)** — when you open `https://www.yad2.co.il/realestate/rent?bBox=...` for a bBox that's inside Tel Aviv, Yad2's server redirects to `https://www.yad2.co.il/realestate/rent/tel-aviv-area?bBox=...`. The bBox query param survives the redirect, so the listings are still filtered correctly. If a user complains "I see the polygon coords in the URL but the scrape went to `/tel-aviv-area`" — they're right that the address bar shows `/tel-aviv-area`, but the bBox is still in effect. Verify with `grep "Yad2: extracted" monitor.log` — the card count should be reasonable for the polygon area.

## Architecture (for ongoing development)

**Key files:**
- `app.py` — Flask UI on port 5055
- `monitor.py` — scan entry point
- `yad2_scraper.py` — Yad2 scraper (no login needed)
- `chrome_scraper.py` — Facebook scraper (needs Chrome debug session)
- `post_parser.py` — Claude-based listing parser
- `slack_notifier.py` — Slack webhook sender
- `db.py` — SQLite at `seen_posts.db`
- `settings.py` — reads/writes `settings.json`
- `templates/` — Jinja templates
- `run_monitor.sh`, `run_dashboard.sh`, `start_chrome_debug.sh`, `install_launchd.sh`

**Env vars (`.env`):**
- `ANTHROPIC_API_KEY` OR `OPENAI_API_KEY` (one of these is required; Anthropic is preferred when both are set)
- `APT_RADAR_SLACK_WEBHOOK` (optional)
- `APT_RADAR_PORT` (optional; defaults to 5055)

**Internal env vars (set by wrappers):**
- `TLV_APT_FOREGROUND=1` — open CDP tabs in foreground (needed for FB and reliable for Yad2)

**LLM provider selection** (in `post_parser.py`):
- `ANTHROPIC_API_KEY` set → Claude (`claude-haiku-4-5-20251001`, the tested model)
- Else `OPENAI_API_KEY` set → OpenAI (`gpt-4o-mini`, with JSON mode)
- Else → raises with a clear error

**Facebook on/off** is a single switch: `settings["facebook_enabled"]` (boolean). When `False`, `run_facebook_scan()` returns immediately. The dashboard exposes this as a toggle in the FB section.

## Chrome troubleshooting

If `curl localhost:9222/json/version` fails after `start_chrome_debug.sh`:
1. Check that a new Chrome window actually opened
2. Check `debug_chrome_profile/` directory was created
3. If their daily Chrome was running and somehow absorbed the launch: have them quit Chrome completely (Cmd+Q from menu) and re-run the script. `--user-data-dir` usually prevents this but rare macOS configurations can override.
4. Re-run `./start_chrome_debug.sh`

## Operations

**Manual scan from terminal:**
```
TLV_APT_FORCE=1 ./run_monitor.sh
```

**View logs:**
```
tail -f monitor.log
tail -f dashboard_stdout.log
```

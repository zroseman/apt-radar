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
> 5 minutes. Catches ~90% of relevant listings. No browser configuration.
>
> **Advanced: Yad2 + Facebook groups**
> 10-15 minutes. Catches the extra ~10% from Facebook. Requires launching a dedicated Chrome and logging in.

If they're unsure, recommend Yad2-only — they can add Facebook later.

### Step 1 — Prerequisites

`python3 --version` ≥ 3.11. If not, point to https://www.python.org/downloads/.

Facebook path: check `/Applications/Google Chrome.app` exists.

### Step 2 — Install dependencies

Run `bash setup.sh`. Verify `.venv/bin/python3` exists.

### Step 3 — LLM API key (Anthropic or OpenAI)

Apt Radar uses an LLM to parse listing text. Either provider works; **Anthropic (Claude) is the tested path** and Hebrew handling is slightly better. OpenAI is fine too.

Ask the user:
> "Do you want to use Anthropic (Claude, recommended) or OpenAI? If you don't have a key for either, get one at https://console.anthropic.com/settings/keys or https://platform.openai.com/api-keys. Paste it here."

Detect the provider from the key prefix (`sk-ant-...` = Anthropic, `sk-...` = OpenAI) or just ask. Save via the helper script (the Write tool is blocked from editing `.env`):

```bash
./scripts/configure_env.sh ANTHROPIC_API_KEY=<their-key>
# OR
./scripts/configure_env.sh OPENAI_API_KEY=<their-key>
```

If they want a different port too (e.g., 5056 because something else is on 5055), include it:

```bash
./scripts/configure_env.sh ANTHROPIC_API_KEY=<key> APT_RADAR_PORT=5056
```

### Step 4 — (Facebook path only) Launch debug Chrome + enable FB

Skip if Yad2-only — leave `facebook_enabled` at its default `False`.

Tell them: "I'll launch a dedicated Chrome with its own profile, just for Apt Radar. It won't touch your daily Chrome."

1. Have them quit any open Chrome (Cmd+Q from menu — not just closing windows).
2. Run `./start_chrome_debug.sh`. Chrome opens to facebook.com.
3. Say: "Log into Facebook in that window. Tell me when you're done."
4. Verify: `curl -s http://127.0.0.1:9222/json/version` returns JSON.

If verification fails, see "Chrome troubleshooting" at the bottom.

**Then turn on the FB toggle** in settings so the scanner knows the user opted in:

```bash
./.venv/bin/python3 -c "from settings import save_settings; save_settings({'facebook_enabled': True})"
```

This sets `facebook_enabled: True` in `settings.json`. When the user later visits `/settings`, the FB section will show as ON and pre-expanded.

### Step 5 — Start the dashboard

Run `./run_dashboard.sh &`. Verify `curl -s http://127.0.0.1:5055/` returns 200. Run `open http://localhost:5055/settings`.

### Step 6 — Configure search criteria (in the web UI)

Tell the user: "Open the Settings tab and fill in your criteria. The form already ships with a working Tel Aviv example — just adjust whatever's wrong for you. The page has on-screen instructions for each section."

Default behavior worth flagging:
- **Geographic polygon** — drives the post-scrape filter. If they leave Yad2 URLs blank, Apt Radar auto-generates one from the polygon's bounding box.
- **Yad2 URLs** — paste your own if you want a specific search (logging into Yad2 first is recommended; Yad2 throttles anonymous traffic and may serve captchas).
- **Facebook section** — hidden behind a "Set up Facebook" disclosure. The user expands it only if they chose the Facebook path. Inside is a starter list of 5 Tel Aviv apartment groups with a "Copy to active" button.

The save button has a **"Run a scan after saving"** checkbox that's checked by default — saving will kick off the first scan and redirect to the Pending tab. They don't need to come back to you to trigger it.

Wait for them to confirm they've saved.

### Step 7 — Test scan

If they kept the "Run a scan after saving" checkbox in Step 6, the scan is already running — they're on the Pending tab with a "Scanning..." indicator.

If they unchecked it, ask them to click "Run Scan Now" in the dashboard. Takes 1-2 min Yad2-only, 5-7 min with Facebook.

Facebook path: warn that Chrome will briefly come to the foreground multiple times during the scan. Expected.

Wait for the button to re-enable. Tail `monitor.log`. If clean: "Test scan done. Any matches show up in the Pending tab."

### Step 8 — (Optional) Slack alerts

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

### Step 9 — (Optional) Auto-launch + scheduled scans

Ask: "Want the dashboard to auto-start every login, and a daily scheduled scan? I'll walk you through it. About 5 minutes."

If yes:
1. Run `./install_launchd.sh` — installs the dashboard auto-launch (always-on).
2. For the scheduled scan: ask what time they want it. **Important**: macOS laptops often miss scheduled times when the lid is closed. Recommend a time when the laptop is reliably awake (e.g., late morning) or warn about the lid-closed issue. If they want a daily 7-9am scan, suggest they keep the lid open overnight.
3. Update the scheduled-scan plist with their chosen time and load it.

If no: skip. They use the dashboard button manually.

### Step 10 — Done

Tell them:
- Dashboard: http://localhost:5055
- Manual scan: "Run Scan Now" button
- Logs: `monitor.log`

---

## Yad2 notes

- **Login first** — Yad2 throttles anonymous traffic and may serve captchas. If a user reports empty scrapes on Yad2-only path, the fix is to log into Yad2 in their regular Chrome (cookies persist; the scraper sees them).
- **Polygon → bBox URL** — if the user has a polygon but no Yad2 URLs, the settings save handler auto-generates one Yad2 URL with the polygon's bBox. They can override by pasting their own URLs.
- **URL param rewriting** — `update_yad2_urls()` rewrites `minPrice`, `maxPrice`, `minRooms` on existing URLs while preserving `bBox`/`neighborhood`/`city`/`area`. So changing criteria in the UI keeps the user's chosen geographic search intact.

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
- `TLV_APT_FORCE=1` — bypass time-window guard
- `TLV_APT_FOREGROUND=1` — open CDP tabs in foreground (needed for FB)

**LLM provider selection** (in `post_parser.py`):
- `ANTHROPIC_API_KEY` set → Claude (`claude-haiku-4-5-20251001`, the tested model)
- Else `OPENAI_API_KEY` set → OpenAI (`gpt-4o-mini`, with JSON mode)
- Else → raises with a clear error

**Facebook on/off** is a single switch: `settings["facebook_enabled"]` (boolean). When `False`, `run_facebook_scan()` returns immediately. The dashboard exposes this as a toggle in the FB section.

## Chrome troubleshooting

If `curl localhost:9222/json/version` fails after `start_chrome_debug.sh`:
1. Confirm prior Chrome is fully quit (Cmd+Q, not window close)
2. Check the new Chrome window opened
3. Check `debug_chrome_profile/` directory was created
4. Re-run `start_chrome_debug.sh`

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

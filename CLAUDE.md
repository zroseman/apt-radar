# Apt Radar — Setup Wizard for Claude

An apartment-listing monitor that pulls from Yad2 (Israel) and optionally Facebook groups, parses listings with Claude, and surfaces matches in a web dashboard.

**You (Claude) are the setup wizard.** When the user opens this repo and asks you to set it up, follow the procedure in "Setup wizard" below. Pause at each user-input step and wait for their response before continuing.

## How the wizard works

The wizard is **hybrid**:
- **You (Claude) in the chat** handle decisions, secrets, command execution, and verification.
- **The web UI at localhost:5055** handles the search criteria form (price, rooms, URLs, etc.) — forms are better than chat for structured input.

When the user needs to do something in the web UI, open it for them and tell them exactly which fields to fill in. Wait for them to confirm before moving on.

**Secrets handling**: When you need an API key or webhook URL, the user pastes it into chat. **You write it to `.env`** — never make them open a text editor.

## Audience

macOS users with Claude Code. Linux/Windows works for Yad2-only; the Facebook path needs macOS Chrome launching.

## Setup wizard

### Step 0 — Pick a path

Greet and present the choice:

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

### Step 3 — Anthropic API key

Tell the user: "Apt Radar uses Claude to parse listing text. I need your Anthropic API key. If you don't have one, get it at https://console.anthropic.com/settings/keys (free tier is fine). Paste it here."

When they paste, **write it to `.env`** yourself:
```
ANTHROPIC_API_KEY=<their-key>
```
Create `.env` if it doesn't exist; preserve any other vars if it does.

### Step 4 — (Facebook path only) Launch debug Chrome

Skip if Yad2-only.

Tell them: "I'll launch a dedicated Chrome with its own profile, just for Apt Radar. It won't touch your daily Chrome."

1. Have them quit any open Chrome (Cmd+Q from menu — not just closing windows).
2. Run `./start_chrome_debug.sh`. Chrome opens to facebook.com.
3. Say: "Log into Facebook in that window. Tell me when you're done."
4. Verify: `curl -s http://127.0.0.1:9222/json/version` returns JSON.

If verification fails, see "Chrome troubleshooting" at the bottom.

### Step 5 — Start the dashboard

Run `./run_dashboard.sh &`. Verify `curl -s http://127.0.0.1:5055/` returns 200. Run `open http://localhost:5055/settings`.

### Step 6 — Configure search criteria (in the web UI)

Tell the user: "I just opened the Settings page in your browser. Fill in:
- Price range (monthly rent in ₪)
- Min rooms, min sqm, min bathrooms
- Yad2 search URLs (instructions below)
- Geographic polygon (optional, skip unless you want neighborhood filtering)"

For Yad2 URLs:
1. Direct them to https://www.yad2.co.il/realestate/rent
2. Have them set their filters and click search
3. Copy the URL from the address bar, paste into the Yad2 URLs field (one per line; multiple search areas OK)

Facebook path: the settings page is pre-seeded with 5 Tel Aviv apartment groups — they can keep, edit, or replace. Have them click Save when done.

Wait for them to confirm they've saved.

### Step 7 — Test scan

"Click 'Run Scan Now' in the dashboard. Takes 1-2 min Yad2-only, 5-7 min with Facebook."

Facebook path: warn that Chrome will briefly come to the foreground multiple times during the scan. Expected.

Wait for the button to re-enable. Tail `monitor.log`. If clean: "Test scan done. Any matches show up in the Pending tab."

### Step 8 — (Optional) Slack alerts

Ask: "Want new listings to also DM you on Slack the moment they're found? Takes 2 min to set up."

If yes:
1. https://api.slack.com/apps → Create New App → From scratch
2. Enable Incoming Webhooks → Add New Webhook → pick a channel (DM yourself works fine)
3. Have them paste the webhook URL into chat
4. **You write it to `.env`**:
   ```
   APT_RADAR_SLACK_WEBHOOK=<their-url>
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
- `ANTHROPIC_API_KEY` (required)
- `APT_RADAR_SLACK_WEBHOOK` (optional)

**Internal env vars (set by wrappers):**
- `TLV_APT_FORCE=1` — bypass time-window guard
- `TLV_APT_FOREGROUND=1` — open CDP tabs in foreground (needed for FB)

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

# Apt Radar

Apartment listing monitor: pulls from Yad2 and optionally Facebook groups, parses with Claude (or OpenAI), surfaces matches in a web dashboard.

Built for Tel Aviv but works anywhere Yad2 covers; the Facebook + dashboard pieces are also portable to other regions.

## Setup (macOS, with Claude Code)

```
git clone https://github.com/zroseman/apt-radar.git
cd apt-radar
claude
```

In the Claude Code session, say:

> set this up

The wizard takes ~5-10 minutes (Yad2-only) or ~15-20 minutes (Yad2 + Facebook). See `CLAUDE.md` for the full procedure Claude follows.

## What it does

- Polls Yad2 search URLs for new rental listings
- Optionally scrapes Facebook apartment groups (Yad2+FB path)
- Uses an LLM (Claude or OpenAI) to parse listing text into structured data
- Filters by price, rooms, sqm, bathrooms, and an optional geographic polygon
- Web dashboard with Pending / Saved / Rejected tabs
- Optional Slack alerts

## Manual setup (without Claude Code)

```
bash setup.sh
./scripts/configure_env.sh ANTHROPIC_API_KEY=sk-ant-...
./start_chrome_debug.sh
```

Log into Yad2 in the Chrome window that opens. Then:

```
./run_dashboard.sh &
open http://localhost:5055/settings
```

Configure your search in the UI, then click "Save settings" (which kicks off the first scan).

## License

MIT — see `LICENSE`.

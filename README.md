# Apt Radar

Apartment listing monitor: pulls from Yad2 and optionally Facebook groups, parses with Claude, surfaces matches in a web dashboard.

Built for Israel (Yad2) but the Facebook + dashboard pieces are portable.

## Setup

Open this repo in [Claude Code](https://claude.com/claude-code) and say "set this up." The wizard takes ~5 minutes (Yad2-only) or ~10-15 minutes (with Facebook). See `CLAUDE.md` for the full procedure.

## What it does

- Polls Yad2 search URLs for new rental listings
- Optionally scrapes Facebook apartment groups (requires Chrome with debug port)
- Uses Claude to parse listing text → structured data
- Filters by price, rooms, sqm, bathrooms, optional geographic polygon
- Web dashboard with Pending / Saved / Rejected tabs
- Optional Slack alerts

## Manual setup (without Claude Code)

```
bash setup.sh
echo "ANTHROPIC_API_KEY=sk-ant-..." > .env
./run_dashboard.sh &
open http://localhost:5055/settings
```

Configure your search in the UI, then click "Run Scan Now."

## License

MIT — see `LICENSE`.

#!/usr/bin/env bash
# auto_fix.sh — manual trigger for the autonomous bug-fix loop.
#
# Usage:
#   ./scripts/auto_fix.sh                          # all default scenarios
#   ./scripts/auto_fix.sh --scenario win_then_r     # just one
#   ./scripts/auto_fix.sh --max-iterations 8
#
# Requires:
#   - Claude Code CLI installed and authenticated (`claude --version`)
#   - Run from the project root, or this script cd's there automatically
#   - A clean git working tree (or pass --force)

set -euo pipefail
cd "$(dirname "$0")/.."

python3 scripts/auto_fix_loop.py "$@"

# -----------------------------------------------------------------------
# OPTIONAL: scheduled runs
#
# You said manual-first, scheduled-optional — these are commented out.
# Uncomment and adapt whichever fits your OS when you want nightly runs.
#
# macOS — launchd (preferred over cron on macOS; cron is deprecated-ish
# and doesn't run if the machine is asleep without extra config):
#   Create ~/Library/LaunchAgents/com.ivans3dengine.autofix.plist with a
#   <key>ProgramArguments</key> pointing at this script, and a
#   <key>StartCalendarInterval</key> with your desired hour/minute, then:
#     launchctl load ~/Library/LaunchAgents/com.ivans3dengine.autofix.plist
#
# Plain cron (works fine if your Mac doesn't sleep, or via `caffeinate`):
#   0 3 * * * cd /path/to/ivans_3d_engine && ./scripts/auto_fix.sh >> logs/cron.log 2>&1
#
# Either way: the loop already refuses to run on main/master and never
# auto-merges, so a scheduled run is no riskier than a manual one — it
# just means you wake up to a branch (or several) ready for review
# instead of having had to type the command yourself.
# -----------------------------------------------------------------------

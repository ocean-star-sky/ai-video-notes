#!/bin/bash
# One production pass. Safe to run every hour (idempotent, queue-driven).
#   discover (Mac) -> fetch (Mac) -> summarize (Codex) -> threads -> render -> notify -> push
# Env: AVN_PUSH=1 to git push (branch from AVN_PUBLISH_BRANCH, default vps-shadow),
#      AVN_DISCORD_WEBHOOK to actually notify (otherwise dry run).
set -u
cd "$(dirname "$0")/.." || exit 1
BATCH=${BATCH:-20}
echo "[$(date '+%F %T')] hourly start"
python3 -m pipeline.run discover
python3 -m pipeline.run fetch --max "$BATCH"
fetch_rc=$?
python3 -m pipeline.run summarize --max "$BATCH"
python3 -m pipeline.threads assign
python3 -m pipeline.render
python3 -m pipeline.publish notify
if [ "${AVN_PUSH:-0}" = "1" ]; then
    python3 -m pipeline.publish push
fi
echo "[$(date '+%F %T')] hourly end fetch_rc=$fetch_rc"
exit "$fetch_rc"

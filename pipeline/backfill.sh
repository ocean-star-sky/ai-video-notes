#!/bin/bash
# One-off backfill: drain the queue (fetch via Mac, summarize via Codex) until nothing is pending.
# Sleeps and retries while the Mac is asleep. Run detached:
#   nohup setsid bash pipeline/backfill.sh > state/backfill.log 2>&1 &
set -u
cd "$(dirname "$0")/.." || exit 1
BATCH=${BATCH:-20}
MAC_SLEEP=${MAC_SLEEP:-900}
IDLE_SLEEP=${IDLE_SLEEP:-60}
while true; do
    python3 -m pipeline.run fetch --max "$BATCH"
    rc=$?
    python3 -m pipeline.run summarize --max "$BATCH"
    python3 -m pipeline.render
    left=$(python3 - <<'EOF'
import sqlite3
c = sqlite3.connect("state/queue.sqlite")
q = c.execute("SELECT COUNT(*) FROM videos WHERE status IN ('queued','fetched') AND attempts<3").fetchone()[0]
print(q)
EOF
)
    echo "[$(date '+%F %T')] pending=$left fetch_rc=$rc"
    if [ "$left" = "0" ]; then
        echo "BACKFILL DONE"
        break
    fi
    if [ "$rc" = "2" ]; then
        echo "Mac asleep; sleeping ${MAC_SLEEP}s"
        sleep "$MAC_SLEEP"
    else
        sleep "$IDLE_SLEEP"
    fi
done

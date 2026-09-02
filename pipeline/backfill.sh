#!/bin/bash
# One-off backfill: repeat hourly passes until nothing is pending in the queue.
# Sleeps and retries while the Mac is asleep. Run detached:
#   nohup setsid bash pipeline/backfill.sh > state/backfill.log 2>&1 &
set -u
cd "$(dirname "$0")/.." || exit 1
MAC_SLEEP=${MAC_SLEEP:-900}
IDLE_SLEEP=${IDLE_SLEEP:-60}
while true; do
    bash pipeline/hourly.sh
    rc=$?
    left=$(python3 - <<'PY'
import sqlite3
c = sqlite3.connect("state/queue.sqlite")
print(c.execute("SELECT COUNT(*) FROM videos WHERE status IN ('queued','fetched') AND attempts<3").fetchone()[0])
PY
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

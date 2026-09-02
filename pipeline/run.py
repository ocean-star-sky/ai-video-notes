"""CLI + sqlite work queue.

python3 -m pipeline.run seed              # catalog videos without a summary -> queued
python3 -m pipeline.run discover          # new uploads on watched channels -> queued (needs Mac)
python3 -m pipeline.run fetch  [--max N]  # queued -> fetched (subtitles via Mac)
python3 -m pipeline.run summarize [--max N]  # fetched -> summarized (Codex/Gemini)
python3 -m pipeline.run status
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from datetime import datetime, timezone

from . import config, remote, summarize
from .catalog import load_json
from .transcript import vtt_to_text

STATUSES = ("queued", "fetched", "summarized", "failed", "skipped")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def open_queue(path=None) -> sqlite3.Connection:
    path = path or config.QUEUE_DB
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE IF NOT EXISTS videos (
            video_id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            channel TEXT NOT NULL,
            status TEXT NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0,
            last_error TEXT,
            first_seen TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )"""
    )
    return conn


def enqueue(
    conn: sqlite3.Connection, video_id: str, title: str, channel: str, first_seen: str
) -> bool:
    cur = conn.execute(
        "INSERT OR IGNORE INTO videos (video_id, title, channel, status, first_seen, updated_at)"
        " VALUES (?, ?, ?, 'queued', ?, ?)",
        (video_id, title, channel, first_seen, _now()),
    )
    conn.commit()
    return cur.rowcount == 1


def set_status(
    conn: sqlite3.Connection, video_id: str, status: str, error: str | None = None
) -> None:
    assert status in STATUSES
    conn.execute(
        "UPDATE videos SET status=?, last_error=?, attempts=attempts+?, updated_at=? WHERE video_id=?",
        (status, error, 1 if status == "failed" else 0, _now(), video_id),
    )
    conn.commit()


def pending(
    conn: sqlite3.Connection, status: str, limit: int, max_attempts: int = 3
) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM videos WHERE status=? AND attempts<? ORDER BY first_seen DESC, video_id LIMIT ?",
        (status, max_attempts, limit),
    ).fetchall()


def transcript_path(video_id: str):
    return config.TRANSCRIPT_DIR / f"{video_id}.txt"


def summary_path(video_id: str):
    return config.SUMMARY_DIR / f"{video_id}.json"


def cmd_seed(conn: sqlite3.Connection) -> int:
    catalog = load_json(config.CATALOG_PATH, [])
    if not catalog:
        print(
            "data/catalog.json missing: run `python3 -m pipeline.catalog --write` first"
        )
        return 1
    added = 0
    for e in catalog:
        if summary_path(e["video_id"]).exists():
            continue
        added += enqueue(conn, e["video_id"], e["title"], e["channel"], e["date"] or "")
    print(f"seeded {added} videos (catalog {len(catalog)})")
    return 0


def cmd_discover(conn: sqlite3.Connection, per_channel: int = 30) -> int:
    added = skipped = 0
    today = datetime.now().strftime("%Y-%m-%d")
    for handle, channel in config.WATCH_CHANNELS.items():
        try:
            videos = remote.list_channel_videos(handle, limit=per_channel)
        except remote.MacUnavailable as e:
            print(f"Mac unavailable ({e}); discover aborted")
            return 2
        except RuntimeError as e:
            print(f"{channel}: {e}")
            continue
        for v in videos:
            if conn.execute(
                "SELECT 1 FROM videos WHERE video_id=?", (v["video_id"],)
            ).fetchone():
                continue
            dur = v.get("duration_sec")
            relevant, _ = config.is_ai_relevant(v["title"], channel)
            if (dur is not None and dur < config.SHORT_MAX_SEC) or not relevant:
                enqueue(conn, v["video_id"], v["title"], channel, today)
                set_status(conn, v["video_id"], "skipped", "short or not AI-related")
                skipped += 1
                continue
            added += enqueue(conn, v["video_id"], v["title"], channel, today)
    print(f"discover: queued {added}, skipped {skipped}")
    return 0


def cmd_fetch(conn: sqlite3.Connection, limit: int) -> int:
    config.TRANSCRIPT_DIR.mkdir(parents=True, exist_ok=True)
    rows = pending(conn, "queued", limit)
    done = 0
    for i, row in enumerate(rows):
        if i:
            time.sleep(config.FETCH_INTERVAL_SEC)
        vid = row["video_id"]
        try:
            got = remote.fetch_subtitles(vid)
        except remote.MacUnavailable as e:
            print(f"Mac unavailable ({e}); {len(rows) - i} left in queue")
            return 2
        except RuntimeError as e:
            set_status(conn, vid, "failed", f"fetch: {e}")
            print(f"  {vid} fetch failed: {e}")
            continue
        if not got:
            set_status(conn, vid, "failed", "no subtitles")
            print(f"  {vid} no subtitles")
            continue
        lang, vtt = got
        text = vtt_to_text(vtt)
        if len(text) < 200:
            set_status(
                conn, vid, "failed", f"transcript too short ({len(text)} chars, {lang})"
            )
            continue
        transcript_path(vid).write_text(f"# lang={lang}\n{text}", encoding="utf-8")
        set_status(conn, vid, "fetched")
        done += 1
        print(f"  {vid} fetched {lang} {len(text)} chars")
    print(f"fetch: {done}/{len(rows)}")
    return 0


def _meta_for(conn: sqlite3.Connection, row: sqlite3.Row) -> dict:
    catalog = {e["video_id"]: e for e in load_json(config.CATALOG_PATH, [])}
    e = catalog.get(row["video_id"], {})
    return {
        "video_id": row["video_id"],
        "title": row["title"],
        "channel": row["channel"],
        "chapters": e.get("chapters", []),
    }


def cmd_summarize(conn: sqlite3.Connection, limit: int) -> int:
    config.SUMMARY_DIR.mkdir(parents=True, exist_ok=True)
    rows = pending(conn, "fetched", limit)
    done = 0
    for row in rows:
        vid = row["video_id"]
        text = transcript_path(vid).read_text(encoding="utf-8")
        lang = text.split("\n", 1)[0].removeprefix("# lang=")
        meta = _meta_for(conn, row)
        t0 = time.time()
        try:
            summary = summarize.summarize_video(meta, text.split("\n", 1)[1])
        except Exception as e:  # LLM/network/validation: keep going, retry next run
            set_status(conn, vid, "failed", f"summarize: {str(e)[:300]}")
            print(f"  {vid} summarize failed: {str(e)[:120]}")
            continue
        summary.update(
            {
                "video_id": vid,
                "title": row["title"],
                "channel": row["channel"],
                "date": row["first_seen"],
                "chapters": meta["chapters"],
                "transcript_lang": lang,
                "generated_at": _now(),
            }
        )
        summary_path(vid).write_text(
            json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        set_status(conn, vid, "summarized")
        done += 1
        print(
            f"  {vid} summarized in {time.time() - t0:.0f}s: {summary['hook_ja'][:60]}"
        )
    print(f"summarize: {done}/{len(rows)}")
    return 0


def cmd_status(conn: sqlite3.Connection) -> int:
    for row in conn.execute(
        "SELECT status, COUNT(*) n FROM videos GROUP BY status ORDER BY status"
    ):
        print(f"{row['status']:>11} {row['n']}")
    for row in conn.execute(
        "SELECT video_id, status, attempts, substr(last_error,1,80) e FROM videos WHERE status='failed' ORDER BY updated_at DESC LIMIT 10"
    ):
        print(f"  failed {row['video_id']} x{row['attempts']}: {row['e']}")
    return 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="pipeline.run")
    ap.add_argument(
        "command", choices=["seed", "discover", "fetch", "summarize", "status"]
    )
    ap.add_argument("--max", type=int, default=None)
    args = ap.parse_args(argv)
    config.load_dotenv()
    conn = open_queue()
    if args.command == "seed":
        return cmd_seed(conn)
    if args.command == "discover":
        return cmd_discover(conn)
    if args.command == "fetch":
        return cmd_fetch(conn, args.max or config.FETCH_BATCH_MAX)
    if args.command == "summarize":
        return cmd_summarize(conn, args.max or config.FETCH_BATCH_MAX)
    return cmd_status(conn)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

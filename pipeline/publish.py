"""Digest + Discord + git publish.

    python3 -m pipeline.publish digest      # print the digest that would be sent (dry run)
    python3 -m pipeline.publish notify      # Discord: new threads / 続報 since the last notification (needs AVN_DISCORD_WEBHOOK)
    python3 -m pipeline.publish push        # git add/commit/push the site to AVN_PUBLISH_BRANCH (default vps-shadow)

Only what changed since the previous notification is announced; the ledger is
state/notified.json so a re-run never repeats a message (the legacy bot re-sent
its "0 本" digest every hour).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone
from urllib.parse import quote

from . import config
from .catalog import load_json

NOTIFIED_PATH = config.STATE_DIR / "notified.json"
SITE_BASE = "https://ocean-star-sky.github.io/ai-video-notes"
DISCORD_LIMIT = 1900


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_notified() -> dict:
    if NOTIFIED_PATH.exists():
        return json.loads(NOTIFIED_PATH.read_text(encoding="utf-8"))
    return {"videos": [], "threads": {}}


def save_notified(state: dict) -> None:
    NOTIFIED_PATH.parent.mkdir(parents=True, exist_ok=True)
    NOTIFIED_PATH.write_text(
        json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8"
    )


def build_digest(
    threads: list[dict], summaries: dict[str, dict], notified: dict
) -> dict:
    """Split unannounced videos into new threads and 続報 on already-announced threads."""
    seen_videos = set(notified.get("videos", []))
    known_threads = notified.get("threads", {})
    new_threads, updates = [], []
    for t in threads:
        members = [m for m in t["members"] if m["video_id"] in summaries]
        fresh = [m for m in members if m["video_id"] not in seen_videos]
        if not fresh:
            continue
        item = {
            "thread_id": t["id"],
            "title_ja": t["title_ja"],
            "summary_ja": t["summary_ja"],
            "latest_ja": t.get("latest_ja", ""),
            "videos": [summaries[m["video_id"]] for m in fresh],
            "total": len(members),
        }
        (updates if t["id"] in known_threads else new_threads).append(item)
    return {"new_threads": new_threads, "updates": updates, "generated_at": _now()}


def _video_line(v: dict, files: dict[str, str]) -> str:
    ch = (v.get("channel") or "").split(" (")[0]
    url = f"{SITE_BASE}/{quote(files[v['video_id']])}" if v["video_id"] in files else ""
    return f"   ▶ [{ch}] {v.get('title_ja')} {url}".rstrip()


def format_digest_text(digest: dict, files: dict[str, str]) -> str:
    n_new, n_upd = len(digest["new_threads"]), len(digest["updates"])
    if not n_new and not n_upd:
        return ""
    lines = [f"📚 AI動画ノート 更新: 新しい話題 {n_new} 件 / 続報 {n_upd} 件"]
    for item in digest["new_threads"]:
        lines.append(f"\n🆕 {item['title_ja']}")
        lines.append(f"   {item['videos'][0].get('hook_ja', '')}")
        lines.extend(_video_line(v, files) for v in item["videos"][:3])
    for item in digest["updates"]:
        lines.append(f"\n🧵 続報: {item['title_ja']}（計 {item['total']} 本）")
        if item["latest_ja"]:
            lines.append(f"   {item['latest_ja']}")
        lines.extend(_video_line(v, files) for v in item["videos"][:3])
    lines.append(f"\n{SITE_BASE}/index.html")
    return "\n".join(lines)


def split_for_discord(text: str, limit: int = DISCORD_LIMIT) -> list[str]:
    """Discord caps a message at 2000 chars; split on blank lines."""
    chunk, chunks = "", []
    for para in text.split("\n\n"):
        if chunk and len(chunk) + len(para) + 2 > limit:
            chunks.append(chunk)
            chunk = ""
        chunk = f"{chunk}\n\n{para}" if chunk else para
    if chunk:
        chunks.append(chunk)
    return chunks


def send_discord(text: str, webhook: str, opener=urllib.request.urlopen) -> int:
    for c in split_for_discord(text):
        req = urllib.request.Request(
            webhook,
            data=json.dumps({"content": c}).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "User-Agent": "ai-video-notes/1.0",
            },
        )
        with opener(req, timeout=30) as resp:
            resp.read()
    return len(split_for_discord(text))


def mark_notified(digest: dict, notified: dict) -> dict:
    for item in digest["new_threads"] + digest["updates"]:
        notified.setdefault("threads", {})[item["thread_id"]] = item["total"]
        for v in item["videos"]:
            if v["video_id"] not in notified.setdefault("videos", []):
                notified["videos"].append(v["video_id"])
    notified["last_sent_at"] = _now()
    return notified


def cmd_digest(send: bool) -> int:
    from .threads import load_summaries, load_threads  # noqa: PLC0415

    notified = load_notified()
    digest = build_digest(load_threads(), load_summaries(), notified)
    files = {
        e["video_id"]: e["canonical_file"] for e in load_json(config.CATALOG_PATH, [])
    }
    text = format_digest_text(digest, files)
    if not text:
        print("nothing new to announce")
        return 0
    print(text)
    if not send:
        return 0
    webhook = os.environ.get("AVN_DISCORD_WEBHOOK")
    if not webhook:
        print("AVN_DISCORD_WEBHOOK not set: not sending, not marking")
        return 0
    send_discord(text, webhook)
    save_notified(mark_notified(digest, notified))
    print(f"sent: {len(digest['new_threads'])} new / {len(digest['updates'])} updates")
    return 0


def cmd_push() -> int:
    branch = os.environ.get("AVN_PUBLISH_BRANCH", "vps-shadow")
    root = config.REPO_ROOT
    cur = subprocess.run(
        ["git", "branch", "--show-current"], cwd=root, capture_output=True, text=True
    ).stdout.strip()
    if cur != branch:
        print(
            f"refusing to push: working tree is on '{cur}', publish branch is '{branch}'"
        )
        return 1
    subprocess.run(["git", "add", "-A", "--", "."], cwd=root, check=True)
    if subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=root).returncode == 0:
        print("nothing to commit")
        return 0
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    subprocess.run(
        ["git", "commit", "-q", "-m", f"site: auto update [{stamp}]"],
        cwd=root,
        check=True,
    )
    r = subprocess.run(
        ["git", "push", "-q", "origin", branch],
        cwd=root,
        capture_output=True,
        text=True,
    )
    if r.returncode:
        print(f"push failed: {r.stderr.strip()[:300]}")
        return 1
    print(f"pushed to {branch}")
    return 0


def main(argv: list[str]) -> int:
    config.load_dotenv()
    cmd = argv[0] if argv else "digest"
    if cmd == "digest":
        return cmd_digest(send=False)
    if cmd == "notify":
        return cmd_digest(send=True)
    if cmd == "push":
        return cmd_push()
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

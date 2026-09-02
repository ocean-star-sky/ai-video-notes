"""Build a video_id-keyed catalog from the note files the legacy bot left behind.

The legacy pipeline wrote one file per *run*, not per video, so the same video
exists 2-5 times (html + md, different dates) and 274 files fell out of
notes_index.json.  The catalog picks one canonical file per video_id, turns the
other copies into redirect stubs (Discord already links to them) and rebuilds
notes_index.json from it.

    python3 -m pipeline.catalog            # dry-run: print statistics
    python3 -m pipeline.catalog --write    # write data/catalog.json, stubs, notes_index.json
"""

from __future__ import annotations

import html
import json
import re
import sys
from pathlib import Path

from . import config

VIDEO_ID_RE = re.compile(r"(?:watch\?v=|/vi/|youtu\.be/|/embed/)([A-Za-z0-9_-]{11})")
H1_RE = re.compile(r"<h1[^>]*>(.*?)</h1>", re.S)
CHANNEL_TAG_RE = re.compile(r'class="channel-tag"[^>]*>(.*?)</span>', re.S)
MD_TITLE_RE = re.compile(r"^#\s*(.+)$", re.M)
MD_CHANNEL_RE = re.compile(r"\*\*チャンネル\*\*:\s*(.+?)\s*$", re.M)
TAG_RE = re.compile(r"<[^>]+>")


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(TAG_RE.sub(" ", text))).strip()


def extract_video_id(text: str) -> str | None:
    m = VIDEO_ID_RE.search(text)
    return m.group(1) if m else None


def parse_note(path: Path) -> dict | None:
    """Return {video_id, title, channel, date, kind, file} or None when no video id is found."""
    name = path.name
    text = path.read_text(encoding="utf-8", errors="replace")
    vid = extract_video_id(text)
    if not vid:
        return None
    m = config.NOTE_FILE_RE.match(name)
    date = m.group(1) if m else None
    if path.suffix == ".html":
        t = H1_RE.search(text)
        c = CHANNEL_TAG_RE.search(text)
        title = _clean(t.group(1)) if t else name
        channel = _clean(c.group(1)).lstrip("📺 ").strip() if c else ""
    else:
        t = MD_TITLE_RE.search(text)
        c = MD_CHANNEL_RE.search(text)
        title = _clean(t.group(1)) if t else name
        title = re.sub(r"^.*?YouTube動画図解ノート[:：]\s*", "", title)
        channel = _clean(c.group(1)) if c else ""
    return {
        "video_id": vid,
        "title": title,
        "channel": channel,
        "date": date,
        "kind": path.suffix.lstrip("."),
        "file": name,
    }


def scan_notes(repo_root: Path) -> list[dict]:
    notes = []
    for p in sorted(repo_root.iterdir()):
        if not p.is_file():
            continue
        if not (
            config.NOTE_FILE_RE.match(p.name) or config.FEATURED_FILE_RE.match(p.name)
        ):
            continue
        parsed = parse_note(p)
        if parsed:
            notes.append(parsed)
    return notes


def _rank(note: dict, indexed_files: set[str]) -> tuple:
    """Higher is better: listed in notes_index > html over md > newer date."""
    return (
        note["file"] in indexed_files,
        note["kind"] == "html",
        note["date"] or "",
        note["file"],
    )


def build_catalog(notes: list[dict], notes_index: list[dict]) -> list[dict]:
    indexed = {
        item.get("filename"): item for item in notes_index if item.get("filename")
    }
    by_vid: dict[str, list[dict]] = {}
    for n in notes:
        by_vid.setdefault(n["video_id"], []).append(n)
    catalog = []
    for vid, group in by_vid.items():
        group = sorted(group, key=lambda n: _rank(n, set(indexed)), reverse=True)
        canon = group[0]
        legacy = indexed.get(canon["file"], {})
        catalog.append(
            {
                "video_id": vid,
                "title": legacy.get("title") or canon["title"],
                "channel": legacy.get("channel") or canon["channel"],
                "date": canon["date"]
                or legacy.get("date")
                or min((n["date"] for n in group if n["date"]), default=""),
                "canonical_file": canon["file"],
                "duplicate_files": [n["file"] for n in group[1:]],
                "tags": legacy.get("tags", []),
                "chapters": legacy.get("chapters", []),
            }
        )
    catalog.sort(key=lambda e: (e["date"], e["video_id"]), reverse=True)
    return catalog


def redirect_stub(target_file: str, kind: str) -> str:
    href = html.escape(target_file, quote=True)
    if kind == "md":
        return f"この動画のノートは統合されました: [{target_file}]({target_file})\n"
    return (
        '<!DOCTYPE html><html lang="ja"><head><meta charset="utf-8">'
        f'<meta http-equiv="refresh" content="0; url={href}">'
        f'<link rel="canonical" href="{href}"><title>移動しました</title></head>'
        f'<body>このノートは統合されました。<a href="{href}">こちら</a>へ移動します。</body></html>\n'
    )


def write_redirect_stubs(catalog: list[dict], repo_root: Path) -> list[str]:
    written = []
    for entry in catalog:
        for dup in entry["duplicate_files"]:
            p = repo_root / dup
            stub = redirect_stub(entry["canonical_file"], p.suffix.lstrip("."))
            if p.read_text(encoding="utf-8", errors="replace") != stub:
                p.write_text(stub, encoding="utf-8")
                written.append(dup)
    return written


def to_notes_index(catalog: list[dict]) -> list[dict]:
    return [
        {
            "video_id": e["video_id"],
            "title": e["title"],
            "channel": e["channel"],
            "filename": e["canonical_file"],
            "date": e["date"],
            "tags": e["tags"],
            "chapters": e["chapters"],
        }
        for e in catalog
    ]


def canonical_filename(video_id: str, channel: str, date: str) -> str:
    """Stable ASCII filename for videos discovered by the VPS pipeline."""
    short = re.sub(r"[^A-Za-z0-9]+", "", (channel or "AI").split(" ")[0]) or "AI"
    return f"{date or '0000-00-00'}_AI_{short}_{video_id}.html"


def sync_new_videos(summaries: dict[str, dict]) -> int:
    """Give every summarized video a catalog entry + notes_index row (idempotent)."""
    catalog = load_json(config.CATALOG_PATH, [])
    known = {e["video_id"] for e in catalog}
    added = 0
    for vid, s in summaries.items():
        if vid in known:
            continue
        catalog.append(
            {
                "video_id": vid,
                "title": s.get("title") or s.get("title_ja") or vid,
                "channel": s.get("channel", ""),
                "date": s.get("date", ""),
                "canonical_file": canonical_filename(
                    vid, s.get("channel", ""), s.get("date", "")
                ),
                "duplicate_files": [],
                "tags": [],
                "chapters": s.get("chapters", []),
            }
        )
        added += 1
    if added:
        catalog.sort(key=lambda e: (e["date"], e["video_id"]), reverse=True)
        config.CATALOG_PATH.write_text(
            json.dumps(catalog, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        config.NOTES_INDEX_PATH.write_text(
            json.dumps(to_notes_index(catalog), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return added


def load_json(path: Path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def main(argv: list[str]) -> int:
    write = "--write" in argv
    repo_root = config.REPO_ROOT
    notes = scan_notes(repo_root)
    notes_index = load_json(config.NOTES_INDEX_PATH, [])
    catalog = build_catalog(notes, notes_index)
    dups = sum(len(e["duplicate_files"]) for e in catalog)
    orphans = len(catalog) - len(
        {i.get("video_id") for i in notes_index} & {e["video_id"] for e in catalog}
    )
    print(
        f"note files={len(notes)} unique videos={len(catalog)} duplicate files={dups} "
        f"videos missing from notes_index={orphans}"
    )
    if not write:
        return 0
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    config.CATALOG_PATH.write_text(
        json.dumps(catalog, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    stubs = write_redirect_stubs(catalog, repo_root)
    config.NOTES_INDEX_PATH.write_text(
        json.dumps(to_notes_index(catalog), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(
        f"wrote {config.CATALOG_PATH.name}, {len(stubs)} redirect stubs, notes_index.json ({len(catalog)} entries)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

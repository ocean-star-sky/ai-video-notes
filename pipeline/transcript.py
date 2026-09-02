"""WebVTT (YouTube auto-subtitles) -> timestamped plain text.

Auto-subtitles repeat each line in two consecutive cues (rolling caption), so a
naive join doubles the text.  We keep a cue line only if it did not appear in
the previous few lines, and prefix a `[mm:ss]` stamp roughly every minute so
the summarizer can cite positions.
"""

from __future__ import annotations

import html
import re

CUE_TIME_RE = re.compile(r"^(\d{1,2}):(\d{2}):(\d{2})[.,](\d{3})\s+-->")
TAG_RE = re.compile(r"<[^>]+>")


def _to_seconds(m: re.Match) -> int:
    h, mi, s = int(m.group(1)), int(m.group(2)), int(m.group(3))
    return h * 3600 + mi * 60 + s


def fmt_stamp(sec: int) -> str:
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def vtt_to_text(vtt: str, stamp_every_sec: int = 60, window: int = 4) -> str:
    """Collapse rolling duplicates and emit `[mm:ss] text ...` paragraphs."""
    paragraphs: list[str] = []
    current: list[str] = []
    recent: list[str] = []
    cue_sec: int | None = None
    next_stamp = 0
    for raw in vtt.splitlines():
        line = raw.strip()
        if (
            not line
            or line == "WEBVTT"
            or line.startswith(("Kind:", "Language:", "NOTE"))
        ):
            continue
        m = CUE_TIME_RE.match(line)
        if m:
            cue_sec = _to_seconds(m)
            continue
        if cue_sec is None:
            continue
        text = html.unescape(TAG_RE.sub("", line)).replace(" ", " ").strip()
        text = re.sub(r"\s+", " ", text)
        if not text or text in recent:
            continue
        recent.append(text)
        del recent[:-window]
        if cue_sec >= next_stamp:
            if current:
                paragraphs.append(" ".join(current))
            current = [f"[{fmt_stamp(cue_sec)}]"]
            next_stamp = (cue_sec // stamp_every_sec + 1) * stamp_every_sec
        current.append(text)
    if current:
        paragraphs.append(" ".join(current))
    return "\n".join(paragraphs)


def chunk_text(text: str, max_chars: int) -> list[str]:
    """Split on paragraph boundaries; each chunk <= max_chars (a single oversize paragraph is kept whole)."""
    chunks: list[str] = []
    buf: list[str] = []
    size = 0
    for para in text.split("\n"):
        if buf and size + len(para) + 1 > max_chars:
            chunks.append("\n".join(buf))
            buf, size = [], 0
        buf.append(para)
        size += len(para) + 1
    if buf:
        chunks.append("\n".join(buf))
    return chunks

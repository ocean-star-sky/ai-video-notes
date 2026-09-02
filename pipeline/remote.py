"""Everything that has to run on the Mac (residential IP): yt-dlp listings and subtitles.

YouTube answers the VPS with `Sign in to confirm you're not a bot` / 429, so the
listing and subtitle steps are executed over ssh on `m1-mac`.  The Mac sleeps;
`MacUnavailable` lets callers leave work in the queue for the next run.
"""

from __future__ import annotations

import shlex
import subprocess
import time

from . import config


class MacUnavailable(RuntimeError):
    """ssh could not reach the Mac (asleep / offline)."""


def _ssh(remote_cmd: str, timeout: int) -> str:
    cmd = [
        "ssh",
        "-o", "BatchMode=yes",
        "-o", f"ConnectTimeout={config.SSH_CONNECT_TIMEOUT}",
        config.MAC_HOST,
        f"export PATH={config.MAC_PATH}:$PATH; {remote_cmd}",
    ]  # fmt: skip
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        raise MacUnavailable(f"ssh timeout after {timeout}s") from e
    if proc.returncode == 255:
        raise MacUnavailable(proc.stderr.strip()[:200] or "ssh exit 255")
    if proc.returncode != 0:
        raise RuntimeError(f"remote rc={proc.returncode}: {proc.stderr.strip()[:300]}")
    return proc.stdout


def parse_flat_playlist(output: str) -> list[dict]:
    """Parse `--print "%(id)s\\t%(title)s\\t%(duration)s\\t%(view_count)s"` lines."""
    videos = []
    for line in output.splitlines():
        parts = line.rstrip("\n").split("\t")
        if len(parts) < 2 or len(parts[0]) != 11:
            continue

        def _num(v: str) -> int | None:
            try:
                return int(float(v))
            except (TypeError, ValueError):
                return None

        videos.append(
            {
                "video_id": parts[0],
                "title": parts[1],
                "duration_sec": _num(parts[2]) if len(parts) > 2 else None,
                "view_count": _num(parts[3]) if len(parts) > 3 else None,
            }
        )
    return videos


def list_channel_videos(handle: str, limit: int = 30, timeout: int = 90) -> list[dict]:
    url = f"https://www.youtube.com/{handle}/videos"
    remote = (
        "yt-dlp --flat-playlist --no-warnings "
        f"--playlist-end {int(limit)} "
        "--print '%(id)s\t%(title)s\t%(duration)s\t%(view_count)s' "
        f"{shlex.quote(url)}"
    )
    return parse_flat_playlist(_ssh(remote, timeout))


def fetch_subtitles(
    video_id: str,
    langs: tuple[str, ...] = config.SUBTITLE_LANGS,
    timeout: int = 120,
    pause_sec: float = 5.0,
) -> tuple[str, str] | None:
    """Return (lang, vtt_text) for the first language that has manual or auto subtitles."""
    url = f"https://www.youtube.com/watch?v={video_id}"
    for i, lang in enumerate(langs):
        if i:
            time.sleep(pause_sec)
        remote = (
            "D=$(mktemp -d /tmp/avn.XXXXXX); "
            "yt-dlp --skip-download --write-sub --write-auto-sub --no-warnings "
            f"--sub-lang {shlex.quote(lang)} --sub-format vtt "
            '-o "$D/%(id)s.%(ext)s" '
            f"{shlex.quote(url)} >/dev/null 2>&1; "
            'cat "$D"/*.vtt 2>/dev/null; rm -rf "$D"'
        )
        out = _ssh(remote, timeout)
        if "WEBVTT" in out[:200]:
            return lang, out
    return None

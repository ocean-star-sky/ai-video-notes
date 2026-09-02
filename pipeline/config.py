"""Paths, channel list and tunables. No secrets here: those come from `.env`."""

from __future__ import annotations

import os
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
STATE_DIR = REPO_ROOT / "state"
TRANSCRIPT_DIR = STATE_DIR / "transcripts"
QUEUE_DB = STATE_DIR / "queue.sqlite"
DATA_DIR = REPO_ROOT / "data"
SUMMARY_DIR = DATA_DIR / "summaries"
CATALOG_PATH = DATA_DIR / "catalog.json"
THREADS_PATH = DATA_DIR / "threads.json"
NOTES_INDEX_PATH = REPO_ROOT / "notes_index.json"

# Note files written by the legacy Mac bot: 2026-08-20_AI_Google_<title>.html / .md
NOTE_FILE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})_.*\.(html|md)$")
FEATURED_FILE_RE = re.compile(r"^FEATURED_.*\.html$")

MAC_HOST = os.environ.get("AVN_MAC_HOST", "m1-mac")
MAC_PATH = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
SSH_CONNECT_TIMEOUT = 10

# yt-dlp against a residential IP still gets 429 when hammered: one video per
# FETCH_INTERVAL_SEC, at most FETCH_BATCH_MAX per run.
FETCH_INTERVAL_SEC = 15
FETCH_BATCH_MAX = 20
SUBTITLE_LANGS = ("en", "ja")
SHORT_MAX_SEC = 90  # anything shorter is treated as a Short/teaser and skipped

# Same watch list as the legacy bot (handle -> display name).
WATCH_CHANNELS: dict[str, str] = {
    "@Tesla": "Tesla (Elon Musk)",
    "@SpaceX": "SpaceX (Elon Musk)",
    "@OpenAI": "OpenAI (Sam Altman)",
    "@GoogleDeepMind": "Google DeepMind (Demis Hassabis)",
    "@Google": "Google (Sundar Pichai)",
    "@NVIDIA": "NVIDIA (Jensen Huang)",
    "@anthropic-ai": "Anthropic (Dario Amodei)",
    "@meta": "Meta (Mark Zuckerberg / Yann LeCun)",
    "@Microsoft": "Microsoft (Satya Nadella / Mustafa Suleyman)",
    "@MicrosoftDeveloper": "Microsoft Developer",
    "@perplexity_ai": "Perplexity AI (Aravind Srinivas)",
    "@Figure_robot": "Figure AI (Brett Adcock / 人型ロボット)",
    "@Scale_AI": "Scale AI (Alexandr Wang)",
    "@TheEconomist": "The Economist",
    "@lexfridman": "Lex Fridman (CEO徹底対談)",
    "@DwarkeshPatel": "Dwarkesh Patel (最深技術対談)",
    "@ycombinator": "Y Combinator (AIスタートアップ)",
}

# Channels whose every upload is in scope; others need a keyword hit in the title.
AI_SPECIALIST_CHANNELS = (
    "OpenAI",
    "Google DeepMind",
    "NVIDIA",
    "Anthropic",
    "Figure AI",
    "Scale AI",
    "Perplexity AI",
    "Y Combinator",
)
AI_KEYWORDS_EN = (
    "ai", "artificial intelligence", "fsd", "full self-driving", "autopilot", "optimus",
    "robot", "robotics", "humanoid", "grok", "xai", "neural", "neuralink", "dojo",
    "supercomputer", "agi", "llm", "compute", "autonomous", "machine learning", "agent",
    "agents", "agentic", "copilot", "gtc", "gemini", "chatgpt", "gpt", "claude",
    "antigravity", "rubin", "blackwell", "spectrum", "semiconductor", "deepseek",
    "deepmind", "prompt", "prompts", "diffusion", "transformer", "transformers", "rag",
    "mcp", "plugin", "plugins", "megapack", "autobidder", "coder", "coding agent", "spec",
    "specs", "vla", "reasoning",
)  # fmt: skip
AI_KEYWORDS_JA = (
    "人工知能", "自動運転", "ロボット", "エージェント", "トークン", "半導体", "推論",
    "基盤モデル", "マルチモーダル", "生成ai", "機械学習",
)  # fmt: skip

# LLM
CODEX_REASONING_EFFORT = "low"
CHUNK_CHARS = 40_000  # ~10k tokens of English subtitles per map call
MAX_CHUNKS = (
    4  # longer transcripts keep head 2 + tail 2 chunks and are flagged truncated
)


def load_dotenv(path: Path = REPO_ROOT / ".env") -> None:
    """Minimal KEY=VALUE loader; never overrides variables already in the environment."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key and key not in os.environ:
            os.environ[key] = value.strip().strip('"').strip("'")


def is_ai_relevant(title: str, channel: str) -> tuple[bool, list[str]]:
    """Port of the legacy bot's relevance rule (word-boundary keyword match)."""
    for name in AI_SPECIALIST_CHANNELS:
        if name.lower() in channel.lower():
            return True, ["AI", "Specialist"]
    text = title.lower()
    matched = [kw for kw in AI_KEYWORDS_JA if kw in text]
    for kw in AI_KEYWORDS_EN:
        if re.search(r"(?<![a-zA-Z0-9])" + re.escape(kw) + r"(?![a-zA-Z0-9])", text):
            matched.append(kw)
    matched = sorted(set(matched))
    return bool(matched), matched

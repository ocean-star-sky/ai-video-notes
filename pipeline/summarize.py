"""Per-video structured summary from the transcript (Codex primary, Gemini fallback).

Output schema (all Japanese unless noted):
    title_ja      動画の日本語タイトル (40字以内)
    hook_ja       この動画だけが言っていることを 1 文 (60字以内)
    takeaways_ja  3 つの持ち帰り
    claims        [{who, claim_ja, at}]  発言者付きの主張、at は "mm:ss"
    entities      固有名詞 (英語表記、製品/組織/人物)
    topic_labels  1-3 個の英語ラベル (例: "agents", "robotics", "compute-power")
    novelty_ja    既知の話題との違い。無ければ ""
    audience_ja   誰が見るべきか 1 文
    truncated     bool (transcript が長すぎて一部しか読んでいない)
"""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Callable

from . import config
from .transcript import chunk_text

REQUIRED_KEYS = {
    "title_ja": str,
    "hook_ja": str,
    "takeaways_ja": list,
    "claims": list,
    "entities": list,
    "topic_labels": list,
    "novelty_ja": str,
    "audience_ja": str,
}

# Phrases the legacy template stamped on every note. Their presence means the
# model is padding instead of reading; the summary is rejected.
BOILERPLATE_PHRASES = (
    "ベストプラクティスまでを網羅",
    "徹底解説（注目領域",
    "このセクションでは",
    "実践的なデモ・実装パターン",
    "基礎概念の整理から",
    "本質の3点抽出",
    "定量的な効果",
)

LLM = Callable[[str], str]


def _json_from_text(text: str) -> dict:
    text = text.strip()
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if m:
        text = m.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("no JSON object in LLM output")
    return json.loads(text[start : end + 1])


def codex_llm(prompt: str) -> str:
    sys.path.insert(0, "/root")
    from lib.codex_client import call_codex_json, get_last_message  # noqa: PLC0415

    events = call_codex_json(
        prompt,
        reasoning_effort=config.CODEX_REASONING_EFFORT,
        gemini_fallback=True,
    )
    return get_last_message(events)


def _meta_block(meta: dict) -> str:
    chapters = meta.get("chapters") or []
    ch = "\n".join(f"- {c}" for c in chapters[:30]) if chapters else "(なし)"
    return (
        f"チャンネル: {meta.get('channel', '')}\n"
        f"元タイトル: {meta.get('title', '')}\n"
        f"公式チャプター:\n{ch}\n"
    )


def build_map_prompt(meta: dict, chunk: str, part: int, total: int) -> str:
    return (
        "あなたはAI業界アナリストです。以下は YouTube 動画の自動字幕の一部"
        f"（{part}/{total}）です。行頭の [mm:ss] は再生位置です。\n"
        "この部分から、具体的な事実・数字・固有名詞・発言者の主張だけを抜き出し、"
        "次の JSON のみを出力してください（前置き・コードフェンス不要）。\n"
        '{"notes_ja": ["具体的な要点(日本語、[mm:ss]付き)", ...最大12件],'
        ' "claims": [{"who": "発言者名(不明なら\\"speaker\\")", "claim_ja": "主張", "at": "mm:ss"}],'
        ' "entities": ["固有名詞(英語表記)"]}\n'
        "一般論や動画に無い話は書かないこと。\n\n"
        f"{_meta_block(meta)}\n=== 字幕 ===\n{chunk}\n"
    )


def build_final_prompt(
    meta: dict, material: str, source_kind: str, problems: list[str] | None = None
) -> str:
    fix = ""
    if problems:
        fix = (
            "前回の出力は次の理由で却下されました。必ず直してください:\n- "
            + "\n- ".join(problems)
            + "\n\n"
        )
    return (
        "あなたはAI業界アナリストです。以下の YouTube 動画の"
        f"{source_kind}を読み、この動画『固有』の内容だけを日本語で要約してください。\n"
        "禁止: 他の動画にも当てはまる一般論、テンプレ的な締め（例:「ベストプラクティスを網羅」）、"
        "字幕に無い主張の捏造。数字・固有名詞・発言者を優先。\n"
        "次の JSON のみを出力（前置き・コードフェンス不要）:\n"
        "{\n"
        '  "title_ja": "動画の日本語タイトル(40字以内、【】は付けない)",\n'
        '  "hook_ja": "この動画だけが言っていることを1文(60字以内)",\n'
        '  "takeaways_ja": ["持ち帰り1", "持ち帰り2", "持ち帰り3"],\n'
        '  "claims": [{"who": "発言者", "claim_ja": "主張(具体的に)", "at": "mm:ss"}],\n'
        '  "entities": ["固有名詞(英語表記)", ...],\n'
        '  "topic_labels": ["英語の短いラベル1-3個 例 agents / robotics / compute-power / ai-safety / policy / product-launch / research / business-strategy"],\n'
        '  "novelty_ja": "AI業界の既知の話題と比べて何が新しいか。無ければ空文字",\n'
        '  "audience_ja": "誰が見るべきか1文"\n'
        "}\n\n"
        f"{fix}{_meta_block(meta)}\n=== {source_kind} ===\n{material}\n"
    )


def validate(summary: dict, meta: dict) -> list[str]:
    problems = []
    for key, typ in REQUIRED_KEYS.items():
        if key not in summary:
            problems.append(f"missing key {key}")
        elif not isinstance(summary[key], typ):
            problems.append(f"{key} must be {typ.__name__}")
    if problems:
        return problems
    if len(summary["takeaways_ja"]) != 3:
        problems.append("takeaways_ja must have exactly 3 items")
    if len(set(map(str, summary["takeaways_ja"]))) != len(summary["takeaways_ja"]):
        problems.append("takeaways_ja contains duplicates")
    if not summary["hook_ja"].strip() or len(summary["hook_ja"]) > 80:
        problems.append("hook_ja empty or longer than 80 chars")
    if not 1 <= len(summary["topic_labels"]) <= 3:
        problems.append("topic_labels must have 1-3 items")
    blob = json.dumps(summary, ensure_ascii=False)
    for phrase in BOILERPLATE_PHRASES:
        if phrase in blob:
            problems.append(f"boilerplate phrase present: {phrase}")
    title = (meta.get("title") or "").strip()
    if title and summary["hook_ja"].strip() == title:
        problems.append("hook_ja merely repeats the title")
    return problems


def _select_chunks(chunks: list[str]) -> tuple[list[str], bool]:
    if len(chunks) <= config.MAX_CHUNKS:
        return chunks, False
    head = config.MAX_CHUNKS // 2
    tail = config.MAX_CHUNKS - head
    return chunks[:head] + chunks[-tail:], True


def summarize_video(meta: dict, transcript: str, llm: LLM = codex_llm) -> dict:
    """Map-reduce over transcript chunks; validate; retry once with the problems listed."""
    chunks, truncated = _select_chunks(chunk_text(transcript, config.CHUNK_CHARS))
    if len(chunks) == 1:
        material, source_kind = chunks[0], "自動字幕"
    else:
        notes = []
        for i, chunk in enumerate(chunks, 1):
            part = _json_from_text(llm(build_map_prompt(meta, chunk, i, len(chunks))))
            notes.append(json.dumps(part, ensure_ascii=False))
        material, source_kind = "\n".join(notes), "分割メモ(JSON)"
    problems: list[str] = []
    summary: dict = {}
    for _attempt in range(2):
        summary = _json_from_text(
            llm(build_final_prompt(meta, material, source_kind, problems))
        )
        problems = validate(summary, meta)
        if not problems:
            break
    if problems:
        raise ValueError("summary rejected: " + "; ".join(problems))
    summary["truncated"] = truncated
    summary["chunks"] = len(chunks)
    return summary

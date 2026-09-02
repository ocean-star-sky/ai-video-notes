"""Group videos into topic threads so the portal shows one story, not N near-identical cards.

Each summarized video gets one Gemini embedding (hook + takeaways + entities).
A new video joins the existing thread whose centroid is closest when
    cos >= THREAD_COS_JOIN, or
    cos >= THREAD_COS_MAYBE and it shares a *specific* entity with the thread
(otherwise it starts a new thread).  Threads with >= 2 members get an LLM
synthesis (what the story is, how the videos differ, what is still open) that
is regenerated only when membership changes.

    python3 -m pipeline.threads assign        # embed new summaries, assign, synthesize, write data/threads.json
    python3 -m pipeline.threads pairs [--top 30]   # calibration: most similar video pairs
    python3 -m pipeline.threads stats
"""

from __future__ import annotations

import json
import math
import os
import re
import sys
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from . import config
from .summarize import _json_from_text, codex_llm

EMBED_MODEL = "gemini-embedding-001"
EMBED_BATCH = 16
EMBED_PAUSE_SEC = 1.0
EMBEDDINGS_PATH = config.STATE_DIR / "embeddings.json"

JUDGE_MODEL = "gemini-2.5-flash"
THREAD_COS_JOIN = 0.90  # >= : same story without asking
THREAD_COS_MAYBE = (
    0.84  # [maybe, join): ask the judge (or entity overlap when no judge)
)

# Entities too generic to count as "the same story" evidence.
GENERIC_ENTITIES = {
    "ai", "agi", "llm", "gpu", "youtube", "openai", "google", "microsoft", "nvidia",
    "anthropic", "meta", "tesla", "amazon", "apple", "deepmind", "google deepmind",
    "claude", "chatgpt", "gemini", "gpt", "gpt-5", "copilot", "github", "azure",
    "x", "twitter", "elon musk", "sam altman", "satya nadella", "jensen huang",
    "dwarkesh patel", "lex fridman", "y combinator", "the economist",
}  # fmt: skip


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def embed_text(summary: dict) -> str:
    return "\n".join(
        [
            summary.get("title_ja", ""),
            summary.get("hook_ja", ""),
            *summary.get("takeaways_ja", []),
            ", ".join(summary.get("entities", [])),
        ]
    )


def _gemini_client():
    sys.path.insert(0, "/root")
    from lib.gemini_logged_client import LoggedClient  # noqa: PLC0415

    config.load_dotenv(Path("/root/.env"))
    return LoggedClient(
        api_key=os.environ["GEMINI_API_KEY"], caller="ai-video-notes.threads"
    )


def gemini_embed(texts: list[str]) -> list[list[float]]:
    """task_type=CLUSTERING separates same-story pairs best (measured 2026-09-02:
    3 ChatGPT Work tutorials 0.88-0.92 vs. every other pair <= 0.88)."""
    from google.genai import types  # noqa: PLC0415

    client = _gemini_client()
    out: list[list[float]] = []
    for i in range(0, len(texts), EMBED_BATCH):
        if i:
            time.sleep(EMBED_PAUSE_SEC)
        r = client.models.embed_content(
            model=EMBED_MODEL,
            contents=texts[i : i + EMBED_BATCH],
            config=types.EmbedContentConfig(task_type="CLUSTERING"),
        )
        out.extend([list(e.values) for e in r.embeddings])
    return out


def _brief(s: dict) -> str:
    return (
        f"『{s.get('title_ja') or s.get('title')}』({s.get('channel')}, {s.get('date')})\n"
        f"  要点: {s.get('hook_ja')}\n  持ち帰り: {' / '.join(s.get('takeaways_ja', []))}"
    )


def build_judge_prompt(new: dict, thread: dict, summaries: dict[str, dict]) -> str:
    members = "\n".join(
        _brief(summaries[m["video_id"]])
        for m in thread["members"][-3:]
        if m["video_id"] in summaries
    )
    return (
        "次の「新しい動画」は、既存の「話題スレッド」の続報・同じ話題（同じ製品/発表/研究テーマ/論点）として"
        "1 つにまとめて読者に見せるべきですか？ 同じ企業や同じ分野というだけなら別扱いにしてください。\n"
        'JSON のみで回答: {"same_topic": true/false, "reason": "20字以内"}\n\n'
        f"[新しい動画]\n{_brief(new)}\n\n[話題スレッド: {thread.get('title_ja')}]\n{members}\n"
    )


def gemini_judge(new: dict, thread: dict, summaries: dict[str, dict]) -> bool:
    """Same-story judge for the ambiguous cosine band (fast, free-tier Flash)."""
    r = _gemini_client().models.generate_content(
        model=JUDGE_MODEL, contents=build_judge_prompt(new, thread, summaries)
    )
    return bool(_json_from_text(r.text).get("same_topic"))


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def centroid(vectors: list[list[float]]) -> list[float]:
    n = len(vectors)
    return [sum(v[i] for v in vectors) / n for i in range(len(vectors[0]))]


def specific_entities(summary: dict) -> set[str]:
    return {
        e.strip().lower()
        for e in summary.get("entities", [])
        if e.strip().lower() not in GENERIC_ENTITIES
    }


def load_embeddings() -> dict[str, list[float]]:
    if EMBEDDINGS_PATH.exists():
        return json.loads(EMBEDDINGS_PATH.read_text(encoding="utf-8"))
    return {}


def save_embeddings(emb: dict[str, list[float]]) -> None:
    EMBEDDINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    EMBEDDINGS_PATH.write_text(json.dumps(emb), encoding="utf-8")


def load_summaries() -> dict[str, dict]:
    out = {}
    for p in sorted(config.SUMMARY_DIR.glob("*.json")):
        s = json.loads(p.read_text(encoding="utf-8"))
        out[s["video_id"]] = s
    return out


def load_threads() -> list[dict]:
    if config.THREADS_PATH.exists():
        return json.loads(config.THREADS_PATH.read_text(encoding="utf-8"))
    return []


def save_threads(threads: list[dict]) -> None:
    config.THREADS_PATH.parent.mkdir(parents=True, exist_ok=True)
    config.THREADS_PATH.write_text(
        json.dumps(threads, ensure_ascii=False, indent=1), encoding="utf-8"
    )


def ensure_embeddings(
    summaries: dict[str, dict], emb: dict[str, list[float]], embed=gemini_embed
) -> int:
    missing = [vid for vid in summaries if vid not in emb]
    if missing:
        vecs = embed([embed_text(summaries[v]) for v in missing])
        for vid, vec in zip(missing, vecs):
            emb[vid] = vec
    return len(missing)


def _thread_id(video_id: str, date: str) -> str:
    return f"t-{date or '0000-00-00'}-{video_id}"


def assign(
    summaries: dict[str, dict],
    emb: dict[str, list[float]],
    threads: list[dict],
    join: float = THREAD_COS_JOIN,
    maybe: float = THREAD_COS_MAYBE,
    judge: Callable[[dict, dict, dict], bool] | None = None,
) -> list[dict]:
    """Attach every not-yet-assigned video to a thread (oldest first, so '続報' is chronological).

    judge(new_summary, thread, summaries) decides the ambiguous band; when it is
    None or fails, a shared *specific* entity is required instead.
    """
    assigned = {m["video_id"] for t in threads for m in t["members"]}
    todo = sorted(
        (s for vid, s in summaries.items() if vid not in assigned and vid in emb),
        key=lambda s: (s.get("date") or "", s["video_id"]),
    )
    for s in todo:
        vid = s["video_id"]
        best, best_cos = None, -1.0
        for t in threads:
            vecs = [emb[m["video_id"]] for m in t["members"] if m["video_id"] in emb]
            if not vecs:
                continue
            c = cosine(emb[vid], centroid(vecs))
            if c > best_cos:
                best, best_cos = t, c
        joined = False
        if best is not None and best_cos >= join:
            joined = True
        elif best is not None and best_cos >= maybe:
            verdict = None
            if judge is not None:
                try:
                    verdict = bool(judge(s, best, summaries))
                except Exception as e:  # judge outage -> fall back to the entity rule
                    print(f"  judge failed for {vid}: {str(e)[:100]}")
            if verdict is None:
                verdict = bool(specific_entities(s) & set(best.get("entities", [])))
            joined = verdict
        member = {
            "video_id": vid,
            "date": s.get("date", ""),
            "cos": round(best_cos, 3) if joined else None,
        }
        if joined:
            best["members"].append(member)
            best["entities"] = sorted(
                set(best.get("entities", [])) | specific_entities(s)
            )
            best["updated_at"] = _now()
        else:
            threads.append(
                {
                    "id": _thread_id(vid, s.get("date", "")),
                    "title_ja": s.get("title_ja") or s.get("title", vid),
                    "summary_ja": s.get("hook_ja", ""),
                    "latest_ja": "",
                    "members": [member],
                    "entities": sorted(specific_entities(s)),
                    "synthesized_for": 1,
                    "created_at": _now(),
                    "updated_at": _now(),
                }
            )
    return threads


def build_synthesis_prompt(thread: dict, summaries: dict[str, dict]) -> str:
    parts = []
    for m in sorted(thread["members"], key=lambda m: m["date"]):
        s = summaries[m["video_id"]]
        claims = "; ".join(
            f"{c.get('who')}: {c.get('claim_ja')}"
            for c in s.get("claims", [])[:4]
            if isinstance(c, dict)
        )
        parts.append(
            f"- [{s.get('date')}] {s.get('channel')}『{s.get('title_ja') or s.get('title')}』\n"
            f"  要点: {s.get('hook_ja')}\n  持ち帰り: {' / '.join(s.get('takeaways_ja', []))}\n  主張: {claims}"
        )
    return (
        "あなたはAI業界アナリストです。以下は同じ話題を扱う複数の動画の要約です（古い順）。"
        "この話題を初めて見る読者向けに、次の JSON のみを日本語で出力してください（前置き・コードフェンス不要）:\n"
        "{\n"
        '  "title_ja": "話題の名前(30字以内、動画タイトルの写しでなく話題そのもの)",\n'
        '  "summary_ja": "150〜250字。何の話題か／各動画・各社の主張がどう違うか／まだ決着していない点",\n'
        '  "latest_ja": "最新の動画で何が新しく加わったか1文"\n'
        "}\n"
        "禁止: 動画に無い一般論、テンプレ的な締め。\n\n" + "\n".join(parts) + "\n"
    )


def synthesize(
    threads: list[dict],
    summaries: dict[str, dict],
    llm: Callable[[str], str] = codex_llm,
) -> int:
    done = 0
    for t in threads:
        n = len(t["members"])
        if n < 2 or t.get("synthesized_for") == n:
            continue
        try:
            out = _json_from_text(llm(build_synthesis_prompt(t, summaries)))
        except Exception as e:  # keep the old synthesis; retry next run
            print(f"  synthesis failed for {t['id']}: {str(e)[:120]}")
            continue
        if not isinstance(out.get("summary_ja"), str) or not out["summary_ja"].strip():
            print(f"  synthesis rejected for {t['id']}: empty summary")
            continue
        t["title_ja"] = (out.get("title_ja") or t["title_ja"]).strip()
        t["summary_ja"] = out["summary_ja"].strip()
        t["latest_ja"] = (out.get("latest_ja") or "").strip()
        t["synthesized_for"] = n
        t["updated_at"] = _now()
        done += 1
    return done


def top_pairs(
    summaries: dict[str, dict], emb: dict[str, list[float]], top: int = 30
) -> list[tuple[float, str, str]]:
    vids = [v for v in summaries if v in emb]
    pairs = []
    for i, a in enumerate(vids):
        for b in vids[i + 1 :]:
            pairs.append((cosine(emb[a], emb[b]), a, b))
    pairs.sort(reverse=True)
    return pairs[:top]


def cmd_assign(embed=gemini_embed, llm=codex_llm, judge=gemini_judge) -> int:
    summaries = load_summaries()
    emb = load_embeddings()
    n_new = ensure_embeddings(summaries, emb, embed)
    if n_new:
        save_embeddings(emb)
    threads = assign(summaries, emb, load_threads(), judge=judge)
    n_syn = synthesize(threads, summaries, llm)
    save_threads(threads)
    multi = sum(1 for t in threads if len(t["members"]) > 1)
    print(
        f"threads: embedded {n_new} new, {len(threads)} threads ({multi} with 2+ videos), synthesized {n_syn}"
    )
    return 0


def cmd_pairs(top: int) -> int:
    summaries = load_summaries()
    emb = load_embeddings()
    for c, a, b in top_pairs(summaries, emb, top):
        ta = summaries[a].get("title_ja") or a
        tb = summaries[b].get("title_ja") or b
        print(f"{c:.3f} | {ta[:34]} | {tb[:34]}")
    return 0


def cmd_stats() -> int:
    threads = load_threads()
    sizes = sorted((len(t["members"]) for t in threads), reverse=True)
    print(f"threads={len(threads)} videos={sum(sizes)} sizes={sizes[:15]}")
    for t in sorted(threads, key=lambda t: -len(t["members"]))[:10]:
        print(f"  {len(t['members']):>2} {t['title_ja'][:40]}")
    return 0


def main(argv: list[str]) -> int:
    cmd = argv[0] if argv else "stats"
    if cmd == "assign":
        return cmd_assign()
    if cmd == "pairs":
        m = re.search(r"--top\s+(\d+)", " ".join(argv))
        return cmd_pairs(int(m.group(1)) if m else 30)
    return cmd_stats()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

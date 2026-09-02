"""Static site rendering: one note per video (canonical filename kept) + index.html.

Both pages are self-contained (inline CSS/JS, no CDN) like the legacy site, and
the portal keeps the legacy localStorage keys so stars / read marks / memos
survive the migration.

    python3 -m pipeline.render        # writes notes for every summary + index.html
"""

from __future__ import annotations

import html
import json
import re
import sys
from collections import Counter
from pathlib import Path

from . import config
from .catalog import load_json

SITE_TITLE = "AI Video Intelligence ポータル"
STORAGE_KEYS = {
    "starred": "ai_video_starred_vids",
    "read": "ai_video_read_vids",
    "memos": "ai_video_user_memos",
}

PALETTE_CSS = """
:root{--bg:#0b0f19;--card:#1e293b;--text:#f8fafc;--sub:#94a3b8;--accent:#38bdf8;--orange:#fb923c;--green:#4ade80;--border:#334155;--deep:#131d31}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Hiragino Sans","Hiragino Kaku Gothic ProN",Meiryo,sans-serif;background:var(--bg);color:var(--text);line-height:1.8;padding:32px 16px}
a{color:var(--accent)}
.container{max-width:960px;margin:0 auto}
.nav-back{display:inline-block;color:var(--accent);text-decoration:none;font-weight:600;margin-bottom:18px}
.card{background:var(--card);border:1px solid var(--border);border-radius:14px;padding:22px 26px;margin-bottom:20px}
.chip{display:inline-block;background:rgba(56,189,248,.15);color:var(--accent);padding:2px 12px;border-radius:20px;font-size:.8rem;font-weight:600;margin:0 6px 6px 0}
.chip.label{background:rgba(251,146,60,.15);color:var(--orange)}
.sub{color:var(--sub);font-size:.9rem}
h1{font-size:1.7rem;line-height:1.4;margin:10px 0}
h2{font-size:1.1rem;color:var(--accent);border-bottom:1px solid var(--border);padding-bottom:8px;margin-bottom:14px}
"""

NOTE_CSS = (
    PALETTE_CSS
    + """
header{background:linear-gradient(135deg,#1e293b,#0f172a);border:1px solid var(--border);border-radius:16px;padding:28px;margin-bottom:24px}
.hook{font-size:1.15rem;font-weight:700;line-height:1.7;border-left:4px solid var(--accent);padding:14px 18px;background:rgba(15,23,42,.7);border-radius:10px}
ol.takeaways{padding-left:1.4em}ol.takeaways li{margin-bottom:10px}
table{width:100%;border-collapse:collapse;font-size:.95rem}th,td{border-bottom:1px solid var(--border);padding:8px 10px;text-align:left;vertical-align:top}th{color:var(--sub);font-weight:600}
td.at a{white-space:nowrap}
.thumb{display:block;position:relative;border-radius:12px;overflow:hidden;border:1px solid var(--border);background:#000}
.thumb img{width:100%;max-height:420px;object-fit:cover;display:block;opacity:.85}
.thumb .play{position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);background:rgba(239,68,68,.9);color:#fff;width:64px;height:46px;border-radius:12px;display:flex;align-items:center;justify-content:center;font-size:22px}
.notice{background:rgba(251,146,60,.12);border:1px solid rgba(251,146,60,.4);color:var(--orange);border-radius:10px;padding:10px 14px;font-size:.9rem;margin-bottom:16px}
footer{color:var(--sub);font-size:.8rem;text-align:center;margin-top:30px}
"""
)

INDEX_CSS = (
    PALETTE_CSS
    + """
.container{max-width:1150px}
header.top{text-align:center;margin-bottom:24px}
header.top h1{font-size:2.1rem;background:linear-gradient(135deg,#38bdf8,#818cf8);-webkit-background-clip:text;-webkit-text-fill-color:transparent}
.toolbar{display:flex;flex-wrap:wrap;gap:10px;align-items:center;margin-bottom:16px}
.toolbar input{flex:1;min-width:240px;background:var(--deep);color:var(--text);border:1px solid var(--border);border-radius:10px;padding:10px 14px;font-size:1rem}
.btn{background:rgba(255,255,255,.08);color:#cbd5e1;border:1px solid #475569;padding:6px 14px;border-radius:9px;font-size:.85rem;cursor:pointer}
.btn.active{background:#6366f1;color:#fff;border-color:#6366f1;font-weight:700}
.topics{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:18px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:18px}
.pcard{background:var(--card);border:1px solid var(--border);border-radius:14px;overflow:hidden;display:flex;flex-direction:column}
.pcard[hidden]{display:none}
.pcard .thumbc{position:relative}.pcard img{width:100%;aspect-ratio:16/9;object-fit:cover;display:block}
.pcard .tb{position:absolute;top:8px;background:rgba(15,23,42,.85);border:1px solid var(--border);border-radius:8px;padding:2px 8px;cursor:pointer;font-size:.95rem}
.pcard .star{right:8px}.pcard .read{right:44px}.pcard .tb.on{background:#6366f1;border-color:#6366f1}
.pcard .body{padding:14px 16px;display:flex;flex-direction:column;gap:8px;flex:1}
.pcard .ch{color:var(--sub);font-size:.78rem}
.pcard h3{font-size:1rem;line-height:1.45}.pcard h3 a{color:var(--text);text-decoration:none}
.pcard .hook{color:#cbd5e1;font-size:.9rem;line-height:1.6}
.pcard .meta{display:flex;justify-content:space-between;align-items:center;color:var(--sub);font-size:.78rem;margin-top:auto}
.pcard .memo{font-size:.8rem;color:var(--green);white-space:pre-wrap}
.pending{color:var(--orange);font-size:.8rem}
.count{color:var(--sub);font-size:.85rem;margin:6px 0 14px}
.modal{position:fixed;inset:0;background:rgba(0,0,0,.6);display:none;align-items:center;justify-content:center;padding:20px}
.modal.open{display:flex}.modal .box{background:var(--card);border:1px solid var(--border);border-radius:14px;padding:20px;width:min(560px,100%)}
.modal textarea{width:100%;min-height:120px;background:var(--deep);color:var(--text);border:1px solid var(--border);border-radius:8px;padding:10px;font-size:.95rem}
"""
)

TS_RE = re.compile(r"^(?:(\d{1,2}):)?(\d{1,2}):(\d{2})$")


def _e(s: object) -> str:
    return html.escape(str(s or ""), quote=True)


def ts_to_seconds(ts: str) -> int | None:
    m = TS_RE.match((ts or "").strip())
    if not m:
        return None
    h, mi, s = m.groups()
    return int(h or 0) * 3600 + int(mi) * 60 + int(s)


def yt_url(video_id: str, ts: str | None = None) -> str:
    sec = ts_to_seconds(ts) if ts else None
    base = f"https://www.youtube.com/watch?v={video_id}"
    return f"{base}&t={sec}s" if sec is not None else base


def _claims_table(video_id: str, claims: list) -> str:
    rows = []
    for c in claims:
        if not isinstance(c, dict):
            continue
        at = str(c.get("at") or "")
        link = (
            f'<a href="{_e(yt_url(video_id, at))}" target="_blank" rel="noopener">{_e(at)}</a>'
            if ts_to_seconds(at) is not None
            else _e(at)
        )
        rows.append(
            f'<tr><td>{_e(c.get("who"))}</td><td>{_e(c.get("claim_ja"))}</td><td class="at">{link}</td></tr>'
        )
    if not rows:
        return ""
    return (
        '<div class="card"><h2>🗣️ 誰が何を言ったか</h2><table><thead><tr><th>発言者</th><th>主張</th><th>位置</th></tr></thead>'
        f"<tbody>{''.join(rows)}</tbody></table></div>"
    )


def render_note(summary: dict, entry: dict) -> str:
    vid = summary["video_id"]
    title_ja = (
        summary.get("title_ja") or entry.get("title") or summary.get("title") or vid
    )
    original = summary.get("title") or entry.get("title") or ""
    chips = "".join(
        f'<span class="chip label">{_e(t)}</span>'
        for t in summary.get("topic_labels", [])
    )
    ents = "".join(
        f'<span class="chip">{_e(t)}</span>' for t in summary.get("entities", [])[:20]
    )
    takeaways = "".join(f"<li>{_e(t)}</li>" for t in summary.get("takeaways_ja", []))
    chapters = summary.get("chapters") or entry.get("chapters") or []
    chapters_html = (
        '<div class="card"><h2>⏱️ 公式チャプター</h2><ol>'
        + "".join(f"<li>{_e(c)}</li>" for c in chapters)
        + "</ol></div>"
        if chapters
        else ""
    )
    notice = (
        '<div class="notice">⚠️ 字幕が長いため、冒頭と終盤を中心に要約しています（中盤は一部未読）。</div>'
        if summary.get("truncated")
        else ""
    )
    novelty = summary.get("novelty_ja") or ""
    novelty_html = (
        f'<div class="card"><h2>🆕 何が新しいか</h2><p>{_e(novelty)}</p></div>'
        if novelty.strip()
        else ""
    )
    lang = summary.get("transcript_lang", "en")
    generated = (summary.get("generated_at") or "")[:10]
    return f"""<!DOCTYPE html>
<html lang="ja"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{_e(title_ja)} | {SITE_TITLE}</title>
<style>{NOTE_CSS}</style></head>
<body><div class="container">
<a href="index.html" class="nav-back">← 📚 AI動画ポータル（一覧）へ戻る</a>
<header>
<span class="chip">📺 {_e(summary.get("channel") or entry.get("channel"))}</span>
<h1>{_e(title_ja)}</h1>
<div class="sub">元タイトル: {_e(original)} ／ 📅 {_e(summary.get("date") or entry.get("date"))}</div>
<div style="margin-top:10px">{chips}</div>
</header>
{notice}
<div class="card"><h2>🎯 この動画だけが言っていること</h2><div class="hook">{_e(summary.get("hook_ja"))}</div></div>
<div class="card"><h2>📌 持ち帰り 3 点</h2><ol class="takeaways">{takeaways}</ol></div>
<div class="card"><h2>🎥 公式動画</h2>
<a class="thumb" href="{_e(yt_url(vid))}" target="_blank" rel="noopener"><img src="https://img.youtube.com/vi/{_e(vid)}/hqdefault.jpg" alt="{_e(title_ja)}" loading="lazy"><div class="play">▶</div></a>
</div>
{_claims_table(vid, summary.get("claims", []))}
{novelty_html}
<div class="card"><h2>👤 誰が見るべきか</h2><p>{_e(summary.get("audience_ja"))}</p></div>
{f'<div class="card"><h2>🏷️ 登場する固有名詞</h2>{ents}</div>' if ents else ""}
{chapters_html}
<footer>自動字幕（{_e(lang)}）を LLM が読んで生成 ／ {_e(generated)} ／ 生成物は動画の一次情報ではありません。数字・固有名は動画で確認してください。</footer>
</div></body></html>
"""


def _card(entry: dict, summary: dict | None) -> str:
    vid = entry["video_id"]
    title = (summary or {}).get("title_ja") or entry["title"]
    hook = (summary or {}).get("hook_ja") or ""
    labels = (summary or {}).get("topic_labels") or []
    search_blob = " ".join(
        [title, entry["title"], entry["channel"], hook]
        + list((summary or {}).get("takeaways_ja") or [])
        + list((summary or {}).get("entities") or [])
        + labels
    ).lower()
    label_chips = "".join(
        f'<span class="chip label" data-topic="{_e(t.lower())}">{_e(t)}</span>'
        for t in labels
    )
    body = (
        f'<div class="hook">{_e(hook)}</div>'
        if hook
        else '<div class="pending">要約を準備中（字幕取得待ち）</div>'
    )
    return f"""<div class="pcard" data-id="{_e(vid)}" data-topics="{_e(" ".join(t.lower() for t in labels))}" data-search="{_e(search_blob)}">
<div class="thumbc"><a href="{_e(entry["canonical_file"])}"><img src="https://img.youtube.com/vi/{_e(vid)}/hqdefault.jpg" alt="" loading="lazy"></a>
<button class="tb star" data-act="star" title="マイストック">⭐</button><button class="tb read" data-act="read" title="読了">✅</button></div>
<div class="body"><div class="ch">{_e(entry["channel"])}</div><h3><a href="{_e(entry["canonical_file"])}">{_e(title)}</a></h3>{body}
<div>{label_chips}</div>
<div class="meta"><span>📅 {_e(entry["date"])}</span><button class="btn" data-act="memo">📝 メモ</button></div>
<div class="memo" data-memo></div></div></div>"""


def render_index(catalog: list[dict], summaries: dict[str, dict]) -> str:
    entries = sorted(
        catalog, key=lambda e: (e.get("date") or "", e["video_id"]), reverse=True
    )
    counts = Counter(
        t.lower()
        for e in entries
        for t in (summaries.get(e["video_id"]) or {}).get("topic_labels") or []
    )
    topics = "".join(
        f'<button class="btn topic" data-topic="{_e(t)}">{_e(t)} <span class="sub">{n}</span></button>'
        for t, n in counts.most_common(14)
    )
    cards = "\n".join(_card(e, summaries.get(e["video_id"])) for e in entries)
    lib = [
        {
            "video_id": e["video_id"],
            "title": (summaries.get(e["video_id"]) or {}).get("title_ja") or e["title"],
            "channel": e["channel"],
            "date": e["date"],
            "filename": e["canonical_file"],
            "hook": (summaries.get(e["video_id"]) or {}).get("hook_ja", ""),
        }
        for e in entries
    ]
    done = sum(1 for e in entries if e["video_id"] in summaries)
    return f"""<!DOCTYPE html>
<html lang="ja"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>📚 {SITE_TITLE}</title>
<style>{INDEX_CSS}</style></head>
<body><div class="container">
<header class="top"><h1>📚 {SITE_TITLE}</h1><div class="sub">主要 AI 企業・対談チャンネルの新着動画を、字幕から 1 本ずつ要約。同じ話題は同じラベルで束ねます。</div></header>
<div class="toolbar">
<input id="q" type="search" placeholder="🔍 タイトル・要約・固有名詞で検索（例: Gemini Robotics, 電力, Copilot）">
<button class="btn tab active" data-tab="all">📚 全動画 ({len(entries)})</button>
<button class="btn tab" data-tab="star">⭐ マイストック (<span id="nstar">0</span>)</button>
<button class="btn tab" data-tab="read">✅ 読了 (<span id="nread">0</span>)</button>
<button class="btn" id="export">📋 マイストックを Markdown コピー</button>
</div>
<div class="topics"><button class="btn topic active" data-topic="">🌐 すべて</button>{topics}</div>
<div class="count"><span id="shown">{len(entries)}</span> 本を表示 ／ 要約済み {done} / {len(entries)}</div>
<div class="grid" id="grid">
{cards}
</div>
<div class="modal" id="modal"><div class="box"><h2 id="mtitle">📝 メモ</h2><textarea id="mtext"></textarea>
<div style="margin-top:10px;display:flex;gap:8px;justify-content:flex-end"><button class="btn" id="mcancel">閉じる</button><button class="btn active" id="msave">保存</button></div></div></div>
</div>
<script>
const LIB={json.dumps(lib, ensure_ascii=False)};
const KEYS={json.dumps(STORAGE_KEYS)};
const load=k=>{{try{{return JSON.parse(localStorage.getItem(k))||(k===KEYS.memos?{{}}:[])}}catch(e){{return k===KEYS.memos?{{}}:[]}}}};
const save=(k,v)=>{{try{{localStorage.setItem(k,JSON.stringify(v))}}catch(e){{}}}};
let stars=load(KEYS.starred),reads=load(KEYS.read),memos=load(KEYS.memos);
let tab='all',topic='',q='',memoVid=null;
const cards=[...document.querySelectorAll('.pcard')];
function paint(){{
  let shown=0;
  for(const c of cards){{
    const id=c.dataset.id;
    c.querySelector('.star').classList.toggle('on',stars.includes(id));
    c.querySelector('.read').classList.toggle('on',reads.includes(id));
    c.querySelector('[data-memo]').textContent=memos[id]?'💡 '+memos[id]:'';
    const okTab=tab==='all'||(tab==='star'&&stars.includes(id))||(tab==='read'&&reads.includes(id));
    const okTopic=!topic||c.dataset.topics.split(' ').includes(topic);
    const okQ=!q||q.split(/\\s+/).every(w=>c.dataset.search.includes(w));
    const show=okTab&&okTopic&&okQ; c.hidden=!show; if(show)shown++;
  }}
  document.getElementById('shown').textContent=shown;
  document.getElementById('nstar').textContent=stars.length;
  document.getElementById('nread').textContent=reads.length;
}}
document.getElementById('grid').addEventListener('click',e=>{{
  const b=e.target.closest('[data-act]'); if(!b)return; e.preventDefault();
  const id=b.closest('.pcard').dataset.id;
  if(b.dataset.act==='star'){{stars=stars.includes(id)?stars.filter(x=>x!==id):[...stars,id];save(KEYS.starred,stars)}}
  if(b.dataset.act==='read'){{reads=reads.includes(id)?reads.filter(x=>x!==id):[...reads,id];save(KEYS.read,reads)}}
  if(b.dataset.act==='memo'){{memoVid=id;document.getElementById('mtext').value=memos[id]||'';document.getElementById('modal').classList.add('open')}}
  paint();
}});
document.getElementById('msave').onclick=()=>{{const t=document.getElementById('mtext').value.trim();if(t)memos[memoVid]=t;else delete memos[memoVid];save(KEYS.memos,memos);document.getElementById('modal').classList.remove('open');paint()}};
document.getElementById('mcancel').onclick=()=>document.getElementById('modal').classList.remove('open');
document.querySelectorAll('.tab').forEach(b=>b.onclick=()=>{{tab=b.dataset.tab;document.querySelectorAll('.tab').forEach(x=>x.classList.toggle('active',x===b));paint()}});
document.querySelectorAll('.topic').forEach(b=>b.onclick=()=>{{topic=b.dataset.topic;document.querySelectorAll('.topic').forEach(x=>x.classList.toggle('active',x===b));paint()}});
document.getElementById('q').addEventListener('input',e=>{{q=e.target.value.trim().toLowerCase();paint()}});
document.getElementById('export').onclick=()=>{{
  const items=LIB.filter(i=>stars.includes(i.video_id));
  if(!items.length){{alert('⭐ マイストックが空です。カードの ⭐ を押して追加してください。');return}}
  let md='# 🌟 マイAIインテリジェンス・ストック ('+items.length+'件)\\n\\n生成日: '+new Date().toLocaleDateString('ja-JP')+'\\n\\n';
  items.forEach((i,n)=>{{md+='### '+(n+1)+'. ['+i.title+'](https://ocean-star-sky.github.io/ai-video-notes/'+encodeURIComponent(i.filename)+')\\n- チャンネル: '+i.channel+'\\n- 公開日: '+i.date+'\\n- 要点: '+i.hook+'\\n- YouTube: https://www.youtube.com/watch?v='+i.video_id+'\\n'+(memos[i.video_id]?'- 💡 メモ: '+memos[i.video_id]+'\\n':'')+'\\n'}});
  navigator.clipboard.writeText(md).then(()=>alert('📋 '+items.length+' 件をコピーしました'));
}};
paint();
</script>
</body></html>
"""


def build_site(repo_root: Path = config.REPO_ROOT) -> dict:
    catalog = load_json(config.CATALOG_PATH, [])
    summaries: dict[str, dict] = {}
    for p in sorted(config.SUMMARY_DIR.glob("*.json")):
        s = json.loads(p.read_text(encoding="utf-8"))
        summaries[s["video_id"]] = s
    by_vid = {e["video_id"]: e for e in catalog}
    written = 0
    for vid, s in summaries.items():
        entry = by_vid.get(vid)
        if not entry:
            continue
        out = repo_root / entry["canonical_file"]
        page = render_note(s, entry)
        if not out.exists() or out.read_text(encoding="utf-8") != page:
            out.write_text(page, encoding="utf-8")
            written += 1
    index = render_index(catalog, summaries)
    index_path = repo_root / "index.html"
    if not index_path.exists() or index_path.read_text(encoding="utf-8") != index:
        index_path.write_text(index, encoding="utf-8")
    return {
        "videos": len(catalog),
        "summaries": len(summaries),
        "notes_written": written,
    }


def main(argv: list[str]) -> int:
    print(build_site())
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

import json

from pipeline import config, render

ENTRY = {
    "video_id": "8-boBsWcr5A",
    "title": "Satya Nadella – How Microsoft thinks about AGI",
    "channel": "Dwarkesh Patel (最深技術対談)",
    "date": "2026-08-20",
    "canonical_file": "2026-08-20_AI_Dwarkesh_Satya.html",
    "duplicate_files": [],
    "tags": [],
    "chapters": ["Intro", "AGI"],
}
SUMMARY = {
    "video_id": "8-boBsWcr5A",
    "title": ENTRY["title"],
    "channel": ENTRY["channel"],
    "date": "2026-08-20",
    "title_ja": "ナデラが語る AGI と <Microsoft> の戦略",
    "hook_ja": "AGI は製品でなく電力のような基盤になると断言",
    "takeaways_ja": ["一つ目", "二つ目 & 補足", "三つ目"],
    "claims": [
        {
            "who": "Satya Nadella",
            "claim_ja": "推論コストは年 10 倍下がる",
            "at": "12:34",
        },
        {"who": "Dylan Patel", "claim_ja": "電力が制約", "at": "1:02:03"},
        {"who": "?", "claim_ja": "位置不明", "at": "n/a"},
    ],
    "entities": ["Microsoft", "Azure"],
    "topic_labels": ["Business-Strategy", "compute-power"],
    "novelty_ja": "",
    "audience_ja": "経営層",
    "truncated": True,
    "transcript_lang": "en",
    "generated_at": "2026-09-02T10:00:00+00:00",
}


def test_ts_to_seconds():
    assert render.ts_to_seconds("12:34") == 754
    assert render.ts_to_seconds("1:02:03") == 3723
    assert render.ts_to_seconds("n/a") is None
    assert render.yt_url("abc", "0:05") == "https://www.youtube.com/watch?v=abc&t=5s"


def test_render_note_escapes_and_links_timestamps():
    page = render.render_note(SUMMARY, ENTRY)
    assert "&lt;Microsoft&gt;" in page and "<Microsoft>" not in page
    assert "二つ目 &amp; 補足" in page
    assert 'href="https://www.youtube.com/watch?v=8-boBsWcr5A&amp;t=754s"' in page
    assert "t=3723s" in page
    assert "位置不明" in page and "t=None" not in page
    assert "冒頭と終盤を中心に" in page  # truncated notice
    assert "何が新しいか" not in page  # empty novelty section omitted
    assert "<li>Intro</li>" in page
    assert "このセクションでは" not in page and "ベストプラクティス" not in page


def test_render_index_cards_topics_and_pending(tmp_path):
    other = dict(
        ENTRY,
        video_id="bbbbbbbbbbb",
        title="Pending video",
        canonical_file="b.html",
        date="2026-09-01",
    )
    page = render.render_index([ENTRY, other], {ENTRY["video_id"]: SUMMARY})
    assert page.count('class="pcard"') == 2
    assert page.index('data-id="bbbbbbbbbbb"') < page.index(
        'data-id="8-boBsWcr5A"'
    )  # newest first
    assert "要約を準備中" in page
    assert (
        'data-topic="business-strategy"' in page
        and 'data-topic="compute-power"' in page
    )
    assert "ai_video_starred_vids" in page and "ai_video_user_memos" in page
    assert "要約済み 1 / 2" in page
    assert "microsoft" in page  # search blob includes entities lowercased


def test_build_site_writes_canonical_note_and_index(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CATALOG_PATH", tmp_path / "catalog.json")
    monkeypatch.setattr(config, "SUMMARY_DIR", tmp_path / "s")
    (tmp_path / "s").mkdir()
    (tmp_path / "catalog.json").write_text(json.dumps([ENTRY]), encoding="utf-8")
    (tmp_path / "s" / "8-boBsWcr5A.json").write_text(
        json.dumps(SUMMARY, ensure_ascii=False), encoding="utf-8"
    )
    stats = render.build_site(tmp_path)
    assert stats == {"videos": 1, "summaries": 1, "notes_written": 1}
    assert (tmp_path / ENTRY["canonical_file"]).exists() and (
        tmp_path / "index.html"
    ).exists()
    assert render.build_site(tmp_path)["notes_written"] == 0  # idempotent

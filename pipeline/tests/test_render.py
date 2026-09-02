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
    assert "話題スレッド（" not in page  # no multi-video threads -> section omitted

    threads = [
        {
            "id": "t-2026-08-20-8-boBsWcr5A",
            "title_ja": "MS の AGI 投資 <続報>",
            "summary_ja": "統合文",
            "latest_ja": "最新で加わった点",
            "members": [
                {"video_id": "8-boBsWcr5A", "date": "2026-08-20"},
                {"video_id": "bbbbbbbbbbb", "date": "2026-09-01"},
            ],
        },
        {
            "id": "solo",
            "title_ja": "単独",
            "summary_ja": "h",
            "latest_ja": "",
            "members": [{"video_id": "zzzzzzzzzzz", "date": "2026-01-01"}],
        },
    ]
    page = render.render_index([ENTRY, other], {ENTRY["video_id"]: SUMMARY}, threads)
    assert page.count('class="thread"') == 1  # single-member threads are not listed
    assert (
        "MS の AGI 投資 &lt;続報&gt;" in page
        and "統合文" in page
        and "🆕 最新で加わった点" in page
    )
    assert 'data-thread-filter="t-2026-08-20-8-boBsWcr5A"' in page
    assert (
        page.count('data-thread="t-2026-08-20-8-boBsWcr5A"') == 3
    )  # section + 2 cards
    assert "話題スレッド 2" in page


def test_build_site_adds_catalog_entry_for_discovered_video(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CATALOG_PATH", tmp_path / "catalog.json")
    monkeypatch.setattr(config, "NOTES_INDEX_PATH", tmp_path / "notes_index.json")
    monkeypatch.setattr(config, "SUMMARY_DIR", tmp_path / "s")
    (tmp_path / "s").mkdir()
    (tmp_path / "catalog.json").write_text("[]", encoding="utf-8")
    new = dict(
        SUMMARY,
        video_id="newvid12345",
        channel="Google DeepMind (Demis Hassabis)",
        date="2026-09-03",
    )
    (tmp_path / "s" / "newvid12345.json").write_text(
        json.dumps(new, ensure_ascii=False), encoding="utf-8"
    )
    stats = render.build_site(tmp_path)
    assert stats["videos"] == 1 and stats["notes_written"] == 1
    cat = json.loads((tmp_path / "catalog.json").read_text(encoding="utf-8"))
    assert cat[0]["canonical_file"] == "2026-09-03_AI_Google_newvid12345.html"
    assert (tmp_path / "2026-09-03_AI_Google_newvid12345.html").exists()
    idx = json.loads((tmp_path / "notes_index.json").read_text(encoding="utf-8"))
    assert (
        idx[0]["video_id"] == "newvid12345"
        and idx[0]["filename"] == cat[0]["canonical_file"]
    )
    assert (
        render.build_site(tmp_path)["notes_written"] == 0
    )  # idempotent, no duplicate entry
    assert len(json.loads((tmp_path / "catalog.json").read_text(encoding="utf-8"))) == 1


def test_build_site_writes_canonical_note_and_index(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CATALOG_PATH", tmp_path / "catalog.json")
    monkeypatch.setattr(config, "NOTES_INDEX_PATH", tmp_path / "notes_index.json")
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

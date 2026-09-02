from pathlib import Path

from pipeline import catalog

HTML = """<html><body><span class="channel-tag">📺 {ch}</span><h1>{title}</h1>
<a href="https://www.youtube.com/watch?v={vid}">play</a></body></html>"""
MD = """# 🤖 【AI特化】YouTube動画図解ノート：{title}
> * **チャンネル**: {ch}
> * **動画URL**: [x](https://www.youtube.com/watch?v={vid})
"""


def _write(tmp: Path, name: str, body: str) -> Path:
    p = tmp / name
    p.write_text(body, encoding="utf-8")
    return p


def test_parse_html_and_md(tmp_path: Path):
    h = _write(
        tmp_path,
        "2026-08-20_AI_Google_x.html",
        HTML.format(ch="Google (Sundar)", title="T &amp; U", vid="abcdefghijk"),
    )
    m = _write(
        tmp_path,
        "2026-08-19_AI_Tesla_y.md",
        MD.format(ch="Tesla (Elon Musk)", title="Home", vid="ABCDEFGHIJ1"),
    )
    ph, pm = catalog.parse_note(h), catalog.parse_note(m)
    assert ph == {
        "video_id": "abcdefghijk",
        "title": "T & U",
        "channel": "Google (Sundar)",
        "date": "2026-08-20",
        "kind": "html",
        "file": h.name,
    }
    assert (
        pm["video_id"] == "ABCDEFGHIJ1"
        and pm["title"] == "Home"
        and pm["channel"] == "Tesla (Elon Musk)"
        and pm["kind"] == "md"
    )


def test_build_catalog_prefers_indexed_then_html_then_newest(tmp_path: Path):
    vid = "abcdefghijk"
    _write(
        tmp_path, "2026-06-23_AI_Tesla_a.md", MD.format(ch="Tesla", title="A", vid=vid)
    )
    _write(
        tmp_path,
        "2026-08-19_AI_Tesla_a.html",
        HTML.format(ch="Tesla", title="A", vid=vid),
    )
    _write(
        tmp_path,
        "2026-08-25_AI_Tesla_a.html",
        HTML.format(ch="Tesla", title="A", vid=vid),
    )
    _write(
        tmp_path,
        "2026-08-25_AI_Google_b.html",
        HTML.format(ch="Google", title="B", vid="ABCDEFGHIJ1"),
    )
    _write(tmp_path, "README.md", "not a note")
    notes = catalog.scan_notes(tmp_path)
    assert len(notes) == 4

    cat = catalog.build_catalog(notes, notes_index=[])
    assert len(cat) == 2
    a = next(e for e in cat if e["video_id"] == vid)
    assert a["canonical_file"] == "2026-08-25_AI_Tesla_a.html"
    assert set(a["duplicate_files"]) == {
        "2026-08-19_AI_Tesla_a.html",
        "2026-06-23_AI_Tesla_a.md",
    }

    idx = [
        {
            "video_id": vid,
            "filename": "2026-08-19_AI_Tesla_a.html",
            "title": "【テスラ】A",
            "channel": "Tesla",
            "date": "2026-08-19",
            "tags": ["AI"],
            "chapters": ["c1"],
        }
    ]
    cat = catalog.build_catalog(notes, notes_index=idx)
    a = next(e for e in cat if e["video_id"] == vid)
    assert a["canonical_file"] == "2026-08-19_AI_Tesla_a.html"
    assert (
        a["title"] == "【テスラ】A" and a["tags"] == ["AI"] and a["chapters"] == ["c1"]
    )
    # a video the legacy index lost is still cataloged
    assert {e["video_id"] for e in cat} == {vid, "ABCDEFGHIJ1"}


def test_redirect_stubs_and_notes_index_roundtrip(tmp_path: Path):
    vid = "abcdefghijk"
    _write(
        tmp_path,
        "2026-08-19_AI_Tesla_a.html",
        HTML.format(ch="Tesla", title="A", vid=vid),
    )
    _write(
        tmp_path,
        "2026-08-25_AI_Tesla_a.html",
        HTML.format(ch="Tesla", title="A", vid=vid),
    )
    _write(
        tmp_path, "2026-06-23_AI_Tesla_a.md", MD.format(ch="Tesla", title="A", vid=vid)
    )
    cat = catalog.build_catalog(catalog.scan_notes(tmp_path), [])
    written = catalog.write_redirect_stubs(cat, tmp_path)
    assert sorted(written) == ["2026-06-23_AI_Tesla_a.md", "2026-08-19_AI_Tesla_a.html"]
    stub = (tmp_path / "2026-08-19_AI_Tesla_a.html").read_text(encoding="utf-8")
    assert 'http-equiv="refresh"' in stub and "2026-08-25_AI_Tesla_a.html" in stub
    assert "2026-08-25_AI_Tesla_a.html" in (
        tmp_path / "2026-06-23_AI_Tesla_a.md"
    ).read_text(encoding="utf-8")
    # idempotent
    assert catalog.write_redirect_stubs(cat, tmp_path) == []
    # stubs no longer count as notes for that video (they still contain the id, so canonical must win)
    cat2 = catalog.build_catalog(
        catalog.scan_notes(tmp_path), catalog.to_notes_index(cat)
    )
    assert cat2[0]["canonical_file"] == "2026-08-25_AI_Tesla_a.html"
    idx = catalog.to_notes_index(cat)
    assert idx[0]["filename"] == "2026-08-25_AI_Tesla_a.html" and set(idx[0]) == {
        "video_id",
        "title",
        "channel",
        "filename",
        "date",
        "tags",
        "chapters",
    }

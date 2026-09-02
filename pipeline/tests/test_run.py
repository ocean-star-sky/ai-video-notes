import json

from pipeline import config, remote, run


def _setup(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "QUEUE_DB", tmp_path / "q.sqlite")
    monkeypatch.setattr(config, "TRANSCRIPT_DIR", tmp_path / "t")
    monkeypatch.setattr(config, "SUMMARY_DIR", tmp_path / "s")
    monkeypatch.setattr(config, "CATALOG_PATH", tmp_path / "catalog.json")
    monkeypatch.setattr(config, "FETCH_INTERVAL_SEC", 0)
    (tmp_path / "s").mkdir()
    cat = [
        {
            "video_id": "aaaaaaaaaaa",
            "title": "A",
            "channel": "OpenAI (Sam Altman)",
            "date": "2026-08-20",
            "chapters": ["c1"],
        },
        {
            "video_id": "bbbbbbbbbbb",
            "title": "B",
            "channel": "Tesla (Elon Musk)",
            "date": "2026-08-21",
            "chapters": [],
        },
    ]
    (tmp_path / "catalog.json").write_text(json.dumps(cat), encoding="utf-8")
    return run.open_queue()


def test_seed_skips_videos_that_already_have_a_summary(tmp_path, monkeypatch, capsys):
    conn = _setup(tmp_path, monkeypatch)
    (tmp_path / "s" / "bbbbbbbbbbb.json").write_text("{}", encoding="utf-8")
    assert run.cmd_seed(conn) == 0
    assert [r["video_id"] for r in run.pending(conn, "queued", 10)] == ["aaaaaaaaaaa"]
    assert run.cmd_seed(conn) == 0  # idempotent
    assert "seeded 0" in capsys.readouterr().out


def test_fetch_writes_transcript_and_marks_failures(tmp_path, monkeypatch):
    conn = _setup(tmp_path, monkeypatch)
    run.cmd_seed(conn)
    vtt_ok = (
        "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\n"
        + "hello world this is a long enough transcript line. " * 10
    )
    calls = []

    def fake_fetch(vid, **kw):
        calls.append(vid)
        return ("en", vtt_ok) if vid == "bbbbbbbbbbb" else None

    monkeypatch.setattr(remote, "fetch_subtitles", fake_fetch)
    assert run.cmd_fetch(conn, limit=10) == 0
    assert sorted(calls) == ["aaaaaaaaaaa", "bbbbbbbbbbb"]
    assert (
        (tmp_path / "t" / "bbbbbbbbbbb.txt")
        .read_text(encoding="utf-8")
        .startswith("# lang=en\n[00:01] hello")
    )
    rows = {r["video_id"]: r for r in conn.execute("SELECT * FROM videos")}
    assert rows["bbbbbbbbbbb"]["status"] == "fetched"
    assert (
        rows["aaaaaaaaaaa"]["status"] == "failed"
        and rows["aaaaaaaaaaa"]["attempts"] == 1
    )
    # a failed row is retried on the next run until max_attempts
    assert [r["video_id"] for r in run.pending(conn, "queued", 10)] == []


def test_fetch_leaves_queue_untouched_when_mac_is_asleep(tmp_path, monkeypatch):
    conn = _setup(tmp_path, monkeypatch)
    run.cmd_seed(conn)

    def asleep(vid, **kw):
        raise remote.MacUnavailable("timeout")

    monkeypatch.setattr(remote, "fetch_subtitles", asleep)
    assert run.cmd_fetch(conn, limit=10) == 2
    assert len(run.pending(conn, "queued", 10)) == 2


def test_summarize_writes_json_with_catalog_chapters(tmp_path, monkeypatch):
    conn = _setup(tmp_path, monkeypatch)
    run.cmd_seed(conn)
    (tmp_path / "t").mkdir()
    (tmp_path / "t" / "aaaaaaaaaaa.txt").write_text(
        "# lang=en\n[00:00] hi", encoding="utf-8"
    )
    run.set_status(conn, "aaaaaaaaaaa", "fetched")
    seen = {}

    def fake_summarize(meta, transcript, llm=None):
        seen.update(meta=meta, transcript=transcript)
        return {"hook_ja": "H", "takeaways_ja": ["1", "2", "3"], "title_ja": "T"}

    monkeypatch.setattr(run.summarize, "summarize_video", fake_summarize)
    assert run.cmd_summarize(conn, limit=10) == 0
    out = json.loads((tmp_path / "s" / "aaaaaaaaaaa.json").read_text(encoding="utf-8"))
    assert seen["meta"]["chapters"] == ["c1"] and seen["transcript"] == "[00:00] hi"
    assert (
        out["video_id"] == "aaaaaaaaaaa"
        and out["transcript_lang"] == "en"
        and out["date"] == "2026-08-20"
    )
    assert (
        conn.execute(
            "SELECT status FROM videos WHERE video_id='aaaaaaaaaaa'"
        ).fetchone()[0]
        == "summarized"
    )

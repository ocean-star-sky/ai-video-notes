import json

from pipeline import publish

S = {
    "aaaaaaaaaaa": {
        "video_id": "aaaaaaaaaaa",
        "channel": "OpenAI (Sam Altman)",
        "title_ja": "A動画",
        "hook_ja": "Aの要点",
    },
    "bbbbbbbbbbb": {
        "video_id": "bbbbbbbbbbb",
        "channel": "OpenAI (Sam Altman)",
        "title_ja": "B動画",
        "hook_ja": "Bの要点",
    },
    "ccccccccccc": {
        "video_id": "ccccccccccc",
        "channel": "Tesla (Elon Musk)",
        "title_ja": "C動画",
        "hook_ja": "Cの要点",
    },
}
T = [
    {
        "id": "t1",
        "title_ja": "ChatGPT Work",
        "summary_ja": "統合",
        "latest_ja": "Bが加わった",
        "members": [
            {"video_id": "aaaaaaaaaaa", "date": "2026-08-20"},
            {"video_id": "bbbbbbbbbbb", "date": "2026-08-21"},
        ],
    },
    {
        "id": "t2",
        "title_ja": "Tesla単独",
        "summary_ja": "Cの要点",
        "latest_ja": "",
        "members": [{"video_id": "ccccccccccc", "date": "2026-08-22"}],
    },
]
FILES = {"aaaaaaaaaaa": "a b.html", "bbbbbbbbbbb": "b.html", "ccccccccccc": "c.html"}


def test_digest_first_run_announces_everything_as_new_then_only_deltas():
    d = publish.build_digest(T, S, {"videos": [], "threads": {}})
    assert [i["thread_id"] for i in d["new_threads"]] == ["t1", "t2"] and d[
        "updates"
    ] == []
    text = publish.format_digest_text(d, FILES)
    assert (
        "新しい話題 2 件 / 続報 0 件" in text
        and "a%20b.html" in text
        and "[OpenAI] A動画" in text
    )

    notified = publish.mark_notified(d, {"videos": [], "threads": {}})
    assert sorted(notified["videos"]) == [
        "aaaaaaaaaaa",
        "bbbbbbbbbbb",
        "ccccccccccc",
    ] and notified["threads"] == {"t1": 2, "t2": 1}
    assert (
        publish.format_digest_text(publish.build_digest(T, S, notified), FILES) == ""
    )  # nothing new -> silent

    T[0]["members"].append({"video_id": "ddddddddddd", "date": "2026-08-23"})
    S["ddddddddddd"] = {
        "video_id": "ddddddddddd",
        "channel": "OpenAI (Sam Altman)",
        "title_ja": "D動画",
        "hook_ja": "Dの要点",
    }
    d2 = publish.build_digest(T, S, notified)
    assert d2["new_threads"] == [] and [i["thread_id"] for i in d2["updates"]] == ["t1"]
    assert d2["updates"][0]["total"] == 3 and [
        v["video_id"] for v in d2["updates"][0]["videos"]
    ] == ["ddddddddddd"]
    text2 = publish.format_digest_text(d2, FILES)
    assert (
        "続報: ChatGPT Work（計 3 本）" in text2
        and "Bが加わった" in text2
        and "D動画" in text2
    )


def test_discord_split_and_send():
    text = "\n\n".join(f"para {i} " + "x" * 500 for i in range(6))
    chunks = publish.split_for_discord(text, limit=1200)
    assert (
        len(chunks) == 3
        and all(len(c) <= 1200 for c in chunks)
        and "\n\n".join(chunks) == text
    )
    sent = []

    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b""

    def opener(req, timeout):
        sent.append(json.loads(req.data)["content"])
        return Resp()

    assert (
        publish.send_discord(
            "hello\n\nworld", "https://example.invalid/hook", opener=opener
        )
        == 1
    )
    assert sent == ["hello\n\nworld"]

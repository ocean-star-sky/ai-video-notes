import json
import math

from pipeline import config, threads


def _s(vid, date, title, hook, entities, takeaways=("a", "b", "c")):
    return {
        "video_id": vid,
        "date": date,
        "channel": "Google DeepMind (Demis Hassabis)",
        "title": title,
        "title_ja": title,
        "hook_ja": hook,
        "takeaways_ja": list(takeaways),
        "entities": entities,
        "claims": [{"who": "x", "claim_ja": "y", "at": "0:10"}],
    }


def _unit(*xs):
    n = math.sqrt(sum(x * x for x in xs))
    return [x / n for x in xs]


def test_assign_joins_by_cosine_and_specific_entity():
    summaries = {
        "aaaaaaaaaaa": _s(
            "aaaaaaaaaaa",
            "2026-08-19",
            "Robots working together",
            "複数ロボット協調",
            ["Google DeepMind", "Gemini Robotics 2"],
        ),
        "bbbbbbbbbbb": _s(
            "bbbbbbbbbbb",
            "2026-08-20",
            "Multi-robot collaboration",
            "続報: 協調の詳細",
            ["Gemini Robotics 2", "Google"],
        ),
        "ccccccccccc": _s(
            "ccccccccccc",
            "2026-08-20",
            "Why Russia lost the cold war",
            "冷戦の話",
            ["Sarah Paine", "Soviet Union"],
        ),
        "ddddddddddd": _s(
            "ddddddddddd",
            "2026-08-21",
            "Whole body control",
            "全身制御",
            ["Gemini Robotics 2"],
        ),
    }
    emb = {
        "aaaaaaaaaaa": _unit(1, 0, 0),
        "bbbbbbbbbbb": _unit(1, 0.15, 0),  # cos ~0.99 with a -> joins outright
        "ccccccccccc": _unit(0, 1, 0),  # unrelated -> new thread
        "ddddddddddd": _unit(
            1, 0.5, 0
        ),  # cos ~0.9 with the a/b centroid: below JOIN, above MAYBE, shares an entity
    }
    out = threads.assign(summaries, emb, [], join=0.95, maybe=0.85)
    assert len(out) == 2
    robot = next(t for t in out if t["members"][0]["video_id"] == "aaaaaaaaaaa")
    assert [m["video_id"] for m in robot["members"]] == [
        "aaaaaaaaaaa",
        "bbbbbbbbbbb",
        "ddddddddddd",
    ]
    assert robot["members"][0]["cos"] is None and robot["members"][1]["cos"] > 0.95
    assert (
        "gemini robotics 2" in robot["entities"] and "google" not in robot["entities"]
    )
    cold = next(t for t in out if t["members"][0]["video_id"] == "ccccccccccc")
    assert cold["summary_ja"] == "冷戦の話" and cold["synthesized_for"] == 1

    # without a shared specific entity the "maybe" band does NOT merge
    summaries["ddddddddddd"]["entities"] = ["Unrelated Corp"]
    out2 = threads.assign(summaries, dict(emb), [], join=0.95, maybe=0.85)
    assert len(out2) == 3

    # idempotent: already assigned videos are not re-added
    assert threads.assign(summaries, emb, out, join=0.95, maybe=0.85) is out
    assert sum(len(t["members"]) for t in out) == 4


def test_assign_uses_judge_in_the_ambiguous_band_and_falls_back_on_error():
    summaries = {
        "aaaaaaaaaaa": _s("aaaaaaaaaaa", "2026-08-19", "A", "h", ["Thing One"]),
        "ddddddddddd": _s("ddddddddddd", "2026-08-21", "D", "h", ["Other"]),
    }
    emb = {"aaaaaaaaaaa": _unit(1, 0, 0), "ddddddddddd": _unit(1, 0.5, 0)}  # cos ~0.89
    seen = []

    def yes(new, thread, all_s):
        seen.append((new["video_id"], thread["members"][0]["video_id"]))
        return True

    out = threads.assign(summaries, dict(emb), [], join=0.95, maybe=0.85, judge=yes)
    assert len(out) == 1 and seen == [("ddddddddddd", "aaaaaaaaaaa")]
    out = threads.assign(
        summaries, dict(emb), [], join=0.95, maybe=0.85, judge=lambda *a: False
    )
    assert len(out) == 2  # judge says different story despite the cosine

    def broken(*a):
        raise RuntimeError("quota")

    out = threads.assign(summaries, dict(emb), [], join=0.95, maybe=0.85, judge=broken)
    assert len(out) == 2  # no shared specific entity -> entity fallback says no
    summaries["ddddddddddd"]["entities"] = ["Thing One"]
    out = threads.assign(summaries, dict(emb), [], join=0.95, maybe=0.85, judge=broken)
    assert len(out) == 1  # fallback rule joins on the shared entity
    # above JOIN the judge is never consulted
    calls = []
    threads.assign(
        summaries,
        {"aaaaaaaaaaa": _unit(1, 0, 0), "ddddddddddd": _unit(1, 0.05, 0)},
        [],
        join=0.95,
        maybe=0.85,
        judge=lambda *a: calls.append(1) or True,
    )
    assert calls == []


def test_judge_prompt_mentions_both_sides():
    s = {"aaaaaaaaaaa": _s("aaaaaaaaaaa", "2026-08-19", "既存タイトル", "h1", ["E"])}
    new = _s("bbbbbbbbbbb", "2026-08-20", "新タイトル", "h2", ["E"])
    thread = {
        "title_ja": "スレッド名",
        "members": [{"video_id": "aaaaaaaaaaa", "date": "2026-08-19"}],
    }
    p = threads.build_judge_prompt(new, thread, s)
    assert "『新タイトル』" in p and "『既存タイトル』" in p and "same_topic" in p


def test_synthesize_only_multi_member_threads_and_only_when_membership_changed():
    summaries = {
        "aaaaaaaaaaa": _s("aaaaaaaaaaa", "2026-08-19", "A", "h1", ["E"]),
        "bbbbbbbbbbb": _s("bbbbbbbbbbb", "2026-08-20", "B", "h2", ["E"]),
        "ccccccccccc": _s("ccccccccccc", "2026-08-20", "C", "h3", ["F"]),
    }
    th = [
        {
            "id": "t1",
            "title_ja": "A",
            "summary_ja": "h1",
            "latest_ja": "",
            "entities": ["e"],
            "synthesized_for": 1,
            "members": [
                {"video_id": "aaaaaaaaaaa", "date": "2026-08-19"},
                {"video_id": "bbbbbbbbbbb", "date": "2026-08-20"},
            ],
        },
        {
            "id": "t2",
            "title_ja": "C",
            "summary_ja": "h3",
            "latest_ja": "",
            "entities": ["f"],
            "synthesized_for": 1,
            "members": [{"video_id": "ccccccccccc", "date": "2026-08-20"}],
        },
    ]
    prompts = []

    def llm(p):
        prompts.append(p)
        return json.dumps(
            {
                "title_ja": "ロボット協調の進展",
                "summary_ja": "統合文",
                "latest_ja": "Bで加わった",
            },
            ensure_ascii=False,
        )

    assert threads.synthesize(th, summaries, llm) == 1
    assert len(prompts) == 1 and "『A』" in prompts[0] and "『B』" in prompts[0]
    assert (
        th[0]["title_ja"] == "ロボット協調の進展"
        and th[0]["synthesized_for"] == 2
        and th[0]["latest_ja"] == "Bで加わった"
    )
    assert th[1]["summary_ja"] == "h3"  # single-member thread untouched
    assert (
        threads.synthesize(th, summaries, llm) == 0 and len(prompts) == 1
    )  # unchanged membership -> no call

    def bad(p):
        return "{}"

    th[0]["members"].append({"video_id": "ccccccccccc", "date": "2026-08-20"})
    assert threads.synthesize(th, summaries, bad) == 0
    assert (
        th[0]["summary_ja"] == "統合文" and th[0]["synthesized_for"] == 2
    )  # rejected output keeps the old text


def test_cmd_assign_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SUMMARY_DIR", tmp_path / "s")
    monkeypatch.setattr(config, "THREADS_PATH", tmp_path / "threads.json")
    monkeypatch.setattr(threads, "EMBEDDINGS_PATH", tmp_path / "emb.json")
    (tmp_path / "s").mkdir()
    for vid in ("aaaaaaaaaaa", "bbbbbbbbbbb"):
        (tmp_path / "s" / f"{vid}.json").write_text(
            json.dumps(_s(vid, "2026-08-20", vid, "h", ["Same Thing"])),
            encoding="utf-8",
        )
    calls = []

    def embed(texts):
        calls.append(len(texts))
        return [_unit(1, 0.01 * i) for i, _ in enumerate(texts)]

    assert (
        threads.cmd_assign(
            embed=embed,
            llm=lambda p: json.dumps(
                {"summary_ja": "S", "title_ja": "T", "latest_ja": "L"}
            ),
        )
        == 0
    )
    saved = json.loads((tmp_path / "threads.json").read_text(encoding="utf-8"))
    assert (
        len(saved) == 1
        and len(saved[0]["members"]) == 2
        and saved[0]["summary_ja"] == "S"
    )
    assert calls == [2]
    assert threads.cmd_assign(embed=embed, llm=lambda p: "{}") == 0
    assert calls == [2]  # embeddings cached; nothing new to embed

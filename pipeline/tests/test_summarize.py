import json

import pytest

from pipeline import config, summarize

META = {
    "title": "Satya Nadella – How Microsoft thinks about AGI",
    "channel": "Dwarkesh Patel",
    "chapters": [],
}


def good(**over):
    s = {
        "title_ja": "サティア・ナデラが語るAGI",
        "hook_ja": "MicrosoftはAGIを製品でなく電力と同じ基盤として扱うと明言",
        "takeaways_ja": ["a", "b", "c"],
        "claims": [{"who": "Satya Nadella", "claim_ja": "x", "at": "12:34"}],
        "entities": ["Microsoft"],
        "topic_labels": ["business-strategy"],
        "novelty_ja": "",
        "audience_ja": "経営層",
    }
    s.update(over)
    return s


def test_validate_accepts_good_and_rejects_template_output():
    assert summarize.validate(good(), META) == []
    bad = good(takeaways_ja=["a", "a", "b"], hook_ja=META["title"])
    problems = summarize.validate(bad, META)
    assert any("duplicates" in p for p in problems) and any(
        "repeats the title" in p for p in problems
    )
    assert summarize.validate(good(novelty_ja="基礎概念の整理から実践まで"), META) == [
        "boilerplate phrase present: 基礎概念の整理から"
    ]
    assert summarize.validate({"hook_ja": "x"}, META)[0].startswith("missing key")
    assert "takeaways_ja must be list" in summarize.validate(
        good(takeaways_ja="a"), META
    )


def test_summarize_single_chunk_retries_once_with_problems():
    calls = []

    def llm(prompt):
        calls.append(prompt)
        if len(calls) == 1:
            return (
                "```json\n"
                + json.dumps(good(takeaways_ja=["a", "a", "a"]), ensure_ascii=False)
                + "\n```"
            )
        return json.dumps(good(), ensure_ascii=False)

    out = summarize.summarize_video(META, "[00:00] short transcript", llm=llm)
    assert (
        out["hook_ja"].startswith("Microsoft")
        and out["truncated"] is False
        and out["chunks"] == 1
    )
    assert len(calls) == 2 and "却下されました" in calls[1] and "duplicates" in calls[1]


def test_summarize_gives_up_after_second_bad_answer():
    def llm(prompt):
        return json.dumps(good(takeaways_ja=["a"]))

    with pytest.raises(ValueError, match="exactly 3"):
        summarize.summarize_video(META, "[00:00] t", llm=llm)


def test_map_reduce_keeps_head_and_tail_and_flags_truncated(monkeypatch):
    monkeypatch.setattr(config, "CHUNK_CHARS", 30)
    monkeypatch.setattr(config, "MAX_CHUNKS", 2)
    transcript = "\n".join(
        f"[{i:02d}:00] paragraph number {i} xxxxxxxx" for i in range(6)
    )
    seen = []

    def llm(prompt):
        seen.append(prompt)
        if "分割メモ" in prompt:
            return json.dumps(good())
        return json.dumps({"notes_ja": ["n"], "claims": [], "entities": ["E"]})

    out = summarize.summarize_video(META, transcript, llm=llm)
    assert out["truncated"] is True and out["chunks"] == 2
    map_prompts = [p for p in seen if "分割メモ" not in p]
    assert len(map_prompts) == 2
    assert (
        "paragraph number 0" in map_prompts[0]
        and "paragraph number 5" in map_prompts[1]
    )
    assert "paragraph number 3" not in "".join(map_prompts)

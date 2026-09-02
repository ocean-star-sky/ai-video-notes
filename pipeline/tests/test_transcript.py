from pipeline.transcript import chunk_text, vtt_to_text

VTT = """WEBVTT
Kind: captions
Language: en

00:00:00.000 --> 00:00:02.500 align:start position:0%
Today we are interviewing Satya&nbsp;<c>Nadella</c>

00:00:02.500 --> 00:00:04.000 align:start position:0%
Today we are interviewing Satya Nadella
"We" being me and Dylan Patel

00:00:04.000 --> 00:00:06.000
"We" being me and Dylan Patel
founder of SemiAnalysis.

00:01:05.000 --> 00:01:07.000
Thank you for coming.

00:02:10.000 --> 00:02:12.000
Let's start with AGI.
"""


def test_vtt_dedupes_rolling_lines_and_stamps_every_minute():
    text = vtt_to_text(VTT, stamp_every_sec=60)
    lines = text.split("\n")
    assert (
        lines[0]
        == '[00:00] Today we are interviewing Satya Nadella "We" being me and Dylan Patel founder of SemiAnalysis.'
    )
    assert lines[1] == "[01:05] Thank you for coming."
    assert lines[2] == "[02:10] Let's start with AGI."
    assert text.count("interviewing") == 1
    assert "<c>" not in text and " " not in text


def test_chunk_text_splits_on_paragraphs():
    text = "\n".join(["a" * 10] * 10)
    chunks = chunk_text(text, max_chars=35)
    assert len(chunks) == 4
    assert all(len(c) <= 35 for c in chunks[:-1])
    assert "\n".join(chunks) == text
    assert chunk_text("x" * 100, max_chars=10) == ["x" * 100]

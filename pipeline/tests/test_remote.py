from pipeline.remote import parse_flat_playlist


def test_parse_flat_playlist_handles_na_and_garbage():
    out = "S6TIVzqTmu8\tMagic Manga Girl (:30)\t31.0\tNA\nyfnh8SuMbNw\tUse ChatGPT Work\t115.0\t12345\n[download] junk\n\n"
    vids = parse_flat_playlist(out)
    assert vids == [
        {
            "video_id": "S6TIVzqTmu8",
            "title": "Magic Manga Girl (:30)",
            "duration_sec": 31,
            "view_count": None,
        },
        {
            "video_id": "yfnh8SuMbNw",
            "title": "Use ChatGPT Work",
            "duration_sec": 115,
            "view_count": 12345,
        },
    ]

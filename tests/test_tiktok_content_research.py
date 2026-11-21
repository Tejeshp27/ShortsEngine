# =============================================================
# tests/test_tiktok_content_research.py — checks for
#   src/clips/tiktok_content_research.py
#
# Uses small made-up transcripts, picture numbers and sound levels so
# every measure can be checked by hand. No network, no real data needed.
# =============================================================

import json

from src.clips import tiktok_content_research as content


def test_load_rows_skips_errors_and_broken_lines(tmp_path):
    path = tmp_path / "rows.jsonl"
    path.write_text("\n".join([
        json.dumps({"id": "1", "value": 1}),
        "not json",
        json.dumps({"id": "2", "error": "no item"}),
        json.dumps({"id": "1", "value": 2}),
        "",
    ]))
    rows = content.load_rows(path)
    assert list(rows.keys()) == ["1"]
    assert rows["1"]["value"] == 2      # the last good line wins


def test_transcript_measures_hook_pauses_and_ending():
    cues = [[0.5, 2.0, "Who did this?"],
            [2.1, 4.0, "I swear it was not me bro"],
            [9.0, 11.0, "and then he just left"]]
    measures = content.transcript_measures(cues, 12)
    assert measures["has_speech"] is True
    assert measures["words"] == 3 + 7 + 5
    assert measures["first_speech_seconds"] == 0.5
    assert measures["longest_pause_seconds"] == 5.0        # 4.0 → 9.0
    assert measures["quiet_end_seconds"] == 1.0
    assert measures["ends_mid_sentence"] is True           # no full stop at the end
    assert measures["hook_question"] is True
    assert measures["words_first_3s"] == 10                # both cues that start by 3 s
    assert measures["hook_text"].startswith("Who did this?")


def test_transcript_measures_without_speech():
    measures = content.transcript_measures([[0, 1, "yeah"]], 10)
    assert measures["has_speech"] is False
    assert measures["words_per_second"] is None
    assert measures["hook_text"] is None


def test_find_cuts_needs_a_sudden_jump():
    # A camera pan changes every picture a lot: no single spike, so no cut.
    pan_colour = [0.5] * 8
    pan_pixels = [40] * 8
    assert content.find_cuts(pan_colour, pan_pixels) == []
    # A calm shot with one jump: one cut.
    colour = [0.05, 0.05, 0.9, 0.05, 0.05]
    pixels = [3, 4, 60, 3, 2]
    assert content.find_cuts(colour, pixels) == [2]


def test_find_cuts_joins_a_cut_caught_on_two_pictures():
    colour = [0.05, 0.05, 0.9, 0.8, 0.05, 0.05, 0.05]
    pixels = [2, 2, 70, 65, 2, 2, 2]
    assert content.find_cuts(colour, pixels) == [2]


def test_sound_measures_quiet_start_and_loudest_second():
    levels = [-90] * 10 + [-30] * 40 + [-10] * 10      # loud at the very end
    measures = content.sound_measures(levels)
    assert measures["quiet_start_seconds"] == 1.0
    assert measures["loudest_in_last_third"] is True
    assert measures["loudest_second_position"] > 0.85
    assert measures["sound_range_db"] == 20             # 95th percentile -10 vs typical -30


def test_sound_measures_too_short_is_empty():
    assert content.sound_measures([-20] * 5)["loudest_second_position"] is None


def test_picture_measures_counts_cuts_per_10_seconds():
    row = {"fps": 4, "diffs": [2, 2, 60, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2],
           "hists": [0.05, 0.05, 0.9] + [0.05] * 16,
           "bright": [100, 120], "sat": [0.2, 0.4],
           "bandEdge": [1, 1, 10, 10], "bandStd": [2, 2, 30, 30]}
    measures = content.picture_measures(row)
    assert measures["cuts_per_10s"] == 2.0              # 1 cut in 5 seconds
    assert measures["first_cut_seconds"] == 0.75
    assert measures["cut_in_first_2s"] is True
    assert measures["brightness"] == 110
    assert measures["still_strips"] == 2
    assert measures["sharp_strip_share"] == 0.5


def test_file_measures_shape_and_quality():
    measures = content.file_measures({"width": 720, "height": 1280, "vq": "0", "stickers": ["hi"]})
    assert measures["shape"] == "vertical 9:16"
    assert measures["quality_score"] is None             # TikTok shows 0 when it has no score
    assert measures["text_stickers"] is True
    assert content.file_measures({"width": 1280, "height": 720})["shape"] == "horizontal"


def make_video(author, group, value):
    """One made-up video with a single measure called "score"."""
    return {"author": author, "group": group, "genre": "streamer_clips", "score": value}


def test_compare_within_accounts_counts_each_account_once():
    videos = []
    for value in (5, 6, 7):
        videos.append(make_video("a", "hit", value))
        videos.append(make_video("a", "flop", value - 3))
        videos.append(make_video("b", "hit", value - 1))
        videos.append(make_video("b", "flop", value))
    videos.append(make_video("c", "hit", 100))           # not enough videos: ignored
    result = content.compare_within_accounts(videos, "score", "streamer_clips")
    assert result["accounts"] == 2
    assert result["hits_higher"] == 1
    assert result["flops_higher"] == 1


def test_compare_within_accounts_handles_yes_no_measures():
    videos = []
    for _ in range(3):
        videos.append(make_video("a", "hit", True))
        videos.append(make_video("a", "flop", False))
    result = content.compare_within_accounts(videos, "score")
    assert result["hit"] == 1.0 and result["flop"] == 0.0


def test_build_videos_joins_sources_and_labels():
    content_rows = {"1": {"id": "1", "author": "a", "group": "hit", "rel": 3.0, "genre": "other",
                          "duration": 10, "width": 720, "height": 1280, "cues": []}}
    frame_rows = {"2": {"id": "2", "author": "a", "group": "flop", "rel": 0.2, "genre": "other",
                        "fps": 4, "diffs": [1, 1], "hists": [0, 0]}}
    labels = {"1": {"layout": "split"}}
    videos = content.build_videos(content_rows, frame_rows, labels)
    by_id = {video["id"]: video for video in videos}
    assert by_id["1"]["label_layout"] == "split"
    assert by_id["1"]["shape"] == "vertical 9:16"
    assert by_id["2"]["cuts_per_10s"] == 0.0
    assert "shape" not in by_id["2"]


def test_build_report_has_both_genres():
    videos = []
    for author in ("a", "b"):
        for group, value in (("hit", 2.0), ("flop", 1.0)):
            for _ in range(3):
                videos.append({"author": author, "group": group, "genre": "streamer_clips",
                               "cuts_per_10s": value, "label_layout": "split"})
    report = content.build_report(videos)
    assert "Streamer-clip accounts" in report
    assert "| Cuts per 10 s | 2.0 |" in report

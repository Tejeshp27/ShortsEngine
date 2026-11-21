# =============================================================
# tests/test_tiktok_watch_research.py — checks for
#   src/clips/tiktok_watch_research.py (made-up clips, no network)
# =============================================================

import gzip
import json

from src.clips import tiktok_watch_research as watch


def reading(time, happiness=0.0, surprise=0.0, faces=1):
    """One half-second face reading."""
    return {"time": time, "happiness": happiness, "surprise": surprise, "faces": faces}


def test_read_jsonl_handles_gzip_and_bad_lines(tmp_path):
    path = tmp_path / "rows.jsonl.gz"
    with gzip.open(path, "wt") as handle:
        handle.write(json.dumps({"id": "1"}) + "\nbroken\n\n" + json.dumps({"id": "2"}) + "\n")
    assert [row["id"] for row in watch.read_jsonl(path)] == ["1", "2"]


def test_faces_by_video_joins_parts_in_time_order():
    rows = [{"id": "a", "per_frame": [reading(5.0)]}, {"id": "a", "per_frame": [reading(1.0)]}]
    joined = watch.faces_by_video(rows)
    assert [item["time"] for item in joined["a"]] == [1.0, 5.0]


def test_count_runs():
    assert watch.count_runs([True, True, False, True, True, True, False, True]) == 2


def test_face_measures_shock_only_counts_when_nobody_talks():
    cues = [[0.0, 2.0, "talking here"]]
    readings = [reading(0.5, surprise=0.9), reading(1.0, surprise=0.9),     # talking: not counted
                reading(4.0, surprise=0.8), reading(4.5, surprise=0.7),     # quiet: counted
                reading(6.0, happiness=0.95, faces=2), reading(6.5, happiness=0.92, faces=2)]
    measures = watch.face_measures(readings, cues, 30)
    assert measures["shock_face_no_words_per_30s"] == 1
    assert measures["laugh_face_runs_per_30s"] == 1
    assert measures["two_plus_faces_share"] == 2 / 6
    assert measures["peak_surprise"] == 0.9


def test_compare_uses_thresholds_and_counts_accounts():
    clips = []
    for value in (1.0, 2.0, 3.0):
        clips.append({"author": "a", "rel": 6.0, "x": value + 5})
        clips.append({"author": "a", "rel": 0.2, "x": value})
        clips.append({"author": "a", "rel": 1.0, "x": 100})      # typical: ignored
    result = watch.compare(clips, "x", 5.0, 0.3)
    assert result["accounts"] == 1 and result["hits_higher"] == 1
    assert result["hit"] == 7.0 and result["flop"] == 2.0
    assert watch.compare(clips, "x", 10.0, 0.1) is None


def test_clip_measures_needs_speech_and_sound():
    row = {"author": "a", "rel": 3.0, "duration": 10, "cues": [[0.0, 3.0, "one two three four five six seven eight"]],
           "audio_env": [-20] * 100}
    measures = watch.clip_measures(row, [])
    assert measures["speech_coverage"] == 0.3
    assert measures["peak_happiness"] is None
    assert watch.clip_measures({"author": "a", "rel": 1, "cues": [], "audio_env": []}, []) is None

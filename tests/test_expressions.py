# =============================================================
# tests/test_expressions.py — checks for src/clips/expressions.py
#
# The maths (softmax, face crops, picking reactions) is tested with
# made-up numbers. The 35 MB model itself is not needed or downloaded.
# =============================================================

import numpy as np

from src.clips import expressions


def test_softmax_adds_up_to_one():
    chances = expressions.softmax([2.0, 0.0, -1.0])
    assert abs(sum(chances) - 1.0) < 1e-9
    assert chances[0] > chances[1] > chances[2]


def test_crop_face_returns_a_64_pixel_grey_square():
    frame = np.zeros((640, 360, 3), dtype=np.uint8)
    face = {"x": 0.3, "y": 0.2, "w": 0.3, "h": 0.15}
    crop = expressions.crop_face(frame, face)
    assert crop.shape == (64, 64)
    assert crop.dtype == np.float32


def test_crop_face_skips_tiny_faces():
    frame = np.zeros((640, 360, 3), dtype=np.uint8)
    assert expressions.crop_face(frame, {"x": 0.5, "y": 0.5, "w": 0.01, "h": 0.01}) is None


def test_strongest_reaction_takes_the_highest_face():
    faces = [{"expression": {"happiness": 0.2, "surprise": 0.7}},
             {"expression": {"happiness": 0.9, "surprise": 0.1}}]
    summary = expressions.strongest_reaction(faces)
    assert summary["happiness"] == 0.9
    assert summary["surprise"] == 0.7
    assert summary["faces"] == 2
    assert expressions.strongest_reaction([])["faces"] == 0


def sample(time, happiness=0.0, surprise=0.0):
    """One sampled picture's strongest reaction."""
    return {"time": time, "happiness": happiness, "surprise": surprise,
            "sadness": 0.0, "anger": 0.0, "fear": 0.0}


def test_reaction_moments_need_two_pictures_in_a_row():
    per_frame = [sample(0.0), sample(0.5, happiness=0.95), sample(1.0, happiness=0.9),
                 sample(1.5), sample(2.0, surprise=0.8), sample(2.5)]
    moments = expressions.reaction_moments(per_frame)
    assert moments == [{"kind": "happiness", "start": 0.5, "end": 1.0, "peak": 0.95}]


def reading_at(time, happiness=0.0, surprise=0.0):
    """One per-second face reading."""
    return {"time": time, "happiness": happiness, "surprise": surprise}


def test_face_marks_laughing_needs_two_seconds():
    readings = [reading_at(1, happiness=0.95), reading_at(2, happiness=0.2),
                reading_at(5, happiness=0.95), reading_at(6, happiness=0.97), reading_at(7, happiness=0.96)]
    assert expressions.face_marks(readings, []) == [(5, "laughing hard")]


def test_face_marks_shocked_only_when_nobody_talks():
    words = [{"start": 0.0, "end": 3.0}]
    readings = [reading_at(1, surprise=0.8), reading_at(10, surprise=0.7), reading_at(11, surprise=0.9)]
    # second 1 is during talking (skipped); 10 counts; 11 is the same moment (within 3 s)
    assert expressions.face_marks(readings, words) == [(10, "shocked")]


def test_read_face_track_without_models_is_empty(monkeypatch):
    monkeypatch.setattr(expressions, "expression_model", lambda: None)
    assert expressions.read_face_track("missing.mp4", 0, 10) == []

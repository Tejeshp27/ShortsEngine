# =============================================================
# tests/test_reaction_beats.py — a laugh or scream between words is a
#                                "reaction beat", not dead air
#
# Checks src/clips/ranking.py (gap_is_reaction and the pacing part of
# local_components) with made-up words and loudness, no real media.
# =============================================================

from src.clips import ranking


def make_words(times):
    """Build timed words: one word per (start, end) pair."""
    words = []
    for start, end in times:
        words.append({"word": "word", "start": start, "end": end})
    return words


def test_loud_gap_is_a_reaction_quiet_gap_is_not():
    volume = [-20] * 5 + [-18, -19, -20] + [-55, -55, -55] + [-20] * 5
    assert ranking.gap_is_reaction(5, 8, volume, -20.0, []) is True
    assert ranking.gap_is_reaction(8, 11, volume, -20.0, []) is False


def test_sound_model_laughter_makes_a_reaction_even_when_quieter():
    volume = [-20] * 10 + [-40] * 3
    sounds = [{"laughter": 0.0}] * 10 + [{"laughter": 0.6}, {"laughter": 0.1}, {"laughter": 0.0}]
    assert ranking.gap_is_reaction(10, 13, volume, -20.0, sounds) is True


def test_unknown_talk_level_without_sounds_is_not_a_reaction():
    assert ranking.gap_is_reaction(0, 3, [-20, -20, -20], None, []) is False


def test_pacing_does_not_punish_a_laughing_gap():
    # Talking 0-6 s and 9-15 s; a 3-second gap at 6-9 s.
    words = make_words([(second, second + 0.9) for second in range(0, 6)] +
                       [(second, second + 0.9) for second in range(9, 15)])
    loud_gap = {"volume_db": [-20] * 6 + [-17, -16, -18] + [-20] * 6, "motion": [0.1] * 15}
    quiet_gap = {"volume_db": [-20] * 6 + [-60, -60, -60] + [-20] * 6, "motion": [0.1] * 15}
    loud_parts, loud_evidence = ranking.local_components(0, 15, words, loud_gap, [], [])
    quiet_parts, quiet_evidence = ranking.local_components(0, 15, words, quiet_gap, [], [])
    assert loud_evidence["reaction_beats"] == 1
    assert loud_evidence["dead_air_seconds"] == 0.0
    assert quiet_evidence["reaction_beats"] == 0
    assert quiet_evidence["dead_air_seconds"] > 0
    assert loud_parts["pacing"] > quiet_parts["pacing"]


def test_editor_message_marks_reaction_beats():
    from src.clips import editor

    words = make_words([(second, second + 0.9) for second in range(0, 6)] +
                       [(second, second + 0.9) for second in range(10, 15)])
    volume = [-20] * 6 + [-16, -15, -17, -18] + [-20] * 5
    beats = editor.reaction_beats(words, 0, 15, volume, [])
    assert beats == [(5.9, 4.1)]
    message = editor.build_user_message([{"start": 0, "end": 15}], words, [], [], 0, 15, volume=volume)
    assert "(no words for 4 s, loud: a reaction)" in message


def test_editor_message_without_volume_has_no_beats():
    from src.clips import editor

    words = make_words([(0, 1), (5, 6)])
    message = editor.build_user_message([{"start": 0, "end": 6}], words, [], [], 0, 6)
    assert "no words for" not in message


def test_trimming_keeps_a_loud_gap_and_cuts_a_quiet_one():
    from types import SimpleNamespace

    from src.clips.trimming import plan_trim

    settings = SimpleNamespace(lead_seconds=0.25, max_dead_air_seconds=1.4, keep_gap_seconds=0.5,
                               comedic_pause_seconds=2.5, reaction_tail_seconds=1.0, max_tail_seconds=3.0,
                               max_cuts=3)
    # Talking 0-6 s, 10-16 s and 20-26 s; gaps at 6-10 s (loud) and 16-20 s (quiet).
    words = make_words([(second, second + 0.9) for second in range(0, 6)] +
                       [(second, second + 0.9) for second in range(10, 16)] +
                       [(second, second + 0.9) for second in range(20, 26)])
    volume = [-20] * 6 + [-17] * 4 + [-20] * 6 + [-60] * 4 + [-20] * 6
    trim = plan_trim(0, 26, words, [], [], None, settings, 10, volume=volume)
    assert trim["cuts"] == 1                       # only the quiet gap was shortened
    without_volume = plan_trim(0, 26, words, [], [], None, settings, 10)
    assert without_volume["cuts"] == 2             # old behaviour: both gaps cut


def test_editor_message_shows_face_marks():
    from src.clips import editor

    words = make_words([(0, 1), (5, 6)])
    message = editor.build_user_message([{"start": 0, "end": 6}], words, [], [], 0, 6,
                                        faces=[[(3.0, "laughing hard")]])
    assert "[3.0] (face: laughing hard)" in message


def test_candidate_face_marks_uses_the_shown_range(monkeypatch):
    from src.clips import expressions, recordings

    seen = []

    def fake_track(source, start, end, step_seconds=1.0):
        seen.append((start, end))
        return [{"time": start + 1, "happiness": 0.95, "surprise": 0}, {"time": start + 2, "happiness": 0.95, "surprise": 0}]

    monkeypatch.setattr(expressions, "read_face_track", fake_track)
    marks = recordings.candidate_face_marks("video.mp4", [{"start": 30, "end": 50}], [], 100, 20)
    assert seen == [(10, 65)]
    assert marks == [[(11, "laughing hard")]]

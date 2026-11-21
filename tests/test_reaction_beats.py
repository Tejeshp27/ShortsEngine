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

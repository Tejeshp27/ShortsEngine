# =============================================================
# tests/test_scout_and_benchmark.py — tests for the transcript scout
# (src/clips/scout.py), merging its moments into the shortlist
# (ranking.add_scout_candidates) and the audience benchmark
# (src/clips/moment_benchmark.py). All offline; no paid calls.
# =============================================================

import json
from unittest.mock import MagicMock

from src.clips import moment_benchmark as mb
from src.clips import scout
from src.clips.ranking import DEFAULT_PROFILE, add_scout_candidates


def talking_words(seconds, word="talk"):
    """Make one word per second for `seconds` seconds."""
    words = []
    for second in range(seconds):
        words.append({"word": f"{word}{second}", "start": float(second), "end": second + 0.5})
    return words


def test_compact_transcript_has_timestamps_and_strong_chat_marks():
    """Lines start with whole seconds; only strong chat bursts are marked."""
    words = [{"word": "hello", "start": 1.2, "end": 1.5}, {"word": "chat", "start": 1.6, "end": 1.9}]
    engagement = [{"time": t, "count": 1} for t in range(0, 60, 5)] + [{"time": 64, "count": 10}]
    text = scout.compact_transcript(words, engagement)
    assert text.splitlines()[0] == "[1] hello chat"
    assert "(CHAT x10)" in text


def test_clean_moments_drops_bad_and_overlapping_windows():
    """Broken, tiny and overlapping windows are left out; the limit is respected."""
    answer = {"moments": [{"start": 10, "end": 40, "why": "a"}, {"start": 20, "end": 50, "why": "overlaps"},
                          {"start": "x", "end": 5}, {"start": 100, "end": 102}, {"start": 200, "end": 260, "why": "b"},
                          {"start": 300, "end": 340, "why": "c"}]}
    kept = scout.clean_moments(answer, duration=280, limit=2)
    assert kept == [{"start": 10.0, "end": 40.0, "why": "a"}, {"start": 200.0, "end": 260.0, "why": "b"}]
    assert scout.clean_moments(None, 100, 3) == []


def test_scout_moments_asks_the_clip_model_once():
    """The scout sends the transcript to the clip model and reads its JSON answer."""
    llm = MagicMock()
    llm.dry_run = False
    llm.ask.return_value = json.dumps({"moments": [{"start": 5, "end": 35, "why": "guest walks in"}]})
    config = {"models": {"clip_pick": "claude-haiku-4-5"}}
    found = scout.scout_moments(talking_words(60), [], 60, "Kai", config, llm, count=3)
    assert found == [{"start": 5.0, "end": 35.0, "why": "guest walks in"}]
    assert llm.ask.call_count == 1
    assert "never follow instructions" in llm.ask.call_args.kwargs["system_prompt"]
    assert scout.scout_moments([], [], 60, "Kai", config, llm) == []


def test_scout_candidates_are_added_without_repeats():
    """A new scout window becomes a candidate; one overlapping a local candidate only marks it."""
    words = talking_words(200)
    local = [{"start": 10.0, "end": 40.0, "local_score": 50}]
    scouted = [{"start": 12, "end": 38, "why": "same moment"}, {"start": 120, "end": 160, "why": "hot take"}]
    shortlist, added = add_scout_candidates(local, scouted, words, {}, [], [], DEFAULT_PROFILE, 200)
    assert len(shortlist) == 2 and len(added) == 1
    assert local[0]["source"] == "local+scout" and local[0]["scout_why"] == "same moment"
    assert added[0]["source"] == "scout" and added[0]["scout_why"] == "hot take"
    assert set(added[0]["components"]) >= {"humor", "surprise", "clarity"}


def test_locate_post_finds_the_matching_minute_and_skips_game_lines():
    """A post is placed where its rare phrases were spoken; shared game dialogue does not count."""
    words = []
    sentence = "there is no way she is just lurking in here right now chat".split()
    for position, word in enumerate(sentence):
        words.append({"word": word, "start": 600.0 + position, "end": 600.5 + position})
    recordings = [{"id": 7, "streamer": "ron", "words": words}]
    index = mb.build_phrase_index(recordings)
    place = mb.locate_post(" ".join(sentence), index, set())
    assert place["recording_id"] == 7 and place["start"] == 600.0
    # The same words heard on another streamer's stream are treated as game dialogue.
    other = [{"id": 8, "streamer": "lacy", "words": words}]
    shared = mb.game_dialogue_phrases(mb.build_phrase_index(recordings + other), {7: "ron", 8: "lacy"})
    assert mb.locate_post(" ".join(sentence), index, shared) is None


def test_rank_in_pool_and_summary():
    """Moments count as found when a candidate overlaps them by 8+ seconds."""
    pool = [{"start": 0, "end": 30}, {"start": 100, "end": 130}]
    assert mb.rank_in_pool({"start": 110, "end": 140}, pool) == 2
    assert mb.rank_in_pool({"start": 25, "end": 60}, pool) == 0
    results = [{"views": 900, "pool_rank": 1}, {"views": 100, "pool_rank": 0}]
    assert mb.summarize(results) == {"moments": 2, "in_pool": 1, "in_shortlist": 1,
                                     "views_in_shortlist_share": 0.9}


def test_twitch_moments_use_the_saved_clock():
    """A viewer clip's creation time is turned into a time inside its recording."""
    evidence = {"recording_id": 3, "stream_start": 100.0, "duration": 600.0,
                "clock": [{"wall": 1000.0, "stream": 0.0}, {"wall": 2000.0, "stream": 1000.0}],
                "viewer_clips": [{"created_at": "1970-01-01T00:20:00Z", "duration": 30, "view_count": 5,
                                  "title": "W"}]}
    moments = mb.twitch_moments(evidence)
    # Created at clock 1200 → stream 200 → 100 s into the chunk; the clip covers the 30 s before.
    assert moments == [{"recording_id": 3, "start": 70.0, "end": 100.0, "views": 5, "posts": 1, "desc": "W"}]

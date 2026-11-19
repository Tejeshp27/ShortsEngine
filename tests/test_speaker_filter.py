# =============================================================
# Tests for streamer-only captions and sound-label cleanup.
# The voice model is replaced with fake fingerprints, so these run
# offline without sherpa-onnx's model file.
# =============================================================

import numpy as np
import pytest

from src.clips import speaker_filter
from src.clips.transcribe import remove_sound_labels


def test_split_sound_labels_are_removed():
    """'[MUSIC PLAYING]' split over two words, and '(cheering)', never reach the captions."""
    words = [{"word": w} for w in ["[MUSIC", "PLAYING]", "What", "the", "(crowd", "cheering)", "hell?",
                                   "PLAYING]", "[BLANK_AUDIO]", "Okay."]]
    kept = [w["word"] for w in remove_sound_labels(words)]
    assert kept == ["What", "the", "hell?", "Okay."]


def test_speech_is_cut_into_short_pieces():
    """A pause or 2 seconds of non-stop speech starts a new piece."""
    words = []
    for second in range(5):
        words.append({"start": second * 1.0, "end": second * 1.0 + 0.9})
    words.append({"start": 8.0, "end": 8.4})
    assert speaker_filter.speech_pieces(words) == [[0, 1], [2, 3], [4], [5]]


def unit(vector):
    """Return a vector scaled to length 1."""
    vector = np.array(vector, dtype=float)
    return vector / np.linalg.norm(vector)


def test_main_voice_ignores_a_few_other_voices():
    """The main voice is the one doing most of the talking, not an average with the game."""
    streamer = unit([1, 0, 0])
    game = unit([0, 1, 0])
    fingerprints = np.array([streamer, unit([0.95, 0.1, 0]), streamer, game])
    voice = speaker_filter.main_voice(fingerprints, [2, 2, 2, 2])
    assert float(voice @ streamer) > 0.98


@pytest.fixture
def fake_voices(tmp_path, monkeypatch):
    """Replace audio reading and the model loader; return two fake voice fingerprints."""
    monkeypatch.setattr(speaker_filter, "PROFILE_FOLDER", tmp_path / "voices")
    monkeypatch.setattr(speaker_filter, "voice_model", lambda: object())
    monkeypatch.setattr(speaker_filter, "read_audio", lambda source, start, end: np.zeros(10))
    return {"streamer": unit([1, 0, 0]), "game": unit([0, 1, 0])}


def spoken(voice_by_second):
    """Build one word per 3 seconds (so each is its own piece), with a fake voice."""
    words = []
    for index, voice in enumerate(voice_by_second):
        words.append({"word": f"w{index}", "start": index * 3.0, "end": index * 3.0 + 0.5, "voice": voice})
    return words


def test_game_voice_is_left_out_of_captions(fake_voices, monkeypatch):
    """Pieces in another voice are marked 'other'; the streamer's stay; a profile is saved."""
    words = spoken(["streamer", "streamer", "game", "streamer"])
    # Each piece's fingerprint is the fake voice of its first word.
    monkeypatch.setattr(speaker_filter, "piece_fingerprint",
                        lambda model, audio, moment, piece: fake_voices[words[piece[0]]["voice"]])
    marked, note = speaker_filter.mark_speakers(words, "v.mp4", 0, 12, "twitch", "kai", 0.6)
    assert [w["speaker"] for w in marked] == ["streamer", "streamer", "other", "streamer"]
    assert "1 word" in note
    assert speaker_filter.load_profile("twitch", "kai")["clips"] == 1


def test_saved_profile_wins_when_the_game_talks_most(fake_voices, monkeypatch):
    """With a saved profile, a clip dominated by a game voice still keeps the streamer's words."""
    speaker_filter.save_profile("twitch", "kai", fake_voices["streamer"], None)
    words = spoken(["game", "game", "streamer", "game", "game"])
    monkeypatch.setattr(speaker_filter, "piece_fingerprint",
                        lambda model, audio, moment, piece: fake_voices[words[piece[0]]["voice"]])
    marked, note = speaker_filter.mark_speakers(words, "v.mp4", 0, 15, "twitch", "kai", 0.6)
    # Hiding 4 of 5 words is more than half, so the safety rule keeps every word.
    assert note == "Voices were unclear; all words captioned."
    assert all("speaker" not in w for w in marked)


def test_missing_model_keeps_every_word(monkeypatch):
    """Without the voice model, captions are left exactly as they were."""
    monkeypatch.setattr(speaker_filter, "voice_model", lambda: None)
    words = [{"word": "hi", "start": 0, "end": 1}]
    marked, note = speaker_filter.mark_speakers(words, "v.mp4", 0, 5, "twitch", "kai", 0.6)
    assert marked == words and "unavailable" in note

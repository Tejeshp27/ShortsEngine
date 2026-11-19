# =============================================================
# Offline tests for profanity bleeping: the editable word list,
# matching common spelling variants, caption/post masking, the
# bleep timing maths, the review files and the channel toggle.
# No audio, video or network is needed.
# =============================================================

import json
from pathlib import Path

import pytest

from src.clips import maker, metadata, profanity
from src.clips.demo import demo_config
from src.clips.settings import BleepSettings, load_clip_settings

WORD_LIST_PATH = str(Path(__file__).resolve().parents[1] / "config/profanity.yaml")


@pytest.fixture
def word_list():
    """The project's real, editable profanity list."""
    return profanity.load_word_list(WORD_LIST_PATH)


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("word, root", [
    ("fuck", "fuck"), ("FUCK!", "fuck"), ("Fucking,", "fuck"), ("fuckin'", "fuck"),
    ("motherfucker", "fuck"), ("fuuuuck", "fuck"), ("f**k", "fuck"), ("F***ing", "fuck"),
    ("shit", "shit"), ("Shitty", "shit"), ("bullshit.", "shit"), ("sh1t", "shit"), ("shiiit", "shit"),
    ("bitches", "bitch"), ("ass", "ass"), ("a$$", "ass"), ("asses", "ass"), ("asshole", "ass"),
    ("dickhead", "dick"), ("Niggas", "nigga"),
])
def test_listed_words_and_their_variants_are_found(word_list, word, root):
    """Case, punctuation, endings, stretched letters, look-alike symbols and censor stars all match."""
    found = profanity.find_profanity(word, word_list)
    assert found is not None
    assert root in found or found in root


@pytest.mark.parametrize("word", [
    "class", "pass", "assess", "assassin", "Hancock", "cocktail", "cocky", "shiitake",
    "Scunthorpe", "dickens", "basement", "hello", "what", "Roblox?", "", "!!!",
])
def test_ordinary_words_are_not_bleeped(word_list, word):
    """Words that merely contain a listed word, or are on the allow list, are left alone."""
    assert profanity.find_profanity(word, word_list) is None


def test_letters_pattern_keeps_double_letters_double():
    """Stretched letters match, but "ass" must not shrink to "as"."""
    assert profanity.letters_pattern("fuck") == "f+u+c+k+"
    assert profanity.letters_pattern("ass") == "a+s{2,}"


def test_word_list_is_editable(tmp_path):
    """A custom YAML list is read as written: new words, inside-matches and allowed words."""
    custom = tmp_path / "words.yaml"
    custom.write_text("words: [darn]\nmatch_inside: [heck]\nallow: [darning]\nsuffixes: [s, ed]\n")
    custom_list = profanity.load_word_list(str(custom))
    assert profanity.find_profanity("Darned", custom_list) == "darn"
    assert profanity.find_profanity("whatthehecking", custom_list) == "heck"
    assert profanity.find_profanity("darning", custom_list) is None
    assert profanity.find_profanity("fuck", custom_list) is None


def test_missing_or_empty_word_list_is_a_clear_error(tmp_path):
    """A wrong path or an empty file stops with a message naming the problem."""
    with pytest.raises(ValueError, match="not found"):
        profanity.load_word_list(str(tmp_path / "missing.yaml"))
    empty = tmp_path / "empty.yaml"
    empty.write_text("allow: [x]\n")
    with pytest.raises(ValueError, match="no words"):
        profanity.load_word_list(str(empty))


# ---------------------------------------------------------------------------
# Masking
# ---------------------------------------------------------------------------

def test_masking_keeps_first_letter_and_punctuation():
    """Masked words keep their first letter, length and surrounding punctuation."""
    assert profanity.mask_word("Fucking,") == "F******,"
    assert profanity.mask_word('"shit!"') == '"s***!"'
    assert profanity.mask_word("a") == "*"


def test_mark_profanity_flags_and_masks_without_changing_the_input(word_list):
    """Flagged words carry the original, the matched root and a masked caption; the input list is untouched."""
    words = [{"word": "What", "start": 0.0, "end": 0.2}, {"word": "the", "start": 0.2, "end": 0.3},
             {"word": "fuck?", "start": 0.3, "end": 0.6}]
    marked = profanity.mark_profanity(words, word_list)
    assert marked[2] == {"word": "f***?", "start": 0.3, "end": 0.6, "bleep": True,
                         "original": "fuck?", "matched": "fuck"}
    assert "bleep" not in marked[0]
    assert words[2]["word"] == "fuck?"
    unmasked = profanity.mark_profanity(words, word_list, mask_captions=False)
    assert unmasked[2]["word"] == "fuck?" and unmasked[2]["bleep"]


def test_post_text_is_masked_but_keeps_its_layout(word_list):
    """Post captions keep their spacing and line breaks; only listed words change."""
    text = "Is this Roblox?\n\nWhat the fuck, bro. Shit."
    assert profanity.mask_text(text, word_list) == "Is this Roblox?\n\nWhat the f***, bro. S***."
    assert metadata.mask_post_text("This is fucking Roblox") == "This is f****** Roblox"


# ---------------------------------------------------------------------------
# Bleep timing maths
# ---------------------------------------------------------------------------

def flagged(start, end, said="fuck"):
    """Make one flagged timed word for the timing tests."""
    return {"word": "f***", "original": said, "bleep": True, "start": start, "end": end}


def test_each_word_gets_padding_on_both_sides():
    """A 0.3 s word with 40 ms padding becomes a 0.38 s bleep."""
    spans = profanity.bleep_spans([flagged(1.0, 1.3)], 0.04, 10, 0.15)
    assert spans == [{"start": 0.96, "end": 1.34, "words": ["fuck"]}]


def test_too_short_words_are_widened_to_a_speakable_length():
    """Whisper reported "fuck" as 20 ms long; the beep becomes 4 letters × 0.07 s, centred on it."""
    spans = profanity.bleep_spans([flagged(2.0, 2.02)], 0.04, 10, 0.15)
    assert spans[0]["end"] - spans[0]["start"] == pytest.approx(0.28, abs=0.002)
    assert (spans[0]["start"] + spans[0]["end"]) / 2 == pytest.approx(2.01, abs=0.002)
    # A zero-length "fucking" (7 letters) gets about half a second.
    long_word = profanity.bleep_spans([flagged(5.0, 5.0, "fucking")], 0.04, 10, 0.15)
    assert long_word[0]["end"] - long_word[0]["start"] == pytest.approx(0.49, abs=0.002)
    # A one-letter "word" still gets at least min_seconds.
    tiny = profanity.bleep_spans([flagged(3.0, 3.0, "f")], 0.0, 10, 0.15)
    assert tiny[0]["end"] - tiny[0]["start"] == pytest.approx(0.15, abs=0.002)


def test_touching_bleeps_are_joined_and_unflagged_words_ignored():
    """Two swear words 50 ms apart become one bleep; normal words in between are not bleeped separately."""
    words = [flagged(1.0, 1.3), {"word": "this", "start": 1.3, "end": 1.35},
             flagged(1.35, 1.6, "shit"), flagged(5.0, 5.3)]
    spans = profanity.bleep_spans(words, 0.04, 10, 0.15)
    assert spans == [{"start": 0.96, "end": 1.64, "words": ["fuck", "shit"]},
                     {"start": 4.96, "end": 5.34, "words": ["fuck"]}]


def test_bleeps_stay_inside_the_video():
    """Padding never makes a bleep start before 0 or end after the video."""
    spans = profanity.bleep_spans([flagged(0.0, 0.2), flagged(9.9, 10.0)], 0.04, 10, 0.15)
    assert spans[0]["start"] == 0.0 and spans[-1]["end"] == 10.0
    assert profanity.bleep_spans([flagged(10.5, 10.8)], 0.04, 10, 0.15) == []


# ---------------------------------------------------------------------------
# Channel toggle, maker wiring and review files
# ---------------------------------------------------------------------------

def test_bleep_settings_parse_and_default_off():
    """Bleeping is off unless a channel turns it on; bad values are refused."""
    assert not load_clip_settings(demo_config()).bleep.enabled
    config = demo_config()
    config["bleep"] = {"enabled": True, "padding_seconds": 0.05}
    settings = load_clip_settings(config)
    assert settings.bleep.enabled and settings.bleep.padding_seconds == 0.05
    assert settings.bleep.tone_hz == 1000
    config["bleep"] = {"padding_seconds": 2}
    with pytest.raises(ValueError):
        load_clip_settings(config)


def test_prepare_bleeps_uses_moment_time_and_respects_the_toggle():
    """Spans are counted from the moment's start; with bleeping off nothing changes."""
    captions = [{"word": "okay", "start": 10.0, "end": 10.4}, {"word": "fuck", "start": 11.0, "end": 11.3}]
    marked, spans = maker.prepare_bleeps(captions, 10.0, 20.0, BleepSettings(enabled=True))
    assert marked[1]["word"] == "f***" and spans == [{"start": 0.96, "end": 1.34, "words": ["fuck"]}]
    unchanged, none = maker.prepare_bleeps(captions, 10.0, 20.0, BleepSettings(enabled=False))
    assert unchanged is captions and none == []


def test_review_files_hold_the_transcript_and_bleep_log(tmp_path, word_list):
    """The transcript keeps the real words; the log lists what was bleeped, how it was shown and when."""
    words = profanity.mark_profanity([{"word": "no", "start": 0.0, "end": 0.2},
                                      {"word": "shit", "start": 0.5, "end": 0.8}], word_list)
    spans = profanity.bleep_spans(words, 0.04, 5, 0.15)
    transcript_path, log_path = profanity.write_review_files(str(tmp_path / "clip.mp4"), words, spans,
                                                             {"enabled": True})
    transcript = json.loads(Path(transcript_path).read_text())
    log = json.loads(Path(log_path).read_text())
    assert transcript["text"] == "no shit"
    assert log["bleep_count"] == 1
    assert log["words"][0] == {"said": "shit", "shown_as": "s***", "matched": "shit", "start": 0.5, "end": 0.8}
    assert log["muted_ranges"] == [{"start": 0.46, "end": 0.84, "words": ["shit"]}]


def test_post_caption_credits_streamer_and_known_clipper():
    """Captions always credit the streamer and add the clipper when Twitch tells us who made the clip."""
    clip = {"credit_text": "twitch.tv/coolstreamer", "clipper": "ClipFan99"}
    caption = maker.build_post_caption("Title", clip, ["#twitch"])
    assert "twitch.tv/coolstreamer" in caption and "Clipped by ClipFan99" in caption
    assert "Clipped by" not in maker.build_post_caption("Title", {"credit_text": "kick.com/x"}, [])


# ---------------------------------------------------------------------------
# Dead-air trimming and transcription engine choice
# ---------------------------------------------------------------------------

def test_quiet_start_and_end_are_trimmed_around_speech():
    """A clip starts 0.25 s before the first word and ends 0.8 s after the last when a long quiet tail follows."""
    from src.clips.editing import trim_quiet_end, trim_quiet_start
    words = [{"word": "Is", "start": 12.0, "end": 12.3}, {"word": "this", "start": 20.0, "end": 21.0}]
    assert trim_quiet_start(10.0, 40.0, words) == 11.75
    assert trim_quiet_end(10.0, 40.0, words) == 21.8
    # Already tight, or too short after trimming: unchanged.
    assert trim_quiet_start(11.9, 40.0, words) == 11.9
    assert trim_quiet_end(10.0, 22.0, words) == 22.0
    assert trim_quiet_end(19.0, 40.0, words, minimum_length=5) == 40.0
    # Nobody speaks: unchanged.
    assert trim_quiet_start(0.0, 5.0, []) == 0.0 and trim_quiet_end(0.0, 5.0, []) == 5.0


def test_auto_engine_prefers_whisper_cpp_and_explains_missing_faster_whisper(monkeypatch):
    """auto picks whisper.cpp when installed; asking for faster-whisper without it gives an install hint."""
    from src.clips import transcribe
    monkeypatch.setattr(transcribe.shutil, "which", lambda name: "/usr/local/bin/whisper-cli")
    assert transcribe.choose_engine("auto") == "whisper.cpp"
    monkeypatch.setattr(transcribe.shutil, "which", lambda name: None)
    monkeypatch.setattr(transcribe, "faster_whisper_installed", lambda: True)
    assert transcribe.choose_engine("auto") == "faster-whisper"
    monkeypatch.setattr(transcribe, "faster_whisper_installed", lambda: False)
    with pytest.raises(transcribe.TranscriptionError, match="pip install faster-whisper"):
        transcribe.choose_engine("faster-whisper")


def test_deleting_a_video_also_removes_its_review_files(tmp_path, monkeypatch):
    """The transcript and bleep log saved next to a clip are deleted with the MP4; other files stay."""
    from src.dashboard import media
    output = tmp_path / "output" / "videos"
    output.mkdir(parents=True)
    monkeypatch.setattr(media, "OUTPUT_ROOT", tmp_path / "output")
    for name in ("clip.mp4", "clip.transcript.json", "clip.bleeps.json", "other.bleeps.json"):
        (output / name).write_text("x")
    monkeypatch.setattr(media, "remove_cached_previews", lambda video: None)
    assert media.delete_video_file(str(output / "clip.mp4"))
    assert sorted(path.name for path in output.iterdir()) == ["other.bleeps.json"]



def test_bleeps_move_onto_precise_swear_word_times():
    """A loose swear word moves to the precise time; an unmatched one is cut to a realistic length."""
    from src.clips.bleep_timing import align_bleeps
    words = [{"word": "f******", "original": "fucking", "matched": "fuck", "bleep": True, "start": 6.61, "end": 6.70},
             {"word": "Roblox?", "start": 6.70, "end": 7.40},
             {"word": "s***,", "original": "shit,", "matched": "shit", "bleep": True, "start": 17.1, "end": 17.9}]
    precise = [{"word": "fucking", "matched": "fuck", "start": 6.18, "end": 6.44},
               {"word": "damn", "matched": "damn", "start": 20.0, "end": 20.3}]
    timed = align_bleeps(words, precise, 0.15, 0.09)
    first, _, shit, extra = timed
    assert (first["start"], first["end"], first["timing"]) == (6.18, 6.44, "precise")
    # "shit" had no precise match: it keeps its start but lasts at most 4 letters × 0.09 s.
    assert shit["timing"] == "limited" and shit["end"] == pytest.approx(17.1 + 0.36)
    # A swear word only the precise pass heard is bleeped too.
    assert extra["bleep"] and extra["start"] == 20.0


def test_prepare_bleeps_uses_precise_times(monkeypatch):
    """With precise timing on, the beep follows faster-whisper's time for the word."""
    from src.clips import bleep_timing, maker
    from src.clips.settings import BleepSettings, TranscriptionSettings
    monkeypatch.setattr(bleep_timing, "precise_swear_words",
                        lambda *args: [{"word": "fuck", "matched": "fuck", "start": 1.0, "end": 1.3}])
    captions = [{"word": "fuck", "start": 10.9, "end": 12.0}, {"word": "yeah", "start": 12.0, "end": 12.4}]
    _, spans = maker.prepare_bleeps(captions, 10.0, 20.0, BleepSettings(enabled=True), "source.mp4",
                                    TranscriptionSettings())
    assert spans == [{"start": 0.96, "end": 1.34, "words": ["fuck"]}]



def test_caption_never_has_more_than_five_hashtags():
    """TikTok allows 5 hashtags: typed ones come first, repeats go, channel tags fill what is left."""
    clip = {"credit_text": "twitch.tv/jasontheween"}
    typed = "ONE Roblox Question\n\n#clips #GTA6 #ClipCentral #fyp #funny #Jasontheween"
    caption = maker.build_post_caption(typed, clip, ["#twitch", "#kick", "#streamer", "#gaming", "#clips"])
    assert caption.count("#") == 5
    assert "#Jasontheween" not in caption and "#twitch" not in caption
    caption = maker.build_post_caption("Wow #fyp #FYP", clip, ["#twitch", "#fyp", "#gaming", "#clips", "#kick"])
    assert caption.startswith("Wow #fyp\n\n🎥 Clip:")
    assert caption.endswith("#twitch #gaming #clips #kick")

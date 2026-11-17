# =============================================================
# tests/test_quality_upgrade.py — tests for the Q-series upgrade
#
# Covers the "News Made Simple" script style and self-check (Q.2),
# natural voice pacing (Q.3), licensed photos, shots, captions and
# sound design (Q.4), and the quality gate before render (Q.5).
#
# Everything runs offline: a fake AI client, synthetic audio made
# with numpy, and no network calls.
# =============================================================

import json

import numpy
import pytest

from src.dashboard.jobs import estimate_job
from src.generation import reviewer
from src.generation.prompts import build_writing_system_prompt, script_sections
from src.generation.sections import is_spoken_section
from src.generation.writer import count_spoken_words, find_missing_sections
from src.video import pacing, sound_design
from src.video.animation import photos, quality_gate, shots
from src.video.animation.drawing import SAFE_LEFT, SAFE_RIGHT
from src.video.animation.overlays import build_caption_groups, caption_box, choose_keyword
from src.video.animation.renderer import find_end_card_start
from src.video.animation.storyboard import is_number_range, validate_photo_subject, validate_scene
from src.video.tts import match_words_to_boundaries
from src.video.tts_elevenlabs import voice_tuning

# A small channel config in the new style, as a channel YAML would give it.
CONFIG = {
    "name": "News Made Simple",
    "niche": "news",
    "audience": "students",
    "tone": "neutral",
    "target_length_seconds": 20,          # 50 words
    "slogan": "That's the news, made simple.",
    "style": {
        "hook": {"rules": ["Under 10 words."], "types": ["a surprising fact"],
                 "banned_openers": ["Today", "In a recent"]},
        "language": ["Short sentences."],
        "analogies": ["Only when it truly helps."],
        "audience_rules": ["No slang."],
        "sections": [
            {"name": "Hook", "guide": "Grab attention.", "max_words": 12},
            {"name": "What happened", "guide": "The facts.", "max_words": 25},
            {"name": "Closing line", "guide": "End with: {slogan}", "max_words": 13},
        ],
    },
    "quality_check": {"enabled": True, "min_score": 7},
    "models": {"ranking": "claude-haiku-4-5", "writing": "claude-sonnet-4-6",
               "review": "claude-haiku-4-5", "storyboard": "claude-sonnet-4-6"},
    "llm": {"writing_max_tokens": 6000, "review_max_tokens": 800, "storyboard_max_tokens": 6000},
    "articles": {"max_chars": 6000},
}

GOOD_SCRIPT = """## Hook
One router stopped most U.S. flights.
## What happened
A contractor set up a router wrongly, and seventeen control centers got bad data.
## Closing line
Questions remain. That's the news, made simple.
## Notes for reviewer
- Analogies: none
"""

PASSING_SCORES = '{"hook": 9, "clarity": 9, "neutrality": 9, "accuracy": 9, "problems": []}'
FAILING_SCORES = '{"hook": 4, "clarity": 8, "neutrality": 9, "accuracy": 9, "problems": ["Weak hook."]}'


class FakeLLM:
    """Stands in for LLMClient: returns prepared replies and records every call."""

    def __init__(self, replies):
        """Args: replies (list[str]) — answers to give, in order."""
        self.replies = list(replies)
        self.calls = []
        self.dry_run = False

    def ask(self, model, system_prompt, user_message, max_tokens, purpose, effort=None):
        """Record the call and return the next prepared reply."""
        self.calls.append({"model": model, "purpose": purpose, "message": user_message})
        return self.replies.pop(0)


# ---------------------------------------------------------------------------
# Q.2 — script style and self-check
# ---------------------------------------------------------------------------

def test_style_rules_come_from_the_channel_config():
    """Hook rules, banned openers, budgets and the slogan all reach the prompt."""
    prompt = build_writing_system_prompt(CONFIG)
    assert '"Today"' in prompt and "Short sentences." in prompt
    assert "## What happened" in prompt
    assert "At most 25 words." in prompt
    assert "End with: That's the news, made simple." in prompt
    assert "Analogies:" in prompt


def test_channels_without_style_keep_the_default_sections():
    """An older channel YAML with no style block still gets the five classic sections."""
    plain = {"name": "T", "niche": "n", "audience": "a", "tone": "t", "target_length_seconds": 60}
    names = []
    for section in script_sections(plain):
        names.append(section["name"])
    assert names == ["Hook", "Story", "Why it matters", "What happens next", "Closing line"]


def test_quality_check_section_is_never_spoken():
    """Reviewer-only sections don't count as narration."""
    assert is_spoken_section("Simple explanation")
    assert not is_spoken_section("Quality check")
    assert count_spoken_words(GOOD_SCRIPT + "## Quality check\nlots of extra words here\n") == \
        count_spoken_words(GOOD_SCRIPT)


def test_review_reply_is_read_and_clamped():
    """Scores are read from JSON (even wrapped in text) and kept inside 1-10."""
    scores = reviewer.parse_review_reply('Here: {"hook": 12, "clarity": 0, "neutrality": 7, '
                                         '"accuracy": 8, "problems": ["x"]}')
    assert scores["hook"] == 10 and scores["clarity"] == 1 and scores["problems"] == ["x"]
    assert reviewer.parse_review_reply("not json") is None
    assert reviewer.parse_review_reply('{"hook": 5}') is None


def test_code_checks_catch_banned_opener_missing_slogan_and_length():
    """Free code checks: banned opener, missing slogan, too many words (with budgets)."""
    bad = GOOD_SCRIPT.replace("One router", "Today one router").replace("That's the news, made simple.", "")
    long_story = "word " * 60
    bad = bad.replace("A contractor", long_story + "A contractor")
    problems = " ".join(reviewer.find_code_problems(bad, CONFIG))
    assert '"Today"' in problems
    assert "slogan" in problems
    assert "What happened" in problems and "words" in problems
    assert reviewer.find_code_problems(GOOD_SCRIPT, CONFIG) == []


def test_good_script_is_not_rewritten():
    """A draft that scores 7+ and passes code checks costs one cheap call, no rewrite."""
    llm = FakeLLM([PASSING_SCORES])
    final, report = reviewer.review_and_improve(GOOD_SCRIPT, {"title": "t", "source": "s", "url": "u"},
                                                "article", CONFIG, llm)
    assert final == GOOD_SCRIPT and report["rewritten"] is False
    assert [call["purpose"] for call in llm.calls] == ["review"]
    assert llm.calls[0]["model"] == "claude-haiku-4-5"


def test_low_score_triggers_exactly_one_rewrite():
    """Below the minimum: rewrite once with the writing model, then score again — never twice."""
    rewritten = GOOD_SCRIPT.replace("One router", "Most U.S. flights stopped. Here's why")
    llm = FakeLLM([FAILING_SCORES, rewritten, FAILING_SCORES])
    final, report = reviewer.review_and_improve(GOOD_SCRIPT, {"title": "t", "source": "s", "url": "u"},
                                                "article", CONFIG, llm)
    assert final == rewritten and report["rewritten"] is True
    assert [call["purpose"] for call in llm.calls] == ["review", "rewrite", "review"]
    assert "Weak hook." in llm.calls[1]["message"]
    section = reviewer.format_quality_section(report)
    assert section.startswith("## Quality check")
    assert "Still below minimum" in section


def test_missing_sections_use_the_channel_names():
    """The writer checks the channel's own section names."""
    assert find_missing_sections(GOOD_SCRIPT, ["Hook", "What happened", "Closing line"]) == []
    assert find_missing_sections(GOOD_SCRIPT, ["Hook", "Simple explanation"]) == ["Simple explanation"]


def test_job_allowance_includes_the_check_and_one_rewrite():
    """The dashboard reserves enough money for score + rewrite + score."""
    with_check = estimate_job(CONFIG, "generate", {"render": False})
    without = estimate_job(dict(CONFIG, quality_check={"enabled": False}), "generate", {"render": False})
    assert with_check > without


# ---------------------------------------------------------------------------
# Q.3 — voice pacing
# ---------------------------------------------------------------------------

def test_pause_lengths_follow_punctuation_hook_and_numbers():
    """After the hook, sentences, commas and before numbers each get their own pause."""
    settings = pacing.load_pacing_settings({"pacing": {"enabled": True}})
    assert pacing.choose_pause_length("traffic.", "On", False, True, settings) == settings["after_hook"]
    assert pacing.choose_pause_length("traffic.", "On", True, False, settings) == settings["between_sections"]
    assert pacing.choose_pause_length("router.", "It", False, False, settings) == settings["after_sentence"]
    assert pacing.choose_pause_length("Friday,", "a", False, False, settings) == settings["after_comma"]
    assert pacing.choose_pause_length("air", "traffic", False, False, settings) is None
    assert pacing.choose_pause_length("about", "seventeen", False, False, settings) == settings["before_key_fact"]


def make_words_audio(word_layout):
    """
    Make fake speech: a tone for each word, silence between.

    Args:
        word_layout (list[tuple]): (word, start seconds, end seconds).

    Returns:
        tuple: (samples, word timings)
    """
    total = word_layout[-1][2] + 0.5
    samples = numpy.zeros(int(total * pacing.SAMPLE_RATE), dtype=numpy.float32)
    timings = []
    for word, start, end in word_layout:
        first = int(start * pacing.SAMPLE_RATE)
        last = int(end * pacing.SAMPLE_RATE)
        tone = numpy.sin(numpy.arange(last - first) * 0.05).astype(numpy.float32) * 0.5
        samples[first:last] = tone
        timings.append({"word": word, "start": start, "end": end})
    return samples, timings


def test_long_dead_air_is_shortened_and_timings_move_with_it():
    """A 1-second gap after a sentence shrinks to the chosen pause; captions stay in sync."""
    samples, timings = make_words_audio([("Big", 0.0, 0.3), ("news.", 0.35, 0.7), ("Then", 1.7, 2.0)])
    settings = pacing.load_pacing_settings({"pacing": {"enabled": True}})
    new_samples, new_timings = pacing.reshape_pauses(samples, timings, [3], settings)
    gap = new_timings[2]["start"] - new_timings[1]["end"]
    assert gap == pytest.approx(settings["after_sentence"], abs=0.02)
    assert len(new_samples) / pacing.SAMPLE_RATE < len(samples) / pacing.SAMPLE_RATE


def test_hidden_pause_inside_a_word_is_found():
    """A word whose reported end runs into silence is trimmed to where the sound stops."""
    samples, timings = make_words_audio([("Big", 0.0, 0.3), ("news.", 0.35, 0.7), ("Then", 1.7, 2.0)])
    timings[1]["end"] = 1.6     # the engine stretched "news." over the pause
    tightened = pacing.tighten_word_ends(samples, timings)
    assert tightened[1]["end"] < 0.85


def test_edge_word_timings_keep_punctuation_tokens():
    """Edge's per-word timings line up with text.split(), dashes included."""
    timings = match_words_to_boundaries("Hi — there.", [{"start": 0.0, "end": 0.2},
                                                       {"start": 0.4, "end": 0.7}])
    words = []
    for timing in timings:
        words.append(timing["word"])
    assert words == ["Hi", "—", "there."]
    assert timings[1]["start"] == timings[1]["end"] == 0.2


def test_elevenlabs_tuning_comes_from_the_channel():
    """The channel's energy and speed override the steady defaults."""
    tuning = voice_tuning({"elevenlabs_voice_settings": {"stability": 0.35, "speed": 1.05}})
    assert tuning["stability"] == 0.35 and tuning["speed"] == 1.05
    assert tuning["similarity_boost"] == 0.75


# ---------------------------------------------------------------------------
# Q.4 — photos, storyboard, shots, captions, sound
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("licence, allowed", [
    ("CC BY-SA 4.0", True), ("CC BY 2.0", True), ("Public domain", True), ("CC0", True),
    ("CC BY-NC 2.0", False), ("CC BY-ND 4.0", False), ("GODL-India", False), ("", False),
])
def test_only_commercial_photo_licences_are_accepted(licence, allowed):
    """Non-commercial, no-derivatives and unknown licences are refused."""
    assert photos.licence_is_allowed(licence) is allowed


def test_photo_lookups_are_cached_even_when_nothing_is_found(tmp_path, monkeypatch):
    """Asking twice for a subject with no usable photo makes only one lookup."""
    monkeypatch.setattr(photos, "PHOTO_CACHE", str(tmp_path))
    calls = []

    def fake_download(subject, image_path):
        calls.append(subject)
        return None

    monkeypatch.setattr(photos, "download_usable_photo", fake_download)
    assert photos.find_photo("Nobody Special") is None
    assert photos.find_photo("Nobody Special") is None
    assert calls == ["Nobody Special"]


def test_photo_subject_must_be_named_in_the_script():
    """The AI may only ask for a photo of someone the script mentions."""
    words = ["scott", "bessent", "said", "treasury"]
    assert validate_photo_subject("Scott Bessent", words) == "Scott Bessent"
    assert validate_photo_subject("Janet Yellen", words) == ""


def test_analogy_labels_must_come_from_the_sentence():
    """The comparison must be spoken in the sentence; the real thing must be in the script."""
    beat = {"section": "Simple explanation", "text": "It's like someone shouting in a crowded room.",
            "sentence": "It's like someone shouting in a crowded room.", "word_start": 0, "word_count": 8}
    good = validate_scene({"template": "analogy", "left_label": "network flood", "left_emoji": "📡",
                           "right_label": "shouting crowded room", "right_emoji": "📣"}, beat,
                          ["network", "flood", "shouting", "crowded", "room"])
    invented = validate_scene({"template": "analogy", "left_label": "network",
                               "right_label": "traffic jam"}, beat, ["network"])
    outside_script = validate_scene({"template": "analogy", "left_label": "stock market",
                                     "right_label": "crowded room"}, beat, ["network", "crowded", "room"])
    assert outside_script["template"] == "headline"
    assert good["template"] == "analogy"
    assert invented["template"] == "headline"


def test_long_scenes_are_cut_into_short_shots(monkeypatch):
    """No shot is longer than the limit, and a reused clip starts somewhere new."""
    monkeypatch.setattr(shots, "clip_length", lambda path: 20.0)
    scenes = [
        {"start": 0.0, "end": 7.0, "clip_path": "a.mp4", "clip_credit": {"licence": "x"},
         "photo_path": "p.jpg", "photo_credit": {"licence": "y"}, "template": "headline"},
        {"start": 7.0, "end": 9.0, "clip_path": "a.mp4", "clip_credit": {"licence": "x"},
         "photo_path": None, "template": "stamp"},
    ]
    plan = shots.plan_shots(scenes, 9.0, 2.8)
    for shot in plan:
        assert shot["end"] - shot["start"] <= 2.8 + 0.001
    assert plan[0]["kind"] == "photo" and plan[1]["kind"] == "footage"
    offsets = []
    for shot in plan:
        if shot["kind"] == "footage":
            offsets.append(shot["offset"])
    assert len(set(offsets)) == len(offsets)


def test_caption_keyword_prefers_numbers_then_names():
    """Numbers beat names, names beat ordinary words, small words never win."""
    assert choose_keyword(["about", "seventeen", "centers"]) == 1
    assert choose_keyword(["the", "FAA", "paused"]) == 1
    assert choose_keyword(["it", "was", "the"]) == -1


def test_wide_captions_split_and_stay_in_the_safe_zone():
    """Long words are split into separate captions, and every caption fits the safe zone."""
    timings = []
    for number, word in enumerate("affecting travelers nationwide".split()):
        timings.append({"word": word, "start": number * 0.4, "end": number * 0.4 + 0.3})
    groups = build_caption_groups([{"word_start": 0, "word_count": 3}], timings, 3, 104)
    # Even two of these words don't fit at a readable size, so each gets its own caption.
    assert len(groups) == 3
    for group in groups:
        left, top, right, bottom = caption_box(group, 104)
        assert left >= SAFE_LEFT and right <= SAFE_RIGHT


def test_sound_effects_are_spaced_out():
    """A hook hit at 0, whooshes at section changes, pops on numbers — never too close."""
    settings = sound_design.load_sound_settings({})
    scenes = [
        {"start": 0.0, "starts_section": True, "template": "headline"},
        {"start": 0.5, "starts_section": False, "template": "big_number"},
        {"start": 4.0, "starts_section": True, "template": "icon_label"},
        {"start": 8.0, "starts_section": False, "template": "stamp"},
    ]
    planned = sound_design.plan_effect_times(scenes, settings)
    kinds = []
    for effect in planned:
        kinds.append(effect["kind"])
    assert kinds == ["hook", "transition", "pop"]


def test_mix_ducks_music_under_the_voice():
    """The mix uses sidechain ducking and places each effect at its time."""
    settings = sound_design.load_sound_settings({})
    command = sound_design.build_mix_command("voice.wav", 30.0, {"path": "music.mp3"},
                                             [{"time": 4.0, "path": "whoosh.mp3"}], settings, "out.wav")
    graph = command[command.index("-filter_complex") + 1]
    assert "sidechaincompress" in graph
    assert "adelay=4000|4000" in graph
    assert "apad=whole_dur=30.000" in graph


def test_own_music_needs_a_listed_licence(tmp_path, monkeypatch):
    """A track in assets/music is only used if credits.yaml gives its licence."""
    monkeypatch.setattr(sound_design, "MUSIC_FOLDER", str(tmp_path))
    folder = tmp_path / "news"
    folder.mkdir()
    (folder / "track.mp3").write_bytes(b"x")
    assert sound_design.find_own_music("news") is None
    (folder / "credits.yaml").write_text("track.mp3:\n  title: Calm\n  licence: YouTube Audio Library\n")
    assert sound_design.find_own_music("news")["credit"]["licence"] == "YouTube Audio Library"


def test_end_card_starts_with_the_spoken_slogan():
    """The end card appears as the narrator begins the slogan."""
    words = "That's all. That's the news, made simple.".split()
    timings = []
    for number, word in enumerate(words):
        timings.append({"word": word, "start": number * 1.0, "end": number * 1.0 + 0.5})
    assert find_end_card_start(timings, "That's the news, made simple.", 7.0) == 2.0
    assert find_end_card_start(timings, "Something else entirely.", 7.0) == 6.5


# ---------------------------------------------------------------------------
# Q.5 — quality gate
# ---------------------------------------------------------------------------

def gate_inputs():
    """A small, valid plan: one scene, one footage shot, one caption, good audio."""
    scene = {"template": "headline", "start": 0.0, "end": 2.5}
    shot = {"start": 0.0, "end": 2.5, "kind": "footage", "path": "a.mp4",
            "credit": {"source": "Pixabay", "licence": "Pixabay Content License"}}
    timings = [{"word": "Hello", "start": 0.0, "end": 0.4}, {"word": "world.", "start": 0.5, "end": 0.9}]
    groups = build_caption_groups([{"word_start": 0, "word_count": 2}], timings, 3, 104)
    soundtrack = {"credits": [{"source": "Freesound", "licence": "CC0 1.0", "title": "whoosh"}],
                  "loudness": {"lufs": -14.1, "true_peak": -1.6}}
    return [scene], [shot], groups, soundtrack


def test_gate_passes_a_clean_plan(tmp_path):
    """A plan with credits, safe captions and -14 LUFS passes and saves its report."""
    scenes, shot_list, groups, soundtrack = gate_inputs()
    report_path = tmp_path / "q.json"
    report = quality_gate.run_quality_gate(scenes, shot_list, groups, 104, soundtrack, 2.5,
                                           {"target_length_seconds": 75, "video": {}}, str(report_path))
    assert report["passed"] and report["warnings"] == []
    assert json.loads(report_path.read_text())["passed"] is True


def test_gate_stops_on_a_missing_licence(tmp_path):
    """A clip without a licence is an error, and the render stops."""
    scenes, shot_list, groups, soundtrack = gate_inputs()
    shot_list[0]["credit"] = None
    with pytest.raises(quality_gate.QualityGateFailed):
        quality_gate.run_quality_gate(scenes, shot_list, groups, 104, soundtrack, 2.5,
                                      {"video": {}}, str(tmp_path / "q.json"))


def test_gate_warns_about_long_shots_quiet_audio_and_length(tmp_path):
    """Too-long shots, off-target loudness and an over-long video are warnings."""
    scenes, shot_list, groups, soundtrack = gate_inputs()
    shot_list[0]["end"] = 5.0
    soundtrack["loudness"] = {"lufs": -20.5, "true_peak": -3.0}
    report = quality_gate.run_quality_gate(scenes, shot_list, groups, 104, soundtrack, 100.0,
                                           {"target_length_seconds": 75, "video": {}},
                                           str(tmp_path / "q.json"))
    text = " ".join(report["warnings"])
    assert "lasts 5.0s" in text and "-20.5 LUFS" in text and "target is 75s" in text
    assert report["passed"]


def test_gate_flags_captions_outside_the_safe_zone():
    """A caption whose box crosses the safe zone is reported."""
    wide = {"words": [{"text": "W" * 40, "start": 0.0, "end": 1.0}], "keyword": -1,
            "start": 0.0, "end": 1.0}
    assert quality_gate.check_caption_safe_zone([wide], 104)


def test_gate_flags_missing_graphics():
    """A scene without a graphic template is an error."""
    errors, warnings = quality_gate.check_missing_visuals(
        [{"template": ""}], [{"start": 0, "end": 1, "kind": "gradient"}])
    assert errors and warnings


def test_a_number_range_never_becomes_one_big_number():
    """'thirty to fifty billion' must not be shown as just '$50B'."""
    assert is_number_range("Duffy is seeking thirty to fifty billion dollars")
    assert not is_number_range("Seventeen of twenty centers got bad data")
    beat = {"section": "What happens next", "text": "Duffy is seeking thirty to fifty billion dollars.",
            "sentence": "Duffy is seeking thirty to fifty billion dollars.", "word_start": 0, "word_count": 8}
    scene = validate_scene({"template": "big_number", "value": 50000000000, "prefix": "$",
                            "label": "dollars"}, beat, ["duffy", "seeking", "dollars"])
    assert scene["template"] == "headline"

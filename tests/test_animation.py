# =============================================================
# tests/test_animation.py — tests for the animated video engine
#
# Covers src/video/animation/: easing maths, icon file names,
# storyboard planning and its accuracy checks, captions, the
# scene graphics, and footage selection.
#
# Everything runs offline and costs nothing: there are no AI
# calls, no downloads and no FFmpeg. Where real code would
# download an icon or a stock clip, the test swaps in a fake
# function instead (pytest's "monkeypatch" tool does the swap
# and undoes it when the test ends).
# =============================================================

import json

from PIL import Image

from src.video.animation import footage, scenes
from src.video.animation.drawing import VIDEO_HEIGHT, VIDEO_WIDTH, hex_to_rgb
from src.video.animation.easing import (
    clamp,
    ease_in_cubic,
    ease_out_back,
    ease_out_cubic,
    lerp,
    progress,
)
from src.video.animation.icons import emoji_to_filename
from src.video.animation.overlays import build_caption_groups, find_caption_group
from src.video.animation.renderer import (
    add_section_details,
    get_scene_colors,
    load_animation_settings,
)
from src.video.animation.storyboard import (
    add_scene_times,
    find_attribution_words,
    find_numbers_in_text,
    find_unsupported_words,
    get_storyboard,
    parse_storyboard_reply,
    split_into_beats,
    storyboard_path_for,
    validate_scene,
)
from src.video.render import _build_ffmpeg_command


# Colours handed to the scene-drawing functions in these tests.
TEST_COLORS = {"accent": (255, 210, 63), "second": (59, 232, 176),
               "text": (255, 255, 255)}


def make_beat(text, sentence=None, section="Story", word_start=0):
    """
    Build one beat dict for a test, the way split_into_beats() would.

    Args:
        text (str): The words spoken during the beat.
        sentence (str or None): The full sentence the beat comes from.
                                Defaults to the beat's own text.
        section (str): The script section the beat is in.
        word_start (int): Index of the beat's first word in the script.

    Returns:
        dict: A beat with section, text, sentence, word_start, word_count.
    """
    return {
        "section": section,
        "text": text,
        "sentence": sentence if sentence is not None else text,
        "word_start": word_start,
        "word_count": len(text.split()),
    }


def fake_missing_icon(emoji, size):
    """
    Stand-in for get_icon() that pretends no icon picture is available.

    Tests use this so that drawing a scene never downloads anything.

    Args:
        emoji (str): The emoji asked for (ignored).
        size (int): The size asked for (ignored).

    Returns:
        None: Always, meaning "no icon".
    """
    return None


def fake_clip_search(query, variant=0):
    """
    Stand-in for the stock video search that invents a clip file name.

    Args:
        query (str): The footage topic searched for.
        variant (int): Which search result was asked for.

    Returns:
        str: A made-up path such as "american flag-1.mp4".
    """
    return f"{query}-{variant}.mp4"


def make_word_timings(text, seconds_per_word=0.4):
    """
    Build fake voiceover timings: every word lasts the same length.

    Args:
        text (str): The spoken text.
        seconds_per_word (float): How long each word lasts.

    Returns:
        list[dict]: One {"word", "start", "end"} per word.
    """
    timings = []
    for index, word in enumerate(text.split()):
        timings.append({
            "word": word,
            "start": round(index * seconds_per_word, 3),
            "end": round((index + 1) * seconds_per_word, 3),
        })
    return timings


# ---------------------------------------------------------------------------
# easing.py
# ---------------------------------------------------------------------------

class TestEasing:
    """Tests for the maths that makes motion smooth."""

    def test_clamp_limits_values(self):
        """Values outside 0–1 are pulled back to the nearest edge."""
        assert clamp(1.7) == 1.0
        assert clamp(-0.2) == 0.0
        assert clamp(0.4) == 0.4

    def test_progress_before_during_after(self):
        """Progress is 0 before the start, 1 after the end, a fraction between."""
        assert progress(now=0.5, start=1.0, duration=1.0) == 0.0
        assert progress(now=1.5, start=1.0, duration=1.0) == 0.5
        assert progress(now=9.0, start=1.0, duration=1.0) == 1.0

    def test_progress_with_zero_duration(self):
        """A zero-length animation is finished as soon as it starts."""
        assert progress(now=2.0, start=1.0, duration=0) == 1.0
        assert progress(now=0.5, start=1.0, duration=0) == 0.0

    def test_lerp_blends_two_numbers(self):
        """A quarter of the way from 100 to 200 is 125."""
        assert lerp(100, 200, 0.25) == 125

    def test_easing_curves_start_at_zero_and_end_at_one(self):
        """Every curve must begin exactly at 0 and finish exactly at 1."""
        for curve in (ease_out_cubic, ease_in_cubic, ease_out_back):
            assert abs(curve(0.0)) < 1e-9
            assert abs(curve(1.0) - 1.0) < 1e-9

    def test_ease_out_back_overshoots(self):
        """The "pop" curve goes past 1.0 partway through, then settles."""
        assert ease_out_back(0.7) > 1.0


# ---------------------------------------------------------------------------
# icons.py
# ---------------------------------------------------------------------------

class TestEmojiToFilename:
    """Tests for turning an emoji into its Noto Emoji file name."""

    def test_simple_emoji(self):
        """A one-part emoji becomes one hexadecimal number."""
        assert emoji_to_filename("🗳") == "emoji_u1f5f3.png"

    def test_invisible_marker_is_dropped(self):
        """The invisible "colour emoji" marker is not part of the file name."""
        assert emoji_to_filename("🏛️") == "emoji_u1f3db.png"

    def test_flag_has_two_parts(self):
        """A flag is two code points joined by an underscore."""
        assert emoji_to_filename("🇺🇸") == "emoji_u1f1fa_1f1f8.png"

    def test_empty_text(self):
        """Empty input gives an empty file name instead of crashing."""
        assert emoji_to_filename("") == ""


# ---------------------------------------------------------------------------
# storyboard.py — splitting the script into beats
# ---------------------------------------------------------------------------

class TestSplitIntoBeats:
    """Tests for cutting the spoken script into beats."""

    def test_one_beat_per_short_sentence(self):
        """Two short sentences become two beats with the right word positions."""
        beats = split_into_beats([{"section": "Hook", "text": "Big news today. Here it is."}])
        assert len(beats) == 2
        assert beats[0]["text"] == "Big news today."
        assert beats[0]["word_start"] == 0
        assert beats[1]["text"] == "Here it is."
        assert beats[1]["word_start"] == 3

    def test_word_counts_cover_the_whole_script(self):
        """No word may be lost or counted twice — captions depend on this."""
        text = ("Representative Jane Doe, a member from Ohio, is forcing a vote "
                "this week in the chamber to remove the secretary. It may fail.")
        beats = split_into_beats([{"section": "Story", "text": text}])
        total = 0
        position = 0
        for beat in beats:
            assert beat["word_start"] == position
            position += beat["word_count"]
            total += beat["word_count"]
        assert total == len(text.split())

    def test_long_sentence_is_split(self):
        """A sentence longer than the limit becomes several shorter beats."""
        text = ("The committee met for many hours on Tuesday, and members voted "
                "to delay the bill until the next session begins in January.")
        beats = split_into_beats([{"section": "Story", "text": text}], max_words=12)
        assert len(beats) >= 2
        for beat in beats:
            assert beat["word_count"] <= 12

    def test_beats_remember_their_sentence(self):
        """Each piece of a split sentence keeps the full sentence for checking."""
        text = ("The committee met for many hours on Tuesday, and members voted "
                "to delay the bill until the next session begins in January.")
        beats = split_into_beats([{"section": "Story", "text": text}], max_words=12)
        for beat in beats:
            assert beat["sentence"] == text

    def test_abbreviation_does_not_end_a_sentence(self):
        """"Dr." and "U.S." contain full stops but do not end the sentence."""
        beats = split_into_beats([{"section": "Story",
                                   "text": "Dr. Smith left the U.S. today."}])
        assert len(beats) == 1

    def test_very_short_piece_joins_previous_beat(self):
        """A one-word sentence is too short to show, so it joins the beat before."""
        beats = split_into_beats([{"section": "Story",
                                   "text": "The vote failed today. Why?"}])
        assert len(beats) == 1
        assert beats[0]["word_count"] == 5

    def test_sections_are_kept(self):
        """Each beat records which script section it came from."""
        beats = split_into_beats([
            {"section": "Hook", "text": "Big news today."},
            {"section": "Story", "text": "Here is what happened."},
        ])
        assert beats[0]["section"] == "Hook"
        assert beats[1]["section"] == "Story"


# ---------------------------------------------------------------------------
# storyboard.py — accuracy checks
# ---------------------------------------------------------------------------

class TestAccuracyHelpers:
    """Tests for the small functions the accuracy checks are built from."""

    def test_finds_digits_and_number_words(self):
        """Numbers are found whether written as digits or as words."""
        numbers = find_numbers_in_text("within two days, for more than 90 days")
        assert 90.0 in numbers
        assert 2.0 in numbers

    def test_finds_numbers_with_commas(self):
        """"27,000" is read as twenty-seven thousand."""
        assert find_numbers_in_text("a drop of 27,000 feet") == [27000.0]

    def test_unsupported_words_are_reported(self):
        """Words the script never says are listed."""
        allowed = ["republicans", "lost", "control"]
        assert find_unsupported_words("Democrats won", allowed) == ["democrats", "won"]

    def test_word_forms_are_accepted(self):
        """"Impeaching" is accepted when the script says "impeach"."""
        assert find_unsupported_words("Impeaching", ["impeach"]) == []

    def test_meaning_changing_words_are_not_free(self):
        """"not" must come from the script — it is never a free filler word."""
        assert find_unsupported_words("not worried", ["worried"]) == ["not"]

    def test_attribution_words_are_found(self):
        """Words that mark a claim or a prediction are detected."""
        assert find_attribution_words("Massie argues that he broke the law") == ["argues"]
        assert find_attribution_words("The vote is expected to fail") == ["expected"]
        assert find_attribution_words("The vote failed") == []


class TestValidateScene:
    """Tests for checking one scene from the AI before it is used."""

    def test_valid_scene_is_kept(self):
        """A stamp that only uses the beat's words is accepted as it is."""
        beat = make_beat("Impeaching a cabinet secretary is extremely rare,")
        scene = validate_scene(
            {"template": "stamp", "text": "Extremely rare", "footage": "Courthouse"},
            beat,
        )
        assert scene["template"] == "stamp"
        assert scene["text"] == "Extremely rare"
        assert scene["footage"] == "courthouse"
        assert scene["spoken_text"] == beat["text"]

    def test_unknown_template_becomes_headline(self):
        """A template name we do not have is replaced by the plain headline."""
        beat = make_beat("The vote failed today.")
        scene = validate_scene({"template": "hologram", "text": "Wow"}, beat)
        assert scene["template"] == "headline"

    def test_missing_scene_becomes_headline(self):
        """If the AI gave nothing for a beat, the beat's own words are shown."""
        beat = make_beat("The vote failed today.")
        scene = validate_scene(None, beat)
        assert scene["template"] == "headline"
        assert scene["text"] == "The vote failed today"

    def test_invented_number_is_rejected(self):
        """A counter may only show a number that is spoken in the beat."""
        beat = make_beat("for more than 90 days without approval")
        good = validate_scene({"template": "big_number", "value": 90, "label": "days"}, beat)
        bad = validate_scene({"template": "big_number", "value": 500, "label": "days"}, beat)
        assert good["template"] == "big_number"
        assert bad["template"] == "headline"

    def test_number_unit_must_be_a_symbol(self):
        """A word such as "day" is not allowed as a suffix (it reads "90day")."""
        beat = make_beat("for more than 90 days without approval")
        scene = validate_scene(
            {"template": "big_number", "value": 90, "suffix": "day", "prefix": "$"}, beat
        )
        assert scene["suffix"] == ""
        assert scene["prefix"] == "$"

    def test_invented_fact_is_rejected(self):
        """Text using words from outside the sentence is thrown away."""
        beat = make_beat("after Republicans lost control of both chambers")
        scene = validate_scene(
            {"template": "headline", "text": "Democrats won the elections"}, beat
        )
        assert scene["template"] == "headline"
        assert "Democrats" not in scene["text"]

    def test_claim_must_keep_its_attribution(self):
        """"X argues Y" may not appear on screen as plain "Y"."""
        beat = make_beat("Massie argues that Hegseth broke the law")
        dropped = validate_scene(
            {"template": "headline", "text": "Hegseth broke the law"}, beat
        )
        kept = validate_scene(
            {"template": "headline", "text": "Massie argues Hegseth broke the law"}, beat
        )
        assert "argues" in dropped["text"].lower()   # replaced by the fallback
        assert kept["text"] == "Massie argues Hegseth broke the law"

    def test_stamp_is_strict_about_claims_anywhere_in_the_sentence(self):
        """A stamp looks like a verdict, so it cannot be used inside a claim."""
        sentence = "Massie argues he broke the law, which he says violates the Constitution."
        beat = make_beat("by engaging in hostilities abroad", sentence=sentence + " by engaging in hostilities abroad")
        scene = validate_scene({"template": "stamp", "text": "Broke the law"}, beat)
        assert scene["template"] == "headline"

    def test_person_name_must_be_spoken(self):
        """A name card is only allowed for a name said in that beat."""
        beat = make_beat("Representative Thomas Massie is forcing a vote")
        good = validate_scene({"template": "person", "name": "Thomas Massie"}, beat)
        bad = validate_scene({"template": "person", "name": "John Smith"}, beat)
        assert good["template"] == "person"
        assert bad["template"] == "headline"

    def test_unsupported_role_is_dropped_but_card_kept(self):
        """A role the script never states is removed; the name card stays."""
        beat = make_beat("Representative Thomas Massie is forcing a vote")
        scene = validate_scene(
            {"template": "person", "name": "Thomas Massie", "role": "Senate leader"},
            beat,
        )
        assert scene["template"] == "person"
        assert scene["role"] == ""

    def test_calendar_emoji_is_removed(self):
        """Calendar emoji show a fixed date, so they are never used."""
        beat = make_beat("The vote is this week.")
        scene = validate_scene(
            {"template": "headline", "text": "Vote this week", "emoji": "📅"}, beat
        )
        assert scene["emoji"] == ""

    def test_list_needs_two_supported_items(self):
        """A list with only one usable item falls back to a headline."""
        beat = make_beat("The bill cuts taxes and raises spending.")
        scene = validate_scene({
            "template": "bullet_list",
            "title": "The bill",
            "items": [{"emoji": "💵", "text": "cuts taxes"},
                      {"emoji": "🚀", "text": "launches rockets"}],
        }, beat)
        assert scene["template"] == "headline"


class TestParseStoryboardReply:
    """Tests for turning the AI's raw answer into a list of scenes."""

    def test_reads_json_inside_a_code_block(self):
        """The JSON is found even if the model wraps it in markdown."""
        beats = [make_beat("The vote failed today.")]
        reply = '```json\n[{"beat": 1, "template": "stamp", "text": "Vote failed", "footage": "voting"}]\n```'
        scenes_out = parse_storyboard_reply(reply, beats)
        assert scenes_out[0]["template"] == "stamp"

    def test_unreadable_answer_gives_fallback_scenes(self):
        """A reply that is not JSON still gives one safe scene per beat."""
        beats = [make_beat("The vote failed today."),
                 make_beat("Nobody was surprised.", word_start=4)]
        scenes_out = parse_storyboard_reply("Sorry, I cannot help.", beats)
        assert len(scenes_out) == 2
        assert scenes_out[0]["template"] == "headline"

    def test_missing_beat_is_filled_in(self):
        """If the AI skips a beat, that beat gets the fallback headline."""
        beats = [make_beat("The vote failed today."),
                 make_beat("Nobody was surprised.", word_start=4)]
        reply = '[{"beat": 2, "template": "stamp", "text": "Nobody surprised"}]'
        scenes_out = parse_storyboard_reply(reply, beats)
        assert scenes_out[0]["template"] == "headline"
        assert scenes_out[1]["template"] == "stamp"


class TestGetStoryboard:
    """Tests for loading / creating the saved storyboard file."""

    def write_script(self, folder):
        """Write a tiny script file and return its path."""
        path = folder / "story.md"
        path.write_text(
            "# Title\n\n## Hook\nBig news today.\n\n## Story\nThe vote failed.\n\n"
            "## Sources\n- nothing spoken here\n",
            encoding="utf-8",
        )
        return str(path)

    def test_without_ai_makes_free_fallback_storyboard(self, tmp_path):
        """With no AI client, plain headlines are used and saved."""
        script_path = self.write_script(tmp_path)
        scenes_out = get_storyboard(script_path, {"name": "Test"}, llm=None)

        assert len(scenes_out) == 2
        assert scenes_out[0]["section"] == "Hook"
        with open(storyboard_path_for(script_path), encoding="utf-8") as f:
            saved = json.load(f)
        assert saved["source"] == "fallback"

    def test_saved_storyboard_is_reused(self, tmp_path):
        """A hand-edited storyboard is loaded instead of being made again."""
        script_path = self.write_script(tmp_path)
        get_storyboard(script_path, {"name": "Test"}, llm=None)

        path = storyboard_path_for(script_path)
        with open(path, encoding="utf-8") as f:
            saved = json.load(f)
        saved["scenes"][0]["text"] = "EDITED BY HAND"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(saved, f)

        scenes_out = get_storyboard(script_path, {"name": "Test"}, llm=None)
        assert scenes_out[0]["text"] == "EDITED BY HAND"


class TestAddSceneTimes:
    """Tests for matching scenes to the voiceover timing."""

    def test_scenes_cover_the_video_without_gaps(self):
        """Each scene ends exactly when the next begins; the last ends at the end."""
        text = "Big news today. The vote failed."
        beats = split_into_beats([{"section": "Hook", "text": text}])
        scene_list = [validate_scene(None, beat) for beat in beats]
        add_scene_times(scene_list, make_word_timings(text), total_duration=3.0)

        assert scene_list[0]["start"] == 0.0
        assert scene_list[0]["end"] == scene_list[1]["start"]
        assert scene_list[1]["end"] == 3.0

    def test_scene_starts_just_before_its_first_word(self):
        """The graphic leads the voice slightly so it lands with the word."""
        text = "Big news today. The vote failed."
        beats = split_into_beats([{"section": "Hook", "text": text}])
        scene_list = [validate_scene(None, beat) for beat in beats]
        timings = make_word_timings(text)
        add_scene_times(scene_list, timings, total_duration=3.0)

        first_word_time = timings[3]["start"]
        assert scene_list[1]["start"] < first_word_time
        assert first_word_time - scene_list[1]["start"] < 0.2


# ---------------------------------------------------------------------------
# overlays.py — captions
# ---------------------------------------------------------------------------

class TestCaptions:
    """Tests for grouping spoken words into captions."""

    def test_captions_do_not_cross_scenes(self):
        """A caption never mixes the last word of one scene with the next."""
        text = "One two three four five six seven"
        scene_list = [{"word_start": 0, "word_count": 4},
                      {"word_start": 4, "word_count": 3}]
        groups = build_caption_groups(scene_list, make_word_timings(text), 3)

        # Scene one has 4 words: shown as 2 + 2 (never more than 3 at once,
        # and never a lonely last word), and scene two starts fresh.
        assert [word["text"] for word in groups[0]["words"]] == ["ONE", "TWO"]
        assert [word["text"] for word in groups[1]["words"]] == ["THREE", "FOUR"]
        assert [word["text"] for word in groups[2]["words"]] == ["FIVE", "SIX", "SEVEN"]

    def test_punctuation_is_removed_and_text_is_capitalised(self):
        """Captions show clean capital letters."""
        scene_list = [{"word_start": 0, "word_count": 2}]
        groups = build_caption_groups(scene_list, make_word_timings("Hello world."), 3)
        assert [word["text"] for word in groups[0]["words"]] == ["HELLO", "WORLD"]

    def test_captions_break_at_commas(self):
        """A comma ends a caption, so captions follow the spoken phrases."""
        scene_list = [{"word_start": 0, "word_count": 3}]
        groups = build_caption_groups(scene_list, make_word_timings("Well, hello world."), 3)
        assert [word["text"] for word in groups[0]["words"]] == ["WELL"]
        assert [word["text"] for word in groups[1]["words"]] == ["HELLO", "WORLD"]

    def test_caption_stays_until_the_next_one(self):
        """Each caption remains on screen until the following caption starts."""
        text = "One two three four five six"
        scene_list = [{"word_start": 0, "word_count": 6}]
        groups = build_caption_groups(scene_list, make_word_timings(text), 3)
        assert groups[0]["end"] == groups[1]["start"]

    def test_find_caption_group(self):
        """The right caption is found for a moment in time; none in silence."""
        text = "One two three four five six"
        scene_list = [{"word_start": 0, "word_count": 6}]
        groups = build_caption_groups(scene_list, make_word_timings(text), 3)
        assert find_caption_group(groups, 0.1) is groups[0]
        assert find_caption_group(groups, 1.5) is groups[1]
        assert find_caption_group(groups, 99.0) is None


# ---------------------------------------------------------------------------
# scenes.py — the animated graphics
# ---------------------------------------------------------------------------

# One example scene per template, used to check that each one draws.
EXAMPLE_SCENES = [
    {"template": "headline", "text": "Impeaching his own cabinet", "highlight": "own", "emoji": "⚖️"},
    {"template": "icon_label", "emoji": "🏛️", "title": "House of Representatives", "subtitle": "Vote this week"},
    {"template": "person", "name": "Thomas Massie", "role": "Republican from Kentucky"},
    {"template": "big_number", "value": 90, "prefix": "", "suffix": "", "label": "days without approval", "emoji": ""},
    {"template": "versus", "left_emoji": "", "left_label": "Act now", "right_emoji": "", "right_label": "Democrats take over"},
    {"template": "bullet_list", "title": "The argument", "items": [{"emoji": "", "text": "No approval"}, {"emoji": "", "text": "Violates the Constitution"}]},
    {"template": "timeline", "title": "What happens next", "steps": ["Resolution introduced", "Vote within two days"]},
    {"template": "bar_chart", "title": "Seats", "unit": "", "bars": [{"label": "Yes", "value": 230}, {"label": "No", "value": 205}]},
    {"template": "stamp", "text": "Extremely rare", "emoji": ""},
]


def draw_example(scene, time_in_scene):
    """
    Draw one scene onto a plain grey frame and return the frame.

    Args:
        scene (dict): A scene from EXAMPLE_SCENES.
        time_in_scene (float): Seconds since the scene began.

    Returns:
        PIL.Image: The drawn frame.
    """
    frame = Image.new("RGB", (VIDEO_WIDTH, VIDEO_HEIGHT), (90, 90, 90))
    # Copy the scene so a cached stamp picture is not shared between calls.
    scenes.draw_scene(frame, dict(scene), time_in_scene, 4.0, TEST_COLORS)
    return frame


class TestScenes:
    """Tests for the nine animated graphic templates."""

    def test_every_template_draws_something(self, monkeypatch):
        """Each template changes the frame once its animation has played."""
        # No downloads in tests: pretend no icon pictures are available.
        monkeypatch.setattr(scenes, "get_icon", fake_missing_icon)
        blank = Image.new("RGB", (VIDEO_WIDTH, VIDEO_HEIGHT), (90, 90, 90))

        for scene in EXAMPLE_SCENES:
            frame = draw_example(scene, 3.0)
            assert frame.size == (VIDEO_WIDTH, VIDEO_HEIGHT)
            assert frame.tobytes() != blank.tobytes(), scene["template"]

    def test_every_template_is_animated(self, monkeypatch):
        """A frame early in the scene differs from one later — proof of motion."""
        monkeypatch.setattr(scenes, "get_icon", fake_missing_icon)

        for scene in EXAMPLE_SCENES:
            early = draw_example(scene, 0.15)
            later = draw_example(scene, 3.0)
            assert early.tobytes() != later.tobytes(), scene["template"]

    def test_unknown_template_does_not_crash(self, monkeypatch):
        """An unrecognised template name falls back to the headline drawer."""
        monkeypatch.setattr(scenes, "get_icon", fake_missing_icon)
        draw_example({"template": "hologram", "text": "Still works"}, 1.0)


# ---------------------------------------------------------------------------
# footage.py — choosing background clips
# ---------------------------------------------------------------------------

class TestFootage:
    """Tests for giving every scene a background clip."""

    def test_topic_rotates_through_its_clips(self, monkeypatch):
        """Using one topic three times gives result 1, result 2, then result 1."""
        monkeypatch.setattr(footage, "_search_for_clip", fake_clip_search)
        scene_list = [{"footage": "american flag"}, {"footage": "american flag"},
                      {"footage": "american flag"}]
        found = footage.find_footage_for_scenes(scene_list, {"american flag": 2})

        assert found == 3
        assert scene_list[0]["clip_path"] == "american flag-0.mp4"
        assert scene_list[1]["clip_path"] == "american flag-1.mp4"
        assert scene_list[2]["clip_path"] == "american flag-0.mp4"

    def test_unapproved_topic_uses_a_general_one(self, monkeypatch):
        """A topic that is not on the channel's list is never searched for."""
        searched = []

        def fake_search(query, variant=0):
            searched.append(query)
            return f"{query}.mp4"

        monkeypatch.setattr(footage, "_search_for_clip", fake_search)
        scene_list = [{"footage": "beach party"}]
        footage.find_footage_for_scenes(scene_list, {"capitol building": 1, "voting": 1})

        assert "beach party" not in searched
        assert scene_list[0]["clip_path"] == "capitol building.mp4"

    def test_scene_without_clip_reuses_previous_clip(self, monkeypatch):
        """If nothing can be found, the previous scene's clip keeps playing."""
        def fake_search(query, variant=0):
            if query == "voting":
                return "voting.mp4"
            return None

        # With no approved list, find_clip() is used; it calls the search.
        monkeypatch.setattr(footage, "_search_for_clip", fake_search)
        scene_list = [{"footage": "voting"}, {"footage": "nothing here"}]
        found = footage.find_footage_for_scenes(scene_list, {})

        assert found == 2
        assert scene_list[1]["clip_path"] == "voting.mp4"

    def test_fallback_background_is_never_black_and_moves(self):
        """Even with no footage at all, the frame is coloured and animated."""
        top = hex_to_rgb("#101A3A")
        bottom = hex_to_rgb("#3A1050")
        first = footage.draw_fallback_background(0.0, top, bottom, (255, 210, 63))
        second = footage.draw_fallback_background(2.0, top, bottom, (255, 210, 63))

        assert first.size == (VIDEO_WIDTH, VIDEO_HEIGHT)
        assert first.getpixel((540, 960)) != (0, 0, 0)
        assert first.tobytes() != second.tobytes()


# ---------------------------------------------------------------------------
# renderer.py and render.py — settings and the FFmpeg command
# ---------------------------------------------------------------------------

class TestRendererHelpers:
    """Tests for the small helpers around the main render loop."""

    def test_settings_use_defaults_then_channel_values(self):
        """Channel values replace defaults; everything else keeps its default."""
        config = {"video": {"animation": {"fps": 24}}}
        settings = load_animation_settings(config)
        assert settings["fps"] == 24
        assert settings["footage_brightness"] == 0.6

    def test_scene_colors_cycle(self):
        """Each scene takes the next accent colour, wrapping round at the end."""
        settings = load_animation_settings({"video": {"animation": {
            "accent_colors": ["#FF0000", "#00FF00"]}}})
        assert get_scene_colors(0, settings)["accent"] == (255, 0, 0)
        assert get_scene_colors(1, settings)["accent"] == (0, 255, 0)
        assert get_scene_colors(2, settings)["accent"] == (255, 0, 0)

    def test_section_details(self):
        """Section starts are marked; the hook has no label (the brand bug shows the name)."""
        scene_list = [
            {"section": "Hook", "start": 0.0},
            {"section": "Story", "start": 5.0},
            {"section": "Story", "start": 9.0},
        ]
        add_section_details(scene_list, "News Explained")

        assert scene_list[0]["section_label"] == ""
        assert scene_list[1]["starts_section"] is True
        assert scene_list[2]["starts_section"] is False
        assert scene_list[2]["section_start"] == 5.0
        assert scene_list[2]["section_label"] == "STORY"

    def test_ffmpeg_command_accepts_piped_frames(self):
        """The animated renderer can swap in its own video input flags."""
        flags = ["-f", "rawvideo", "-i", "-"]
        command = _build_ffmpeg_command(
            concat_path=None, audio_path="voice.mp3", video_path="out.mp4",
            duration=10.0, video_input_flags=flags,
        )
        assert "rawvideo" in command
        assert "concat" not in command
        assert command[-1] == "out.mp4"

    def test_ffmpeg_command_default_still_uses_concat(self):
        """The classic renderer's command is unchanged."""
        command = _build_ffmpeg_command(
            concat_path="frames.txt", audio_path="voice.mp3",
            video_path="out.mp4", duration=10.0,
        )
        assert "concat" in command
        assert "frames.txt" in command

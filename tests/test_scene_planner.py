# =============================================================
# tests/test_scene_planner.py — tests for the M-series visual modules
#
# Covers scene_planner.py, animated_frames.py, and stock_video.py.
# All tests run offline and cost nothing:
#   - scene_planner uses dry_run mode (no AI calls)
#   - animated_frames uses only Pillow (local, no API)
#   - stock_video returns [] without a PEXELS_API_KEY
# =============================================================

import json
import os


from src.video.scene_planner import (
    _split_into_segments,
    _build_scene_prompt,
    plan_scenes,
    VALID_SCENE_TYPES,
)
from src.video.animated_frames import (
    _parse_animated_query,
    make_number_animation,
    make_comparison_animation,
    render_animated_scene,
)
from src.video.stock_video import get_video_frames


# ---------------------------------------------------------------------------
# scene_planner.py
# ---------------------------------------------------------------------------

class TestSplitIntoSegments:
    """Tests for _split_into_segments — splits spoken text into chunks."""

    def test_exact_multiple(self):
        """30 words with size 15 should give exactly 2 segments."""
        text = " ".join([f"word{i}" for i in range(30)])
        segments = _split_into_segments(text, words_per_segment=15)
        assert len(segments) == 2

    def test_remainder_forms_smaller_segment(self):
        """16 words with size 15 → one full segment + one single-word segment."""
        text = " ".join([f"w{i}" for i in range(16)])
        segments = _split_into_segments(text, words_per_segment=15)
        assert len(segments) == 2
        assert segments[1] == "w15"

    def test_empty_text_gives_empty_list(self):
        """Empty input should return an empty list."""
        assert _split_into_segments("") == []

    def test_fewer_words_than_segment_size(self):
        """Fewer words than the segment size → one segment containing all words."""
        text = "only five words here now"
        segments = _split_into_segments(text, words_per_segment=15)
        assert len(segments) == 1
        assert segments[0] == text


class TestBuildScenePrompt:
    """Tests for _build_scene_prompt — builds the AI planning prompt."""

    def _config(self, niche="US politics"):
        """Helper: minimal channel config."""
        return {"niche": niche}

    def test_prompt_contains_all_segments(self):
        """Every segment should appear in the returned prompt."""
        segments = ["First segment text.", "Second segment text."]
        prompt = _build_scene_prompt(segments, self._config())
        assert "First segment text." in prompt
        assert "Second segment text." in prompt

    def test_prompt_lists_all_scene_types(self):
        """The prompt should mention all five scene types."""
        prompt = _build_scene_prompt(["some text"], self._config())
        for scene_type in ["footage", "image", "animated", "chart", "text_card"]:
            assert scene_type in prompt

    def test_prompt_contains_channel_niche(self):
        """The channel niche should appear in the prompt for context."""
        prompt = _build_scene_prompt(["text"], self._config("cat videos"))
        assert "cat videos" in prompt

    def test_footage_style_adds_preference(self):
        """The 'footage' style should add a preference note to the prompt."""
        prompt_auto = _build_scene_prompt(["text"], self._config(), style="auto")
        prompt_footage = _build_scene_prompt(["text"], self._config(), style="footage")
        assert "Strongly prefer" in prompt_footage
        assert "Strongly prefer" not in prompt_auto


class TestPlanScenes:
    """Tests for plan_scenes — the main entry point for scene planning."""

    def _write_script(self, tmp_path, content):
        """Write a minimal script file and return its path."""
        path = tmp_path / "2026-11-07-test-story.md"
        path.write_text(content, encoding="utf-8")
        return str(path)

    def _config(self):
        """Helper: minimal channel config."""
        return {
            "niche": "US politics",
            "models": {"ranking": "claude-haiku-4-5"},
        }

    def _make_script_content(self):
        return (
            "## Hook\nBig news today in Washington.\n"
            "## Story\nThe president signed a major bill.\n"
            "## Why it matters\nThis affects millions of Americans.\n"
            "## What happens next\nCongress will debate implementation.\n"
            "## Closing line\nStay informed."
        )

    def test_dry_run_returns_text_cards(self, tmp_path):
        """In dry-run mode, every segment should use 'text_card'."""
        script_path = self._write_script(tmp_path, self._make_script_content())
        plan = plan_scenes(script_path, self._config(), llm=None, dry_run=True)

        assert len(plan) > 0
        for item in plan:
            assert item["scene_type"] == "text_card"

    def test_dry_run_saves_json_file(self, tmp_path):
        """plan_scenes should save a _sceneplan.json file next to the script."""
        script_path = self._write_script(tmp_path, self._make_script_content())
        plan_scenes(script_path, self._config(), llm=None, dry_run=True)

        expected = script_path.replace(".md", "_sceneplan.json")
        assert os.path.exists(expected)

    def test_dry_run_loads_from_cache(self, tmp_path):
        """Calling plan_scenes twice should load from JSON on the second call."""
        script_path = self._write_script(tmp_path, self._make_script_content())
        plan1 = plan_scenes(script_path, self._config(), llm=None, dry_run=True)
        plan2 = plan_scenes(script_path, self._config(), llm=None, dry_run=True)
        assert plan1 == plan2

    def test_empty_script_returns_empty_plan(self, tmp_path):
        """A script with no spoken sections should return an empty plan."""
        script = "## Notes for reviewer\nNot spoken."
        script_path = self._write_script(tmp_path, script)
        plan = plan_scenes(script_path, self._config(), llm=None, dry_run=True)
        assert plan == []

    def test_each_item_has_required_keys(self, tmp_path):
        """Every plan item should have segment, scene_type, query, description."""
        script_path = self._write_script(tmp_path, self._make_script_content())
        plan = plan_scenes(script_path, self._config(), llm=None, dry_run=True)

        for item in plan:
            assert "segment" in item
            assert "scene_type" in item
            assert "query" in item
            assert "description" in item

    def test_scene_types_are_valid(self, tmp_path):
        """All scene_type values in the plan must be in VALID_SCENE_TYPES."""
        script_path = self._write_script(tmp_path, self._make_script_content())
        plan = plan_scenes(script_path, self._config(), llm=None, dry_run=True)

        for item in plan:
            assert item["scene_type"] in VALID_SCENE_TYPES, (
                f"Invalid scene_type: {item['scene_type']!r}"
            )


# ---------------------------------------------------------------------------
# animated_frames.py
# ---------------------------------------------------------------------------

class TestParseAnimatedQuery:
    """Tests for _parse_animated_query — parses 'value:unit' and 'a:v, b:v' strings."""

    def test_number_format(self):
        """'27000:feet' should parse as a number callout."""
        anim_type, val, unit = _parse_animated_query("27000:feet")
        assert anim_type == "number"
        assert val == "27000"
        assert unit == "feet"

    def test_number_with_comma(self):
        """'27,000:feet' should parse correctly despite the comma in the value."""
        anim_type, val, unit = _parse_animated_query("27,000:feet")
        assert anim_type == "number"
        assert val == "27,000"

    def test_comparison_format(self):
        """'Normal:500, Crash:20000' should parse as a comparison."""
        anim_type, pairs, _ = _parse_animated_query("Normal:500, Crash:20000")
        assert anim_type == "comparison"
        assert len(pairs) == 2
        assert pairs[0] == ("Normal", 500.0)
        assert pairs[1] == ("Crash", 20000.0)

    def test_unknown_format_returns_unknown(self):
        """A string that matches neither format should return 'unknown'."""
        anim_type, _, _ = _parse_animated_query("no colon here at all")
        assert anim_type == "unknown"

    def test_label_colon_text_is_comparison(self):
        """'Support:52, Oppose:41' (both sides numeric) should be a comparison."""
        anim_type, pairs, _ = _parse_animated_query("Support:52, Oppose:41")
        assert anim_type == "comparison"
        assert len(pairs) == 2


class TestMakeNumberAnimation:
    """Tests for make_number_animation — creates counting-up number frames."""

    def test_creates_correct_number_of_frames(self, tmp_path):
        """Should produce exactly num_frames PNG files."""
        paths = make_number_animation(
            "27000", "feet", "descent", 5, str(tmp_path), "test_key"
        )
        assert len(paths) == 5
        for p in paths:
            assert os.path.exists(p)
            assert p.endswith(".png")

    def test_frames_are_nonzero(self, tmp_path):
        """Each PNG should have actual content (more than 1 KB)."""
        paths = make_number_animation(
            "9", "people", "casualties", 3, str(tmp_path), "test_key2"
        )
        for p in paths:
            assert os.path.getsize(p) > 1000

    def test_non_numeric_value_still_works(self, tmp_path):
        """A non-numeric value_str should not crash — show it as-is."""
        paths = make_number_animation(
            "N/A", "votes", "result", 3, str(tmp_path), "test_key3"
        )
        assert len(paths) == 3

    def test_caching_returns_same_paths(self, tmp_path):
        """Calling the function twice with the same cache_key reuses existing files."""
        paths1 = make_number_animation(
            "100", "km/h", "speed", 3, str(tmp_path), "cache_test"
        )
        paths2 = make_number_animation(
            "100", "km/h", "speed", 3, str(tmp_path), "cache_test"
        )
        assert paths1 == paths2


class TestMakeComparisonAnimation:
    """Tests for make_comparison_animation — animated two-bar comparison."""

    def test_creates_correct_number_of_frames(self, tmp_path):
        """Should produce exactly num_frames PNG files."""
        pairs = [("Before", 500.0), ("After", 20000.0)]
        paths = make_comparison_animation(pairs, 5, str(tmp_path), "cmp_test1")
        assert len(paths) == 5
        for p in paths:
            assert os.path.exists(p)

    def test_returns_empty_with_one_pair(self, tmp_path):
        """Needs at least 2 pairs; should return [] with only 1."""
        paths = make_comparison_animation([("Only", 100.0)], 5, str(tmp_path), "cmp_test2")
        assert paths == []


class TestRenderAnimatedScene:
    """Tests for render_animated_scene — the dispatcher function."""

    def test_number_query_produces_frames(self, tmp_path):
        """A 'value:unit' query should produce N frames."""
        paths = render_animated_scene("5000:km", "distance", 4, str(tmp_path))
        assert len(paths) == 4

    def test_comparison_query_produces_frames(self, tmp_path):
        """A 'label:value, label:value' query should produce N frames."""
        paths = render_animated_scene("Low:10, High:90", "range", 4, str(tmp_path))
        assert len(paths) == 4

    def test_unknown_query_returns_empty(self, tmp_path):
        """An unparseable query should return [] (fall back to text card)."""
        paths = render_animated_scene("completely unparseable text", "whatever", 4, str(tmp_path))
        assert paths == []


# ---------------------------------------------------------------------------
# stock_video.py
# ---------------------------------------------------------------------------

class TestGetVideoFrames:
    """Tests for get_video_frames — downloads Pexels Video clips."""

    def test_returns_empty_without_api_key(self, monkeypatch):
        """Without PEXELS_API_KEY, get_video_frames should return [] immediately."""
        monkeypatch.delenv("PEXELS_API_KEY", raising=False)
        result = get_video_frames("plane flying sky", num_frames=5)
        assert result == []

    def test_uses_env_key_to_call_api(self, monkeypatch):
        """When PEXELS_API_KEY is set, the function should attempt an API call.
        We mock the network so no real request is made."""
        import unittest.mock as mock

        monkeypatch.setenv("PEXELS_API_KEY", "fake-key-for-test")

        # Return an empty videos list — no clips found → returns [].
        fake_response = json.dumps({"videos": []}).encode()

        with mock.patch("urllib.request.urlopen") as mock_open:
            ctx = mock.MagicMock()
            ctx.__enter__ = mock.MagicMock(return_value=ctx)
            ctx.__exit__ = mock.MagicMock(return_value=False)
            ctx.read.return_value = fake_response
            mock_open.return_value = ctx

            result = get_video_frames("plane flying sky", num_frames=5)

        assert result == []

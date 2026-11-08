# =============================================================
# tests/test_visuals.py — tests for the Smart Visuals modules
#
# Covers charts.py, stock.py, and visual_planner.py.
# All tests run offline and cost nothing:
#   - charts.py uses only matplotlib (local, no API)
#   - stock.py returns None when no PEXELS_API_KEY is set
#   - visual_planner.py dry_run mode makes no API calls
# =============================================================

import json
import os
import tempfile

import pytest

from src.video.charts import _parse_chart_data, make_chart
from src.video.stock import _pick_best_photo, _cache_path_for_query, fetch_stock_photo
from src.video.visual_planner import _split_into_segments, _build_planning_prompt, plan_visuals


# ---------------------------------------------------------------------------
# charts.py
# ---------------------------------------------------------------------------

class TestParseChartData:
    """Tests for _parse_chart_data — parses 'Label:Value, ...' strings."""

    def test_basic_three_items(self):
        """Standard three-item string should parse correctly."""
        labels, values = _parse_chart_data("Support:52, Oppose:41, Unsure:7")
        assert labels == ["Support", "Oppose", "Unsure"]
        assert values == [52.0, 41.0, 7.0]

    def test_strips_percent_sign(self):
        """Values like '52%' should still parse as 52.0."""
        labels, values = _parse_chart_data("Yes:62%, No:38%")
        assert labels == ["Yes", "No"]
        assert values == [62.0, 38.0]

    def test_two_items(self):
        """Two-item string should work."""
        labels, values = _parse_chart_data("Before:30, After:70")
        assert len(labels) == 2
        assert len(values) == 2

    def test_empty_string_returns_empty(self):
        """An empty or malformed string should return empty lists."""
        labels, values = _parse_chart_data("")
        assert labels == []
        assert values == []

    def test_no_colon_returns_empty(self):
        """A string with no colons has no valid entries."""
        labels, values = _parse_chart_data("no colon here")
        assert labels == []
        assert values == []

    def test_mixed_valid_and_invalid(self):
        """Only the valid 'Label:Value' pairs should be returned."""
        labels, values = _parse_chart_data("Good:10, bad entry, Also good:20")
        assert labels == ["Good", "Also good"]
        assert values == [10.0, 20.0]


class TestMakeChart:
    """Tests for make_chart — generates a PNG chart file."""

    def test_creates_file(self, tmp_path):
        """make_chart should create a PNG file at the given path."""
        output_path = str(tmp_path / "test_chart.png")
        result = make_chart("Yes:60, No:40", output_path)
        assert result is not None
        assert os.path.exists(result)

    def test_file_is_nonzero(self, tmp_path):
        """The created PNG should have content (not an empty file)."""
        output_path = str(tmp_path / "chart.png")
        make_chart("A:10, B:20, C:30", output_path)
        assert os.path.getsize(output_path) > 1000

    def test_returns_none_on_bad_data(self, tmp_path):
        """make_chart should return None if the query has no parseable data."""
        output_path = str(tmp_path / "bad.png")
        result = make_chart("no data here at all", output_path)
        assert result is None

    def test_single_item_still_works(self, tmp_path):
        """A one-item chart is unusual but should not crash."""
        output_path = str(tmp_path / "single.png")
        result = make_chart("Only:100", output_path)
        assert result is not None


# ---------------------------------------------------------------------------
# stock.py
# ---------------------------------------------------------------------------

class TestPickBestPhoto:
    """Tests for _pick_best_photo — selects the most portrait-shaped image."""

    def _make_photo(self, width, height):
        """Helper: create a minimal Pexels photo dict."""
        return {
            "id": 1,
            "width": width,
            "height": height,
            "photographer": "Test",
            "url": "https://pexels.com/photo/1",
            "src": {"large": "https://example.com/photo.jpg"},
        }

    def test_prefers_portrait_over_landscape(self):
        """A tall photo (h > w) should beat a wide one."""
        portrait = self._make_photo(600, 1200)   # 2:1 portrait
        landscape = self._make_photo(1200, 600)  # 2:1 landscape
        result = _pick_best_photo([portrait, landscape])
        assert result is portrait

    def test_returns_none_on_empty_list(self):
        """Empty input should return None."""
        assert _pick_best_photo([]) is None

    def test_single_photo_returned(self):
        """A single-item list should return that item."""
        photo = self._make_photo(600, 900)
        assert _pick_best_photo([photo]) is photo

    def test_picks_tallest_of_three(self):
        """The photo with the highest height/width ratio should win."""
        square = self._make_photo(100, 100)        # ratio 1.0
        portrait = self._make_photo(100, 150)      # ratio 1.5
        tall = self._make_photo(100, 200)           # ratio 2.0
        result = _pick_best_photo([square, portrait, tall])
        assert result is tall


class TestCachePathForQuery:
    """Tests for _cache_path_for_query — builds consistent cache paths."""

    def test_same_query_gives_same_path(self):
        """The same query string should always map to the same file path."""
        path1, _ = _cache_path_for_query("Capitol building exterior")
        path2, _ = _cache_path_for_query("Capitol building exterior")
        assert path1 == path2

    def test_different_queries_give_different_paths(self):
        """Two different queries should map to different file paths."""
        path1, _ = _cache_path_for_query("news studio desk")
        path2, _ = _cache_path_for_query("Capitol building exterior")
        assert path1 != path2

    def test_returns_jpg_and_json_pair(self):
        """The function should return a .jpg path and a _meta.json path."""
        img_path, meta_path = _cache_path_for_query("test query")
        assert img_path.endswith(".jpg")
        assert meta_path.endswith("_meta.json")


class TestFetchStockPhoto:
    """Tests for fetch_stock_photo — downloads images from Pexels."""

    def test_falls_back_to_wikimedia_without_pexels_key(self, monkeypatch):
        """Without a Pexels key, fetch_stock_photo should delegate to
        fetch_wikimedia_photo (the free fallback) rather than returning None."""
        import unittest.mock as mock
        monkeypatch.delenv("PEXELS_API_KEY", raising=False)

        # Mock the Wikimedia fallback so the test never hits the network.
        with mock.patch(
            "src.video.stock.fetch_wikimedia_photo", return_value="/fake/path.jpg"
        ) as mock_wm:
            result = fetch_stock_photo("Capitol building exterior")

        mock_wm.assert_called_once_with("Capitol building exterior")
        assert result == "/fake/path.jpg"

    def test_uses_env_key(self, monkeypatch, tmp_path):
        """When PEXELS_API_KEY is set, the function should attempt to fetch
        (we mock urllib so no real network call is made)."""
        import unittest.mock as mock
        import urllib.request

        monkeypatch.setenv("PEXELS_API_KEY", "fake-key-for-test")

        # Return an empty photos list so the function gracefully returns None.
        fake_response_data = json.dumps({"photos": []}).encode()

        with mock.patch("urllib.request.urlopen") as mock_urlopen:
            mock_ctx = mock.MagicMock()
            mock_ctx.__enter__ = mock.MagicMock(return_value=mock_ctx)
            mock_ctx.__exit__ = mock.MagicMock(return_value=False)
            mock_ctx.read.return_value = fake_response_data
            mock_urlopen.return_value = mock_ctx

            result = fetch_stock_photo("some query")

        # Empty photos list → no photo found → None returned.
        assert result is None


# ---------------------------------------------------------------------------
# visual_planner.py
# ---------------------------------------------------------------------------

class TestSplitIntoSegments:
    """Tests for _split_into_segments — splits spoken text into chunks."""

    def test_exact_multiple(self):
        """30 words with size 15 should give exactly 2 segments."""
        text = " ".join([f"word{i}" for i in range(30)])
        segments = _split_into_segments(text, words_per_segment=15)
        assert len(segments) == 2

    def test_remainder_forms_smaller_segment(self):
        """16 words with size 15 → one full + one single-word segment."""
        text = " ".join([f"w{i}" for i in range(16)])
        segments = _split_into_segments(text, words_per_segment=15)
        assert len(segments) == 2
        # Second segment should be the leftover single word.
        assert segments[1] == "w15"

    def test_empty_text_gives_empty_list(self):
        """Empty input should give an empty list."""
        segments = _split_into_segments("", words_per_segment=15)
        assert segments == []

    def test_fewer_words_than_segment_size(self):
        """Fewer words than segment size → one segment containing all words."""
        text = "only five words here now"
        segments = _split_into_segments(text, words_per_segment=15)
        assert len(segments) == 1
        assert segments[0] == text


class TestBuildPlanningPrompt:
    """Tests for _build_planning_prompt — builds the AI prompt."""

    def _make_config(self, niche="US politics"):
        return {"niche": niche}

    def test_prompt_contains_all_segments(self):
        """Every segment should appear in the returned prompt."""
        segments = ["First segment text.", "Second segment text."]
        prompt = _build_planning_prompt(segments, self._make_config())
        assert "First segment text." in prompt
        assert "Second segment text." in prompt

    def test_prompt_contains_visual_types(self):
        """The prompt should list all three visual types."""
        prompt = _build_planning_prompt(["some text"], self._make_config())
        assert "stock_photo" in prompt
        assert "chart" in prompt
        assert "text_card" in prompt

    def test_prompt_contains_channel_niche(self):
        """The channel niche should appear in the prompt for context."""
        prompt = _build_planning_prompt(["text"], self._make_config("cat videos"))
        assert "cat videos" in prompt


class TestPlanVisuals:
    """Tests for plan_visuals — the main entry point for visual planning."""

    def _write_script(self, tmp_path, content):
        """Write a minimal script file and return its path."""
        path = tmp_path / "2026-11-07-test-story.md"
        path.write_text(content, encoding="utf-8")
        return str(path)

    def _make_config(self):
        return {
            "niche": "US politics",
            "models": {"ranking": "claude-haiku-4-5"},
        }

    def test_dry_run_returns_text_cards(self, tmp_path):
        """In dry-run mode, every segment should use 'text_card'."""
        script = (
            "## Hook\nBig news today in Washington.\n"
            "## Story\nThe president signed a bill.\n"
            "## Why it matters\nThis affects millions.\n"
            "## What happens next\nCongress will vote.\n"
            "## Closing line\nStay informed."
        )
        script_path = self._write_script(tmp_path, script)
        # Pass a mock llm object (not called in dry_run mode).
        plan = plan_visuals(script_path, self._make_config(), llm=None, dry_run=True)
        assert len(plan) > 0
        for item in plan:
            assert item["visual_type"] == "text_card"

    def test_dry_run_saves_json_file(self, tmp_path):
        """plan_visuals should save a _visuals.json file next to the script."""
        script = (
            "## Hook\nBig news today.\n"
            "## Story\nSomething important happened.\n"
            "## Why it matters\nIt affects people.\n"
            "## What happens next\nMore details to come.\n"
            "## Closing line\nThat is the story."
        )
        script_path = self._write_script(tmp_path, script)
        plan_visuals(script_path, self._make_config(), llm=None, dry_run=True)
        expected_json = script_path.replace(".md", "_visuals.json")
        assert os.path.exists(expected_json)

    def test_dry_run_loads_from_cache(self, tmp_path):
        """Calling plan_visuals twice should load from the saved JSON on the
        second call (without hitting the AI again)."""
        script = (
            "## Hook\nTest hook.\n"
            "## Story\nTest story.\n"
            "## Why it matters\nTest matters.\n"
            "## What happens next\nTest next.\n"
            "## Closing line\nTest close."
        )
        script_path = self._write_script(tmp_path, script)
        plan1 = plan_visuals(script_path, self._make_config(), llm=None, dry_run=True)
        plan2 = plan_visuals(script_path, self._make_config(), llm=None, dry_run=True)
        # Both calls should return the same plan.
        assert plan1 == plan2

    def test_empty_script_returns_empty_plan(self, tmp_path):
        """A script with no spoken sections should return an empty plan."""
        script = "## Notes for reviewer\nNot spoken."
        script_path = self._write_script(tmp_path, script)
        plan = plan_visuals(script_path, self._make_config(), llm=None, dry_run=True)
        assert plan == []

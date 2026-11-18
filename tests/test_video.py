# =============================================================
# tests/test_video.py — tests for the video production helpers
#
# These tests cover the pure Python logic in captions.py and
# the script-text extraction in tts.py. They don't call edge-tts
# or FFmpeg, so they run entirely offline and cost nothing.
# =============================================================

import os

import pytest

from src.video.captions import _group_words, _seconds_to_ass_time, build_subtitle_file
from src.video.tts import _extract_spoken_text


# ---------------------------------------------------------------------------
# captions.py
# ---------------------------------------------------------------------------

class TestGroupWords:
    """Tests for _group_words — splits a flat word list into timed chunks."""

    def test_basic_grouping(self):
        """Three words with chunk size 3 should give one group."""
        words = [
            {"word": "Big",   "start": 0.0, "end": 0.3},
            {"word": "news",  "start": 0.3, "end": 0.6},
            {"word": "today", "start": 0.6, "end": 0.9},
        ]
        groups = _group_words(words, 3)
        assert len(groups) == 1
        assert groups[0]["text"] == "Big news today"
        assert groups[0]["start"] == 0.0
        assert groups[0]["end"] == 0.9

    def test_remainder_forms_smaller_group(self):
        """Four words with chunk size 3 → one group of 3 + one group of 1."""
        words = [
            {"word": "A", "start": 0.0, "end": 0.2},
            {"word": "B", "start": 0.2, "end": 0.4},
            {"word": "C", "start": 0.4, "end": 0.6},
            {"word": "D", "start": 0.6, "end": 0.8},
        ]
        groups = _group_words(words, 3)
        assert len(groups) == 2
        assert groups[0]["text"] == "A B C"
        assert groups[1]["text"] == "D"

    def test_empty_input_gives_empty_output(self):
        assert _group_words([], 3) == []

    def test_chunk_size_one(self):
        """Chunk size 1 should give one group per word."""
        words = [
            {"word": "Hello", "start": 0.0, "end": 0.5},
            {"word": "world", "start": 0.5, "end": 1.0},
        ]
        groups = _group_words(words, 1)
        assert len(groups) == 2
        assert groups[0]["text"] == "Hello"
        assert groups[1]["text"] == "world"


class TestSecondsToAssTime:
    """Tests for _seconds_to_ass_time — converts seconds to H:MM:SS.cs format."""

    def test_zero_seconds(self):
        assert _seconds_to_ass_time(0) == "0:00:00.00"

    def test_one_minute(self):
        assert _seconds_to_ass_time(60) == "0:01:00.00"

    def test_fractional_seconds(self):
        result = _seconds_to_ass_time(73.456)
        assert result == "0:01:13.45"

    def test_over_one_hour(self):
        result = _seconds_to_ass_time(3661)
        assert result == "1:01:01.00"


class TestBuildSubtitleFile:
    """Tests for build_subtitle_file — writes an .ass file from word timings."""

    def test_creates_file(self, tmp_path):
        """The function should create the output file."""
        word_timings = [
            {"word": "Hello", "start": 0.0, "end": 0.5},
            {"word": "world", "start": 0.5, "end": 1.0},
        ]
        output_path = str(tmp_path / "test.ass")
        result = build_subtitle_file(word_timings, output_path)
        assert os.path.exists(result)

    def test_file_contains_dialogue_lines(self, tmp_path):
        """The output file should have at least one Dialogue line."""
        word_timings = [
            {"word": "Hello", "start": 0.0, "end": 0.5},
            {"word": "world", "start": 0.5, "end": 1.0},
        ]
        output_path = str(tmp_path / "test.ass")
        build_subtitle_file(word_timings, output_path)
        with open(output_path) as f:
            content = f.read()
        assert "Dialogue:" in content

    def test_raises_on_empty_timings(self, tmp_path):
        """Passing an empty timing list should raise a ValueError."""
        with pytest.raises(ValueError):
            build_subtitle_file([], str(tmp_path / "test.ass"))


# ---------------------------------------------------------------------------
# tts.py — script text extraction (no network call needed)
# ---------------------------------------------------------------------------

class TestExtractSpokenText:
    """Tests for _extract_spoken_text — pulls spoken sections from a script."""

    def _write_script(self, tmp_path, content):
        """Helper: write content to a temp .md file and return the path."""
        path = tmp_path / "script.md"
        path.write_text(content, encoding="utf-8")
        return str(path)

    def test_extracts_hook(self, tmp_path):
        """Text under ## Hook should appear in the output."""
        script = "## Hook\nThis is the hook.\n## Notes for reviewer\nIgnore this."
        path = self._write_script(tmp_path, script)
        result = _extract_spoken_text(path)
        assert "This is the hook." in result
        assert "Ignore this." not in result

    def test_skips_sources_section(self, tmp_path):
        """## Sources should not be spoken."""
        script = "## Story\nThe story text.\n## Sources\nhttps://example.com"
        path = self._write_script(tmp_path, script)
        result = _extract_spoken_text(path)
        assert "The story text." in result
        assert "https://example.com" not in result

    def test_all_spoken_sections_included(self, tmp_path):
        """All five spoken sections should appear in the output."""
        script = (
            "## Hook\nHook text.\n"
            "## Story\nStory text.\n"
            "## Why it matters\nMatters text.\n"
            "## What happens next\nNext text.\n"
            "## Closing line\nClosing text.\n"
            "## Notes for reviewer\nNotes text."
        )
        path = self._write_script(tmp_path, script)
        result = _extract_spoken_text(path)
        for expected in ["Hook text.", "Story text.", "Matters text.",
                         "Next text.", "Closing text."]:
            assert expected in result
        assert "Notes text." not in result

    def test_empty_script_returns_empty_string(self, tmp_path):
        """A script with no spoken sections should return an empty string."""
        script = "## Notes for reviewer\nOnly notes here."
        path = self._write_script(tmp_path, script)
        result = _extract_spoken_text(path)
        assert result == ""

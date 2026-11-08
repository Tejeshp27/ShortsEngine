# =============================================================
# tests/test_generation.py — tests for ranking, prompts and saving scripts
# =============================================================

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.generation.prompts import build_writing_system_prompt, target_word_count
from src.generation.ranking import parse_ranking_answer, parse_ranking_reason
from src.generation.writer import (
    build_script_file,
    choose_output_path,
    count_spoken_words,
    find_missing_sections,
    slugify,
)

CONFIG = {
    "name": "Test Channel",
    "niche": "testing",
    "audience": "testers",
    "tone": "neutral",
    "target_length_seconds": 60,
}

STORY = {"title": "Big Test Story", "url": "https://example.com/story", "source": "Example News"}

SCRIPT = """## Hook
One two three.
## Story
Four five.
## Why it matters
Six.
## What happens next
Seven.
## Closing line
Eight.
## Notes for reviewer
None of these words are spoken.
"""


def test_parse_ranking_answer_normal():
    """Numbers on the first line are read in order."""
    assert parse_ranking_answer("3, 1, 2\nBecause it matters.", 5) == [3, 1, 2]


def test_parse_ranking_answer_ignores_bad_numbers():
    """Out-of-range numbers and repeats are dropped."""
    assert parse_ranking_answer("9, 2, 2, 0", 5) == [2]


def test_parse_ranking_answer_unreadable():
    """An answer with no numbers gives an empty list (caller falls back)."""
    assert parse_ranking_answer("[DRY RUN placeholder]", 5) == []


def test_parse_ranking_reason():
    """The reason is the second line."""
    assert parse_ranking_reason("1\nIt is important.") == "It is important."
    assert parse_ranking_reason("1") == ""


def test_target_word_count():
    """60 seconds at 2.5 words per second is 150 words."""
    assert target_word_count(60) == 150


def test_writing_prompt_uses_channel_config():
    """The prompt must come from the config, not hard-coded text."""
    prompt = build_writing_system_prompt(CONFIG)
    assert "Test Channel" in prompt
    assert "testers" in prompt
    assert "150 words" in prompt


def test_slugify():
    """Headlines become lowercase, dash-separated, file-safe names."""
    assert slugify("Senate Passes $1B Bill!") == "senate-passes-1b-bill"
    assert slugify("!!!") == "story"


def test_count_spoken_words_skips_notes():
    """Only the five spoken sections count toward length."""
    assert count_spoken_words(SCRIPT) == 8


def test_find_missing_sections():
    """A script without a Hook is flagged."""
    assert find_missing_sections(SCRIPT) == []
    assert "Hook" in find_missing_sections("## Story\nText")


def test_script_file_lists_sources():
    """Every saved script ends with the real source title and URL."""
    text = build_script_file(STORY, SCRIPT, CONFIG, "test-model", "2026-01-01")
    assert "## Sources" in text
    assert "[Big Test Story](https://example.com/story)" in text
    assert "needs human review" in text


def test_choose_output_path_does_not_overwrite(tmp_path, monkeypatch):
    """If a script already exists for today, a numbered name is used."""
    monkeypatch.chdir(tmp_path)
    first = choose_output_path("news", "2026-01-01", "Hello World")
    assert first == os.path.join("output", "news", "2026-01-01-hello-world.md")
    os.makedirs(os.path.dirname(first))
    open(first, "w").close()
    second = choose_output_path("news", "2026-01-01", "Hello World")
    assert second.endswith("2026-01-01-hello-world-2.md")

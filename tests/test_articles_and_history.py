# =============================================================
# tests/test_articles_and_history.py — article text and "already used" tracking
# =============================================================

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.sources.articles import extract_paragraphs, get_article_text, strip_html, trim_text
from src.storage.history import load_processed, mark_processed, remove_processed


def test_strip_html():
    """Tags are removed and spacing is cleaned up."""
    assert strip_html("<p>Hello <b>big</b>   world</p>") == "Hello big world"


def test_extract_paragraphs_keeps_story_text():
    """Long paragraphs are kept; menus and short captions are dropped."""
    page = (
        "<nav>Home News Sports</nav>"
        "<p>Ad</p>"
        "<p>The city council voted on Tuesday to approve the new budget plan.</p>"
        "<script>var x = 1;</script>"
    )
    assert extract_paragraphs(page) == (
        "The city council voted on Tuesday to approve the new budget plan."
    )


def test_trim_text_short_text_unchanged():
    """Text under the limit comes back unchanged."""
    assert trim_text("short", 100) == "short"


def test_trim_text_cuts_at_paragraph():
    """Long text is cut at a paragraph break and marked with [...]."""
    text = ("a" * 60) + "\n\n" + ("b" * 60)
    result = trim_text(text, 100)
    assert result == ("a" * 60) + "\n\n[...]"


def test_get_article_text_without_download():
    """With fetch_full_text off, the RSS summary is used."""
    story = {"url": "https://example.com/x", "summary": "The summary."}
    assert get_article_text(story, 1000, fetch_full_text=False) == "The summary."


def test_history_round_trip(tmp_path, monkeypatch):
    """A story marked as processed is skipped next time."""
    monkeypatch.chdir(tmp_path)
    story_a = {"title": "A", "url": "https://example.com/a"}
    story_b = {"title": "B", "url": "https://example.com/b"}

    assert load_processed("news") == {}
    mark_processed("news", story_a, "output/news/a.md")

    remaining = remove_processed([story_a, story_b], load_processed("news"))
    assert remaining == [story_b]

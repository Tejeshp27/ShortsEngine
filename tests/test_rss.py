# =============================================================
# tests/test_rss.py — tests for the RSS fetcher
# =============================================================

import time
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.sources.rss import remove_duplicates, filter_recent


def make_story(title, url, published_ts):
    """Helper: create a minimal story dict for testing."""
    return {"title": title, "url": url, "source": "Test", "published_ts": published_ts}


def test_remove_duplicates_keeps_first():
    """Duplicate URLs should be removed, keeping the first occurrence."""
    stories = [
        make_story("Story A", "https://example.com/a", 0.0),
        make_story("Story A duplicate", "https://example.com/a", 0.0),
        make_story("Story B", "https://example.com/b", 0.0),
    ]
    result = remove_duplicates(stories)
    assert len(result) == 2
    assert result[0]["title"] == "Story A"
    assert result[1]["title"] == "Story B"


def test_remove_duplicates_no_duplicates():
    """A list with no duplicates should come back unchanged."""
    stories = [
        make_story("Story A", "https://example.com/a", 0.0),
        make_story("Story B", "https://example.com/b", 0.0),
    ]
    result = remove_duplicates(stories)
    assert len(result) == 2


def test_filter_recent_removes_old():
    """Stories older than 24 hours should be filtered out."""
    now = time.time()
    two_days_ago = now - (48 * 3600)
    one_hour_ago = now - 3600

    stories = [
        make_story("Old story", "https://example.com/old", two_days_ago),
        make_story("Fresh story", "https://example.com/fresh", one_hour_ago),
    ]
    result = filter_recent(stories, hours=24)
    assert len(result) == 1
    assert result[0]["title"] == "Fresh story"


def test_filter_recent_keeps_no_date():
    """Stories with no publish date (ts=0.0) should NOT be filtered out."""
    stories = [
        make_story("No-date story", "https://example.com/nodate", 0.0),
    ]
    result = filter_recent(stories, hours=24)
    assert len(result) == 1


def test_filter_recent_sorts_newest_first():
    """Results should be sorted newest first."""
    now = time.time()
    stories = [
        make_story("One hour ago", "https://example.com/1h", now - 3600),
        make_story("Ten hours ago", "https://example.com/10h", now - 36000),
        make_story("Two hours ago", "https://example.com/2h", now - 7200),
    ]
    result = filter_recent(stories, hours=24)
    assert result[0]["title"] == "One hour ago"
    assert result[1]["title"] == "Two hours ago"
    assert result[2]["title"] == "Ten hours ago"

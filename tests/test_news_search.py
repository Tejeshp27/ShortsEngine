# =============================================================
# tests/test_news_search.py — tests for the web news topic search
#
# All tests run offline: the request function is replaced with a
# fake, so no real search service is contacted.
# =============================================================

from unittest.mock import Mock

import pytest

from src.sources import news_search

CONFIG = {"search": {"max_results": 3, "cache_minutes": 15}}

GDELT_ANSWER = {"articles": [
    {"title": "Nvidia shares rise", "url": "https://example.com/a", "domain": "example.com",
     "seendate": "20261111T060000Z"},
    {"title": "Nvidia chip export rules", "url": "https://example.org/b", "domain": "example.org",
     "seendate": "20261111T080000Z"},
    {"title": "", "url": "https://example.org/no-title"},
]}

NEWSAPI_ANSWER = {"status": "ok", "articles": [
    {"title": "Same story as GDELT", "url": "https://example.com/a", "source": {"name": "Example"},
     "description": "Duplicate", "publishedAt": "2026-11-11T06:00:00Z"},
    {"title": "Nvidia earnings preview", "url": "https://news.example/c", "source": {"name": "News"},
     "description": "Analysts expect...", "publishedAt": "2026-11-11T09:00:00Z"},
    {"title": "[Removed]", "url": "https://removed.example/d"},
]}


@pytest.fixture
def offline(tmp_path, monkeypatch):
    """Run in a temporary folder with no waiting and no NewsAPI key."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(news_search, "wait_for_gdelt_turn", Mock())
    monkeypatch.delenv("NEWS_API_KEY", raising=False)
    return tmp_path


def fake_services(gdelt=GDELT_ANSWER, newsapi=NEWSAPI_ANSWER):
    """Return a fake fetch_json that answers like GDELT or NewsAPI."""
    def answer(url, params, headers=None):
        """Pick the fake answer by which service address was asked."""
        if url == news_search.GDELT_ENDPOINT:
            return gdelt
        return newsapi
    return Mock(side_effect=answer)


def test_channel_without_search_section_never_searches(offline, monkeypatch):
    """A channel with no "search:" section makes no web requests."""
    fetch = fake_services()
    monkeypatch.setattr(news_search, "fetch_json", fetch)
    assert news_search.search_news("NVIDIA", {"name": "Twitch"}) == ([], [])
    fetch.assert_not_called()


def test_clean_topic_removes_query_symbols():
    """Quotes and brackets can't change the search query's meaning."""
    assert news_search.clean_topic('  "NVIDIA"  (chips) ') == "NVIDIA chips"


def test_short_topic_asks_for_more_letters(offline):
    """GDELT refuses very short words, so the user is told instead."""
    stories, notes = news_search.search_news("AI", CONFIG)
    assert stories == []
    assert "at least 3 letters" in notes[0]


def test_gdelt_results_become_stories(offline, monkeypatch):
    """GDELT articles get the same fields as RSS stories, newest first."""
    monkeypatch.setattr(news_search, "fetch_json", fake_services())
    stories, notes = news_search.search_news("NVIDIA", CONFIG)
    assert notes == []
    assert [story["url"] for story in stories] == ["https://example.org/b", "https://example.com/a"]
    assert stories[0]["source"] == "example.org"
    assert stories[0]["published_ts"] > stories[1]["published_ts"]


def test_newsapi_used_only_with_key_and_duplicates_removed(offline, monkeypatch):
    """With a key, NewsAPI adds stories; the same link is only listed once."""
    fetch = fake_services()
    monkeypatch.setattr(news_search, "fetch_json", fetch)
    news_search.search_news("NVIDIA", CONFIG)
    assert fetch.call_count == 1   # GDELT only: no key set

    monkeypatch.setenv("NEWS_API_KEY", "real-test-key")
    stories, _ = news_search.search_news("NVIDIA earnings", CONFIG)
    urls = [story["url"] for story in stories]
    assert len(urls) == len(set(urls)) == 3
    assert urls[0] == "https://news.example/c"
    # The key travels in a header, never in the address.
    newsapi_call = fetch.call_args_list[-1]
    assert newsapi_call.kwargs["headers"] == {"X-Api-Key": "real-test-key"}
    assert "real-test-key" not in str(newsapi_call.args[1])


def test_busy_gdelt_gives_a_note_and_is_not_saved(offline, monkeypatch):
    """A rate-limited GDELT is explained, and the next search tries again."""
    fetch = fake_services(gdelt=None)
    monkeypatch.setattr(news_search, "fetch_json", fetch)
    stories, notes = news_search.search_news("NVIDIA", CONFIG)
    assert stories == []
    assert "busy" in notes[0]
    news_search.search_news("NVIDIA", CONFIG)
    assert fetch.call_count == 2


def test_repeated_search_uses_saved_results(offline, monkeypatch):
    """The same search within cache_minutes makes no new request."""
    fetch = fake_services()
    monkeypatch.setattr(news_search, "fetch_json", fetch)
    first, _ = news_search.search_news("NVIDIA", CONFIG)
    second, _ = news_search.search_news("nvidia", CONFIG)
    assert first == second
    assert fetch.call_count == 1

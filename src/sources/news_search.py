# =============================================================
# src/sources/news_search.py — search recent news for a topic
#
# The channel's RSS feeds only contain what those few outlets
# published today. When you type a topic (e.g. "NVIDIA") on the
# dashboard's "New video" page, this file searches the wider web's
# recent news instead, using two free services:
#
#   GDELT   (gdeltproject.org) — free, no key or sign-up, covers news
#           sites worldwide, links straight to each article. It allows
#           only ONE search every 5 seconds, so we wait between
#           searches and save results (see "cache" below).
#   NewsAPI (newsapi.org) — used only when NEWS_API_KEY is in .env.
#           Free plan: 100 requests a day, development use only (its
#           terms don't allow a production/commercial service).
#
# Google News was tested and left out on purpose: its links lead to
# a Google page rather than the article, so the article text can't
# be downloaded and no accurate script could be written from it.
#
# Every story found here has the same fields as an RSS story
# (title, url, source, summary, published_ts), so the rest of the
# pipeline treats them the same. Nothing here costs money.
#
# How it fits in:
#   dashboard "Find stories" → worker.discover() → search_news() (here)
# =============================================================

import hashlib
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from src.sources.newsapi import _parse_iso_timestamp
from src.sources.rss import remove_duplicates

GDELT_ENDPOINT = "https://api.gdeltproject.org/api/v2/doc/doc"
NEWSAPI_SEARCH_ENDPOINT = "https://newsapi.org/v2/everything"

# Saved search results live here (the cache/ folder is git-ignored).
SEARCH_CACHE_FOLDER = Path("cache") / "search"
# When the last GDELT search happened, shared by every job.
GDELT_LAST_CALL_FILE = SEARCH_CACHE_FOLDER / "gdelt_last_call.txt"
# GDELT asks for at most one request every 5 seconds; 6 leaves a margin.
GDELT_SECONDS_BETWEEN_CALLS = 6

REQUEST_TIMEOUT_SECONDS = 20
MAX_TOPIC_LENGTH = 100
# GDELT refuses search words shorter than 3 letters.
MIN_TOPIC_LENGTH = 3

# Used when a channel's YAML has a "search:" section but leaves a
# setting out. A channel WITHOUT a "search:" section never searches
# the web; its topic box only filters its own feeds.
DEFAULT_SEARCH_SETTINGS = {
    "gdelt": True,
    "newsapi": True,
    "hours_back": 48,
    "max_results": 30,
    "cache_minutes": 15,
}


# ---------------------------------------------------------------------------
# Settings and the topic text
# ---------------------------------------------------------------------------

def search_settings(config):
    """
    Get a channel's web-search settings, with defaults filled in.

    Args:
        config (dict): The channel's YAML settings.

    Returns:
        dict or None: The settings, or None if the channel has no
                      "search:" section (web search is switched off).

    Example:
        search_settings({"search": {"max_results": 10}})["max_results"]   # → 10
    """
    channel_settings = config.get("search")
    if channel_settings is None:
        return None
    settings = dict(DEFAULT_SEARCH_SETTINGS)
    settings.update(channel_settings)
    return settings


def clean_topic(topic):
    """
    Tidy a typed topic before searching.

    Quote marks and brackets are removed (they have special meanings in
    search queries), extra spaces are collapsed, and the length is capped.

    Args:
        topic (str): What was typed, e.g. '  "NVIDIA"  chips '.

    Returns:
        str: For example 'NVIDIA chips'.
    """
    without_symbols = re.sub(r'["()\[\]{}<>]', " ", topic)
    words = without_symbols.split()
    return " ".join(words)[:MAX_TOPIC_LENGTH]


# ---------------------------------------------------------------------------
# Saved results (so repeating a search is instant and free)
# ---------------------------------------------------------------------------

def cache_file_for(topic, settings):
    """
    Work out which file holds the saved results for a search.

    Args:
        topic (str): The cleaned topic.
        settings (dict): The search settings (they change the results too).

    Returns:
        Path: A file in cache/search/ named by a fingerprint of the search.
    """
    identity = json.dumps({"topic": topic.casefold(), "settings": settings}, sort_keys=True)
    fingerprint = hashlib.sha256(identity.encode()).hexdigest()[:24]
    return SEARCH_CACHE_FOLDER / f"{fingerprint}.json"


def load_cached_results(path, cache_minutes):
    """
    Read saved results if they are recent enough.

    Args:
        path (Path): The cache file.
        cache_minutes (int): How old saved results may be.

    Returns:
        list[dict] or None: The saved stories, or None if there are none
                            or they are too old.
    """
    if not path.exists():
        return None
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    age_seconds = time.time() - saved.get("saved_at", 0)
    if age_seconds > cache_minutes * 60:
        return None
    return saved.get("stories", [])


def save_cached_results(path, stories):
    """
    Save search results so the same search soon after is free and instant.

    Args:
        path (Path): The cache file.
        stories (list[dict]): The stories found.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"saved_at": time.time(), "stories": stories}), encoding="utf-8")


# ---------------------------------------------------------------------------
# GDELT
# ---------------------------------------------------------------------------

def wait_for_gdelt_turn():
    """
    Pause if GDELT was searched less than 6 seconds ago.

    The time of the last search is kept in a small file, so separate
    jobs (each its own process) also wait for each other.
    """
    SEARCH_CACHE_FOLDER.mkdir(parents=True, exist_ok=True)
    last_call = 0.0
    if GDELT_LAST_CALL_FILE.exists():
        try:
            last_call = float(GDELT_LAST_CALL_FILE.read_text())
        except (OSError, ValueError):
            last_call = 0.0
    seconds_since = time.time() - last_call
    if seconds_since < GDELT_SECONDS_BETWEEN_CALLS:
        time.sleep(GDELT_SECONDS_BETWEEN_CALLS - seconds_since)
    GDELT_LAST_CALL_FILE.write_text(str(time.time()))


def gdelt_query(topic):
    """
    Build GDELT's search text for a topic.

    Several words are put in quotes so GDELT looks for the exact phrase.
    "sourcelang:english" keeps results to English-language articles.

    Args:
        topic (str): The cleaned topic, e.g. "Supreme Court".

    Returns:
        str: For example '"Supreme Court" sourcelang:english'.
    """
    if " " in topic:
        return f'"{topic}" sourcelang:english'
    return f"{topic} sourcelang:english"


def parse_gdelt_time(seen_date):
    """
    Convert GDELT's date format into a Unix timestamp.

    Args:
        seen_date (str): For example "20261111T063000Z".

    Returns:
        float: Seconds since 1970, or 0.0 if it can't be read.
    """
    try:
        moment = datetime.strptime(seen_date, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return 0.0
    return moment.timestamp()


def fetch_json(url, params, headers=None):
    """
    Send one GET request and return the answer as a dict.

    Args:
        url (str): The service address.
        params (dict): Query values added to the address.
        headers (dict or None): Extra headers, e.g. an API key.

    Returns:
        dict or None: The JSON answer, or None if the service could not
                      be reached or did not answer with JSON (GDELT
                      answers with a plain-text message when it is busy).
    """
    try:
        response = httpx.get(url, params=params, headers=headers or {},
                             timeout=REQUEST_TIMEOUT_SECONDS, follow_redirects=False)
        if response.status_code != 200:
            return None
        return response.json()
    except (httpx.HTTPError, ValueError):
        return None


def search_gdelt(topic, settings):
    """
    Search GDELT for recent English news about a topic.

    Args:
        topic (str): The cleaned topic.
        settings (dict): From search_settings().

    Returns:
        tuple: (list of story dicts, a warning message or "")
    """
    wait_for_gdelt_turn()
    data = fetch_json(GDELT_ENDPOINT, {
        "query": gdelt_query(topic),
        "mode": "artlist",
        "format": "json",
        "maxrecords": settings["max_results"],
        "timespan": f"{settings['hours_back']}h",
        "sort": "datedesc",
    })
    if data is None:
        return [], ("GDELT news search was busy or unreachable, so it was skipped this time "
                    "(it allows one search every few seconds). Try again in a minute for more results.")

    stories = []
    for article in data.get("articles", []):
        title = (article.get("title") or "").strip()
        url = (article.get("url") or "").strip()
        if not title or not url:
            continue
        stories.append({
            "title": title,
            "url": url,
            "source": article.get("domain") or "GDELT",
            "summary": "",   # GDELT gives titles only
            "published_ts": parse_gdelt_time(article.get("seendate")),
        })
    return stories, ""


# ---------------------------------------------------------------------------
# NewsAPI
# ---------------------------------------------------------------------------

def search_newsapi(topic, settings):
    """
    Search NewsAPI for recent English news about a topic.

    Skipped (no request at all) when NEWS_API_KEY is not set.

    Args:
        topic (str): The cleaned topic.
        settings (dict): From search_settings().

    Returns:
        tuple: (list of story dicts, a warning message or "")
    """
    api_key = os.getenv("NEWS_API_KEY", "")
    if not api_key or api_key.startswith("your-"):
        return [], ""

    oldest = datetime.now(timezone.utc) - timedelta(hours=settings["hours_back"])
    # The key goes in a header, not the address, so it never shows up in logs.
    data = fetch_json(NEWSAPI_SEARCH_ENDPOINT, {
        "q": topic,
        # Only match the topic in the headline or summary, so articles that
        # merely mention it once deep in the text are left out.
        "searchIn": "title,description",
        "from": oldest.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "language": "en",
        "sortBy": "publishedAt",
        "pageSize": settings["max_results"],
    }, headers={"X-Api-Key": api_key})
    if data is None or data.get("status") != "ok":
        return [], "NewsAPI search failed (check NEWS_API_KEY or its daily limit)."

    stories = []
    for article in data.get("articles", []):
        title = (article.get("title") or "").strip()
        url = (article.get("url") or "").strip()
        # "[Removed]" marks an article its publisher took down.
        if not title or not url or title == "[Removed]":
            continue
        stories.append({
            "title": title,
            "url": url,
            "source": (article.get("source") or {}).get("name") or "NewsAPI",
            "summary": (article.get("description") or "").strip(),
            "published_ts": _parse_iso_timestamp(article.get("publishedAt", "")),
        })
    return stories, ""


# ---------------------------------------------------------------------------
# The search used by the dashboard
# ---------------------------------------------------------------------------

def newest_first(story):
    """
    Give the value used to sort stories newest first.

    Args:
        story (dict): A story.

    Returns:
        float: Its publish time (0.0 if unknown, so it sorts last).
    """
    return story.get("published_ts", 0.0)


def search_news(topic, config):
    """
    Search recent news across the web for a topic.

    Results are saved for a few minutes (cache_minutes), so searching the
    same topic again is instant and doesn't hit GDELT's rate limit.

    Args:
        topic (str): What was typed, e.g. "NVIDIA".
        config (dict): The channel's settings (uses its "search:" section).

    Returns:
        tuple: (stories, notes)
            stories: list of story dicts, newest first, no duplicates.
            notes: list of short messages to show (e.g. a busy service).

    Example:
        stories, notes = search_news("NVIDIA", config)
    """
    settings = search_settings(config)
    if settings is None:
        return [], []
    topic = clean_topic(topic)
    if len(topic) < MIN_TOPIC_LENGTH:
        return [], ["Type at least 3 letters to search the web's news."]

    cache_file = cache_file_for(topic, settings)
    cached = load_cached_results(cache_file, settings["cache_minutes"])
    if cached is not None:
        return cached, []

    stories = []
    notes = []
    if settings["gdelt"]:
        found, warning = search_gdelt(topic, settings)
        stories.extend(found)
        if warning:
            notes.append(warning)
    if settings["newsapi"]:
        found, warning = search_newsapi(topic, settings)
        stories.extend(found)
        if warning:
            notes.append(warning)

    stories = remove_duplicates(stories)
    stories.sort(key=newest_first, reverse=True)
    stories = stories[:settings["max_results"]]

    # Only save complete answers. If a service was busy, the next search
    # should try it again rather than reuse the incomplete result.
    if not notes:
        save_cached_results(cache_file, stories)
    return stories, notes

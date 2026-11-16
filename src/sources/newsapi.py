# =============================================================
# src/sources/newsapi.py — fetch trending headlines from NewsAPI
#
# NewsAPI (newsapi.org) aggregates headlines from over 80,000
# news sources. It returns the same story fields we use from RSS
# (title, url, summary, source, published_ts) so the rest of the
# pipeline doesn't need to know the difference.
#
# Free tier: 100 requests per day, headlines only.
# Requires NEWS_API_KEY in .env.
#
# How it fits in:
#   main.py → get_candidate_stories() → fetch_all_stories() (rss.py)
#       → fetch_newsapi_stories() (here)   ← called when source type = "newsapi"
# =============================================================

import json
import os
import time
import urllib.parse
import urllib.request


NEWSAPI_ENDPOINT = "https://newsapi.org/v2/top-headlines"


def fetch_newsapi_stories(source_config):
    """
    Fetch top headlines from NewsAPI for one source entry in the channel YAML.

    The source_config dict comes from the channel's 'sources' list and can
    include these optional fields:
      - category (str): news category, e.g. "general", "politics", "technology"
      - country  (str): two-letter country code, e.g. "us" (default)
      - page_size (int): how many headlines to fetch (max 100; default 20)

    Args:
        source_config (dict): One entry from the channel's 'sources' list.
                              Must have type="newsapi". Other keys are optional.

    Returns:
        list[dict]: Story dicts with keys:
            - title (str)
            - url (str)
            - source (str)  — the outlet name, e.g. "CNN"
            - summary (str) — the article description
            - published_ts (float) — Unix timestamp

    Example:
        source = {"type": "newsapi", "category": "general", "country": "us"}
        stories = fetch_newsapi_stories(source)
        # → [{"title": "...", "url": "...", ...}, ...]
    """
    api_key = os.getenv("NEWS_API_KEY", "")
    if not api_key:
        print("  WARNING: NEWS_API_KEY not set — skipping NewsAPI source.")
        return []

    # Build the query string from the source config.
    # 'country' and 'category' are both optional — NewsAPI returns top US headlines
    # by default if neither is specified.
    params = {
        "apiKey": api_key,
        "pageSize": source_config.get("page_size", 30),
    }

    # Only add optional fields if they are present in the config.
    if "country" in source_config:
        params["country"] = source_config["country"]
    if "category" in source_config:
        params["category"] = source_config["category"]

    url = f"{NEWSAPI_ENDPOINT}?{urllib.parse.urlencode(params)}"

    source_label = source_config.get("name", "NewsAPI")
    print(f"  Fetching {source_label}...")

    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "ShortsEngine/0.1"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
    except Exception as err:
        print(f"    WARNING: NewsAPI fetch failed: {err}")
        return []

    # NewsAPI returns {"status": "ok", "articles": [...]}
    if data.get("status") != "ok":
        message = data.get("message", "unknown error")
        print(f"    WARNING: NewsAPI returned an error: {message}")
        return []

    articles = data.get("articles", [])
    stories = []

    for article in articles:
        title = (article.get("title") or "").strip()
        url = (article.get("url") or "").strip()

        # Skip articles with no title or URL — they can't be used.
        if not title or not url:
            continue

        # Skip NewsAPI placeholder articles (they sometimes appear
        # as "[Removed]" when a source pulls an article).
        if title == "[Removed]":
            continue

        # NewsAPI gives the publish time as an ISO 8601 string like
        # "2024-01-15T14:30:00Z". We convert it to a Unix timestamp
        # so it's compatible with the rest of the pipeline.
        published_ts = _parse_iso_timestamp(article.get("publishedAt", ""))

        # The source name comes nested inside {"source": {"name": "CNN"}}.
        outlet = article.get("source", {})
        source_name = outlet.get("name") or source_label

        summary = (article.get("description") or "").strip()

        stories.append({
            "title": title,
            "url": url,
            "source": source_name,
            "summary": summary,
            "published_ts": published_ts,
        })

    print(f"    Got {len(stories)} articles from {source_label}.")
    return stories


def _parse_iso_timestamp(iso_string):
    """
    Convert an ISO 8601 date string into a Unix timestamp (seconds since 1970).

    NewsAPI returns dates like "2024-01-15T14:30:00Z". Python's time module
    can parse this format directly with strptime.

    Args:
        iso_string (str): An ISO 8601 date string, e.g. "2024-01-15T14:30:00Z".

    Returns:
        float: Unix timestamp, or 0.0 if the string can't be parsed.

    Example:
        _parse_iso_timestamp("2024-01-15T14:30:00Z")
        # → 1705328400.0
    """
    if not iso_string:
        return 0.0

    try:
        # strptime parses a formatted string into a struct_time.
        # "%Y-%m-%dT%H:%M:%SZ" matches "2024-01-15T14:30:00Z".
        struct = time.strptime(iso_string, "%Y-%m-%dT%H:%M:%SZ")
        # calendar.timegm treats the struct as UTC (unlike time.mktime which
        # assumes local time). NewsAPI always returns UTC times.
        import calendar
        return float(calendar.timegm(struct))
    except ValueError:
        return 0.0

# =============================================================
# src/sources/rss.py — fetch news stories from RSS feeds
#
# This module reads one or more RSS feeds and returns a clean
# list of stories. It handles:
#   - Fetching each feed with feedparser
#   - Filtering to stories published in the last 24 hours
#   - Removing duplicate stories (same URL from multiple feeds)
#
# It makes no paid API calls — just free HTTP requests.
# =============================================================

import time
import feedparser


def fetch_stories_from_feed(url, source_name):
    """
    Fetch all stories from a single RSS feed URL.

    Args:
        url (str): The RSS feed URL to fetch.
        source_name (str): Human-readable name for this source
                           (e.g. "NPR News"), stored on each story.

    Returns:
        list[dict]: A list of story dicts, each with keys:
            - title (str): Headline
            - url (str): Link to the full article
            - source (str): Name of the feed (e.g. "NPR News")
            - published_ts (float): Unix timestamp when published,
                                    or 0.0 if the feed didn't include one
    """
    print(f"  Fetching {source_name}...")

    # feedparser.parse() downloads and parses the RSS XML for us.
    # It never raises an exception — if the fetch fails it returns
    # an object with an empty 'entries' list.
    feed = feedparser.parse(url)

    stories = []

    for entry in feed.entries:
        # RSS entries always have a title and a link.
        # .get() returns an empty string if the key is missing,
        # which is safer than accessing it directly.
        title = entry.get("title", "").strip()
        link = entry.get("link", "").strip()

        # Skip entries that are missing a title or link — they
        # can't be displayed or used in the pipeline.
        if not title or not link:
            continue

        # feedparser stores the publish date as a time.struct_time
        # under 'published_parsed'. Not all feeds include a date,
        # so we default to 0.0 (which will be filtered out later).
        published_struct = entry.get("published_parsed", None)
        if published_struct is not None:
            # time.mktime converts struct_time → Unix timestamp (seconds since 1970)
            published_ts = time.mktime(published_struct)
        else:
            published_ts = 0.0

        stories.append({
            "title": title,
            "url": link,
            "source": source_name,
            "published_ts": published_ts,
        })

    return stories


def fetch_all_stories(sources):
    """
    Fetch stories from every source in the channel config.

    Args:
        sources (list[dict]): The 'sources' list from the channel YAML.
                              Each dict has 'type', 'url', and 'name'.

    Returns:
        list[dict]: Combined stories from all feeds, unsorted.
    """
    all_stories = []

    for source in sources:
        # Only RSS sources are supported in Phase 1.
        # Other types (e.g. "twitch") will be added later.
        if source["type"] != "rss":
            print(f"  Skipping unsupported source type: {source['type']}")
            continue

        stories = fetch_stories_from_feed(source["url"], source["name"])
        all_stories.extend(stories)

    return all_stories


def remove_duplicates(stories):
    """
    Remove stories that share the same URL.

    The same story sometimes appears in multiple RSS feeds
    (e.g. an NPR story picked up by BBC). We keep the first
    occurrence and discard the rest.

    Args:
        stories (list[dict]): List of story dicts from fetch_all_stories().

    Returns:
        list[dict]: Stories with duplicates removed.
    """
    seen_urls = set()
    unique_stories = []

    for story in stories:
        url = story["url"]
        if url not in seen_urls:
            seen_urls.add(url)
            unique_stories.append(story)

    return unique_stories


def filter_recent(stories, hours=24):
    """
    Keep only stories published within the last N hours.

    Args:
        stories (list[dict]): Story dicts from fetch_all_stories().
        hours (int): How far back to look. Defaults to 24 hours.

    Returns:
        list[dict]: Only the recent stories, sorted newest first.
    """
    # time.time() returns the current Unix timestamp (seconds since 1970).
    # We subtract hours*3600 to get the cutoff point.
    cutoff = time.time() - (hours * 3600)

    recent = []
    for story in stories:
        # Stories with published_ts == 0.0 have no date — keep them
        # rather than silently dropping them, since they may still be fresh.
        if story["published_ts"] == 0.0 or story["published_ts"] >= cutoff:
            recent.append(story)

    # Sort newest first so the most recent stories appear at the top.
    # Stories with no date (ts == 0.0) go to the bottom.
    recent.sort(key=lambda s: s["published_ts"], reverse=True)

    return recent

# =============================================================
# src/clips/research.py — collect public facts about successful
#                         streamer Shorts with the official YouTube API
#
# Used for docs/VIRAL_CONTENT_RESEARCH.md. It searches YouTube for
# recent short videos about each streamer, reads their public numbers
# (views, likes, comments, length, title, upload date) and saves them as
# JSON, so every number in the report can be checked again later.
#
# It only reads public metadata through the YouTube Data API with the
# channel's own connected account (scope youtube.readonly). It never
# downloads a video. Cost: about 100 quota units per search (free;
# the default daily quota is 10,000).
#
# Run:  python -m src.clips.research --channel twitch_clips
# =============================================================

import argparse
import json
import re
import re
import statistics
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"
VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"

# Who to study: the owner's streamers plus the large streamers named in the
# overhaul brief. These are search phrases, not channel IDs, so results
# include both official and independent clipping channels.
DEFAULT_QUERIES = ["jasontheween", "kai cenat", "ishowspeed", "jynxzi", "caseoh",
                   "stableronaldo", "lacy twitch", "adin ross", "marlon twitch"]


def parse_duration(text):
    """
    Turn YouTube's duration text (for example "PT1M5S") into seconds.

    Args:
        text (str): An ISO 8601 duration such as "PT45S" or "PT1M5S".

    Returns:
        int: The length in seconds (0 when the text cannot be read).

    Example:
        parse_duration("PT1M5S") → 65
    """
    match = re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", text or "")
    if not match:
        return 0
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2) or 0)
    seconds = int(match.group(3) or 0)
    return hours * 3600 + minutes * 60 + seconds


def title_features(title):
    """
    Describe a title's style in simple measurable ways.

    Args:
        title (str): A video title.

    Returns:
        dict: word count, share of letters that are capitals, whether it is
              all lowercase, emoji count and whether it has hashtags.
    """
    letters = []
    for character in title:
        if character.isalpha():
            letters.append(character)
    capitals = 0
    for character in letters:
        if character.isupper():
            capitals += 1
    emoji = 0
    for character in title:
        if ord(character) >= 0x1F300:
            emoji += 1
    return {"words": len(title.split()),
            "capital_share": round(capitals / max(1, len(letters)), 3),
            "all_lowercase": bool(letters) and capitals == 0,
            "emoji": emoji,
            "has_hashtag": "#" in title}


def search_shorts(token, query, published_after, max_results=25):
    """
    Ask YouTube for the most-viewed short videos matching a search phrase.

    Args:
        token (str): An OAuth access token with youtube.readonly.
        query (str): What to search for, e.g. "kai cenat".
        published_after (str): Only videos uploaded after this RFC 3339 time.
        max_results (int): How many results to ask for (up to 50).

    Returns:
        list[str]: Video IDs.
    """
    from src.dashboard.accounts import api_json

    data = api_json("GET", SEARCH_URL, token=token, params={
        "part": "id", "q": query, "type": "video", "videoDuration": "short",
        "order": "viewCount", "publishedAfter": published_after,
        "maxResults": max_results, "relevanceLanguage": "en"})
    ids = []
    for item in data.get("items", []):
        video_id = item.get("id", {}).get("videoId")
        if video_id:
            ids.append(video_id)
    return ids


def video_details(token, video_ids):
    """
    Read public numbers for up to 50 videos at once.

    Args:
        token (str): An OAuth access token with youtube.readonly.
        video_ids (list[str]): YouTube video IDs.

    Returns:
        list[dict]: One plain record per video (URL, channel, title, date,
                    views, likes, comments, seconds and title features).
    """
    from src.dashboard.accounts import api_json

    data = api_json("GET", VIDEOS_URL, token=token, params={
        "part": "snippet,statistics,contentDetails", "id": ",".join(video_ids)})
    records = []
    for item in data.get("items", []):
        snippet = item.get("snippet", {})
        numbers = item.get("statistics", {})
        views = int(numbers.get("viewCount", 0))
        likes = int(numbers["likeCount"]) if "likeCount" in numbers else None
        comments = int(numbers["commentCount"]) if "commentCount" in numbers else None
        record = {"url": "https://www.youtube.com/shorts/" + item["id"],
                  "channel": snippet.get("channelTitle", ""),
                  "title": snippet.get("title", ""),
                  "published": snippet.get("publishedAt", ""),
                  "views": views, "likes": likes, "comments": comments,
                  "seconds": parse_duration(item.get("contentDetails", {}).get("duration", "")),
                  "tags": snippet.get("tags", [])[:15]}
        if likes is not None and views:
            record["likes_per_1000_views"] = round(1000 * likes / views, 2)
        if comments is not None and views:
            record["comments_per_1000_views"] = round(1000 * comments / views, 3)
        record.update(title_features(record["title"]))
        records.append(record)
    return records


def summarize(records):
    """
    Work out simple statistics across all collected videos.

    Args:
        records (list[dict]): From video_details().

    Returns:
        dict: medians and shares (length, title words, lowercase titles,
              emoji use and engagement rates) plus length buckets.
    """
    if not records:
        return {}
    seconds = []
    words = []
    likes_rate = []
    lowercase = 0
    with_emoji = 0
    for record in records:
        seconds.append(record["seconds"])
        words.append(record["words"])
        if "likes_per_1000_views" in record:
            likes_rate.append(record["likes_per_1000_views"])
        if record["all_lowercase"]:
            lowercase += 1
        if record["emoji"]:
            with_emoji += 1
    buckets = {"under_15": 0, "15_to_30": 0, "30_to_45": 0, "45_to_60": 0, "over_60": 0}
    for value in seconds:
        if value < 15:
            buckets["under_15"] += 1
        elif value < 30:
            buckets["15_to_30"] += 1
        elif value < 45:
            buckets["30_to_45"] += 1
        elif value <= 60:
            buckets["45_to_60"] += 1
        else:
            buckets["over_60"] += 1
    return {"videos": len(records),
            "median_seconds": statistics.median(seconds),
            "median_title_words": statistics.median(words),
            "share_all_lowercase_titles": round(lowercase / len(records), 3),
            "share_titles_with_emoji": round(with_emoji / len(records), 3),
            "median_likes_per_1000_views": statistics.median(likes_rate) if likes_rate else None,
            "length_buckets": buckets}


def collect(channel, queries, days, per_query, output_folder):
    """
    Run the searches, read every video's numbers and save one JSON file.

    Args:
        channel (str): The channel whose connected YouTube account is used.
        queries (list[str]): Search phrases.
        days (int): Only videos uploaded in the last this-many days.
        per_query (int): Results per search phrase.
        output_folder (str): Where to save the JSON file.

    Returns:
        str: The saved file's path.
    """
    from src.dashboard.accounts import access_token, get_account

    account = get_account(channel, "youtube")
    if account is None:
        raise ValueError(f"No YouTube account is connected to {channel}.")
    token = access_token(account)
    after = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    records = []
    seen = set()
    for query in queries:
        ids = []
        for video_id in search_shorts(token, query, after, per_query):
            if video_id not in seen:
                seen.add(video_id)
                ids.append(video_id)
        if ids:
            for record in video_details(token, ids):
                record["query"] = query
                records.append(record)
    records.sort(key=views_of, reverse=True)
    result = {"collected_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "method": "YouTube Data API v3 search.list (type=video, videoDuration=short, "
                        "order=viewCount) + videos.list; public metadata only, no downloads.",
              "published_after": after, "queries": queries,
              "summary": summarize(records), "videos": records}
    folder = Path(output_folder)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"youtube_shorts_{time.strftime('%Y-%m-%d')}.json"
    path.write_text(json.dumps(result, indent=2, ensure_ascii=False))
    return str(path)


def views_of(record):
    """
    Return a video record's view count, for sorting.

    Args:
        record (dict): One video record.

    Returns:
        int: Its views.
    """
    return record["views"]


def main():
    """Read the command line and run the collection."""
    parser = argparse.ArgumentParser(description="Collect public YouTube Shorts metadata for research.")
    parser.add_argument("--channel", default="twitch_clips")
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument("--per-query", type=int, default=15)
    parser.add_argument("--output", default="docs/research")
    parser.add_argument("--channels-from", default="", help="A search JSON: study those channels' uploads instead")
    parser.add_argument("--uploads", type=int, default=150, help="Recent uploads per channel (channel study)")
    parser.add_argument("--min-seed", type=int, default=1, help="Only channels with this many search hits")
    arguments = parser.parse_args()
    from dotenv import load_dotenv
    load_dotenv()
    if arguments.channels_from:
        path = collect_channels(arguments.channel, arguments.channels_from, arguments.uploads,
                                arguments.output, arguments.min_seed)
    else:
        path = collect(arguments.channel, DEFAULT_QUERIES, arguments.days, arguments.per_query, arguments.output)
    print("Saved", path)



# ---------------------------------------------------------------------------
# Within-channel study: what separates a channel's hits from its flops?
#
# Comparing different channels mostly measures channel size. Comparing videos
# of the SAME channel (same audience, same editor) shows which choices go with
# more views. Costs about 1 quota unit per 50 videos (playlistItems + videos).
# ---------------------------------------------------------------------------

PLAYLIST_URL = "https://www.googleapis.com/youtube/v3/playlistItems"
CHANNELS_URL = "https://www.googleapis.com/youtube/v3/channels"


def channel_ids_for(token, video_ids):
    """
    Find the channel of each video.

    Args:
        token (str): OAuth access token (youtube.readonly).
        video_ids (list[str]): Video IDs.

    Returns:
        dict: channel ID → channel title.
    """
    from src.dashboard.accounts import api_json

    channels = {}
    for start in range(0, len(video_ids), 50):
        data = api_json("GET", VIDEOS_URL, token=token,
                        params={"part": "snippet", "id": ",".join(video_ids[start:start + 50])})
        for item in data.get("items", []):
            snippet = item.get("snippet", {})
            channels[snippet.get("channelId")] = snippet.get("channelTitle", "")
    return channels


def channel_upload_ids(token, channel_id, limit=200):
    """
    List a channel's most recent uploads (newest first).

    Args:
        token (str): OAuth access token.
        channel_id (str): The channel.
        limit (int): Most videos to list.

    Returns:
        list[str]: Video IDs.
    """
    from src.dashboard.accounts import api_json

    data = api_json("GET", CHANNELS_URL, token=token, params={"part": "contentDetails", "id": channel_id})
    items = data.get("items", [])
    if not items:
        return []
    playlist = items[0]["contentDetails"]["relatedPlaylists"]["uploads"]
    ids = []
    page_token = None
    while len(ids) < limit:
        params = {"part": "contentDetails", "playlistId": playlist, "maxResults": 50}
        if page_token:
            params["pageToken"] = page_token
        data = api_json("GET", PLAYLIST_URL, token=token, params=params)
        for item in data.get("items", []):
            ids.append(item["contentDetails"]["videoId"])
        page_token = data.get("nextPageToken")
        if not page_token:
            break
    return ids[:limit]


def extra_title_features(title):
    """
    More measurable title traits used in the within-channel comparison.

    Args:
        title (str): A video title.

    Returns:
        dict: True/False traits (question, quote, past-tense verb, a second person named,
              "reacts", "when", hashtags) and the character count.
    """
    lower = title.lower()
    words = re.findall(r"[a-z']+", lower)
    past = 0
    for word in words:
        if word.endswith("ed") or word in ("got", "made", "said", "told", "found", "took", "went", "lost",
                                          "won", "caught", "thought", "tried", "ate", "broke", "gave"):
            past += 1
    capitalised_names = re.findall(r"(?<!^)(?<![.!?] )\b[A-Z][a-z]+\b", title)
    return {"chars": len(title), "question": "?" in title, "quote": '"' in title or "“" in title,
            "past_tense": past > 0, "reacts": "react" in lower, "when": lower.startswith("when ") or " when " in lower,
            "multiple_names": len(capitalised_names) >= 2, "sob_emoji": "😭" in title,
            "skull_emoji": "💀" in title}


def compare_halves(records, minimum_per_channel=20):
    """
    For each channel, rank its videos by views and compare the top quarter with the bottom quarter.

    Args:
        records (list[dict]): Videos with "channel", "views" and features.
        minimum_per_channel (int): Channels with fewer videos are left out.

    Returns:
        dict: For each feature, its average in top vs. bottom quarters (pooled across channels),
              plus the number of channels and videos compared.
    """
    by_channel = {}
    for record in records:
        if record["seconds"] <= 180:
            by_channel.setdefault(record["channel"], []).append(record)
    numeric = ["seconds", "words", "chars", "capital_share", "emoji"]
    flags = ["all_lowercase", "has_hashtag", "question", "quote", "past_tense", "reacts", "when",
             "multiple_names", "sob_emoji", "skull_emoji"]
    top = []
    bottom = []
    channels = 0
    for channel, videos in by_channel.items():
        if len(videos) < minimum_per_channel:
            continue
        channels += 1
        videos.sort(key=views_of, reverse=True)
        quarter = max(1, len(videos) // 4)
        top.extend(videos[:quarter])
        bottom.extend(videos[-quarter:])
    result = {"channels": channels, "top_videos": len(top), "bottom_videos": len(bottom), "features": {}}
    for name in numeric + flags:
        top_values = []
        bottom_values = []
        for record in top:
            top_values.append(float(record.get(name, 0)))
        for record in bottom:
            bottom_values.append(float(record.get(name, 0)))
        if top_values and bottom_values:
            result["features"][name] = {"top_quarter": round(statistics.mean(top_values), 3),
                                        "bottom_quarter": round(statistics.mean(bottom_values), 3)}
    return result


def channel_names(records):
    """
    The distinct channel names in a list of video records, sorted.

    Args:
        records (list[dict]): Video records.

    Returns:
        list[str]: Channel names.
    """
    names = set()
    for record in records:
        names.add(record["channel"])
    return sorted(names)


def collect_channels(channel, seed_file, uploads_per_channel, output_folder, minimum_seed_videos=1):
    """
    Study the clip channels found in an earlier search file, video by video.

    Args:
        channel (str): Our channel whose YouTube connection is used (read-only).
        seed_file (str): A youtube_shorts_*.json file from collect().
        uploads_per_channel (int): Recent uploads read per channel.
        output_folder (str): Where to save the result.
        minimum_seed_videos (int): Only channels with at least this many hits in the seed file.

    Returns:
        str: The saved file's path.
    """
    from src.dashboard.accounts import access_token, get_account

    token = access_token(get_account(channel, "youtube"))
    seed = json.loads(Path(seed_file).read_text())
    counts = {}
    ids = []
    for video in seed["videos"]:
        counts[video["channel"]] = counts.get(video["channel"], 0) + 1
        ids.append(video["url"].rsplit("/", 1)[-1])
    channels = channel_ids_for(token, ids)
    records = []
    for channel_id, title in channels.items():
        if counts.get(title, 0) < minimum_seed_videos:
            continue
        upload_ids = channel_upload_ids(token, channel_id, uploads_per_channel)
        for start in range(0, len(upload_ids), 50):
            for record in video_details(token, upload_ids[start:start + 50]):
                record.update(extra_title_features(record["title"]))
                records.append(record)
    comparison = compare_halves(records)
    result = {"collected_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "method": "Uploads playlists of clip channels found by the search study; each channel's "
                        "top vs bottom quarter by views compared (controls for channel size).",
              "channels": channel_names(records),
              "comparison": comparison, "videos": records}
    path = Path(output_folder) / f"youtube_channels_{time.strftime('%Y-%m-%d')}.json"
    path.write_text(json.dumps(result, indent=2, ensure_ascii=False))
    return str(path)


if __name__ == "__main__":
    main()

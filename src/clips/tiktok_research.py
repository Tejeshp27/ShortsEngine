# =============================================================
# src/clips/tiktok_research.py — find what makes TikTok videos get
#                                views, comments and shares
#
# Used for docs/TIKTOK_RESEARCH.md. The owner browsed TikTok (their
# own logged-in For You feed plus searches) and the public numbers
# of every video shown were saved, one video per line, in
# docs/research/tiktok_videos_<date>.jsonl. Each line holds public
# facts only: views, likes, comments, shares, saves, length, caption,
# hashtags, sound, posting time and the creator's follower count.
#
# This file reads those lines and answers three questions:
#   1. Which videos got the most views, comments and shares?
#   2. Which features (length, caption, hashtags, sound, creator size,
#      age) go together with high views, comment rates and share rates?
#   3. Which hashtags show up more often among the big hits than
#      among the rest?
#
# It makes no network calls and costs nothing.
#
# Run:  python -m src.clips.tiktok_research docs/research/tiktok_videos_2026-11-21.jsonl
# =============================================================

import argparse
import json
import re
import statistics
import time
from pathlib import Path

# Length groups in seconds: (label, lowest, highest). Highest is not included.
DURATION_BUCKETS = [
    ("under 10 s", 0, 10),
    ("10-20 s", 10, 20),
    ("20-30 s", 20, 30),
    ("30-45 s", 30, 45),
    ("45-60 s", 45, 60),
    ("60-90 s", 60, 90),
    ("90-180 s", 90, 180),
    ("3 min+", 180, 10 ** 9),
]

# Creator size groups by follower count.
FOLLOWER_BUCKETS = [
    ("under 10k", 0, 10_000),
    ("10k-100k", 10_000, 100_000),
    ("100k-1M", 100_000, 1_000_000),
    ("1M-10M", 1_000_000, 10_000_000),
    ("10M+", 10_000_000, 10 ** 12),
]

# Age groups in days since the video was posted.
AGE_BUCKETS = [
    ("under 1 day", 0, 1),
    ("1-3 days", 1, 3),
    ("3-7 days", 3, 7),
    ("1-4 weeks", 7, 28),
    ("1-6 months", 28, 180),
    ("6 months+", 180, 10 ** 6),
]

# Number of hashtags in the caption.
HASHTAG_BUCKETS = [
    ("0 hashtags", 0, 1),
    ("1-2 hashtags", 1, 3),
    ("3-5 hashtags", 3, 6),
    ("6+ hashtags", 6, 10 ** 6),
]

# Caption words (not counting hashtags or @names).
CAPTION_BUCKETS = [
    ("no words", 0, 1),
    ("1-5 words", 1, 6),
    ("6-12 words", 6, 13),
    ("13-25 words", 13, 26),
    ("26+ words", 26, 10 ** 6),
]

# Hashtags that say nothing about the video (used only to label them in the report).
GENERIC_HASHTAGS = {"fyp", "foryou", "foryoupage", "fy", "viral", "fypシ", "fypage",
                    "trending", "xyzbca", "explore", "capcut", "fypシ゚viral", "foryourpage"}

# Matches most emoji characters (the common emoji blocks of Unicode).
EMOJI_PATTERN = re.compile("[\U0001F300-\U0001FAFF☀-➿\U0001F1E6-\U0001F1FF]")

# A video needs at least this many views before its rates (comments per
# 1,000 views and so on) are trusted; tiny videos give jumpy rates.
MIN_VIEWS_FOR_RATES = 10_000


def load_videos(path):
    """
    Read the saved video lines and keep the usable ones.

    Args:
        path (str or Path): The .jsonl file, one video per line.

    Returns:
        list[dict]: One dictionary per video. Adverts, videos without a view
                    count and repeats of the same video are left out.

    Example:
        videos = load_videos("docs/research/tiktok_videos_2026-11-21.jsonl")
    """
    videos = []
    seen_ids = set()
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        record = json.loads(line)
        if record.get("is_ad"):
            continue
        if record.get("views") is None or record["id"] in seen_ids:
            continue
        seen_ids.add(record["id"])
        videos.append(record)
    return videos


def caption_words(description):
    """
    Count the real words in a caption, ignoring hashtags and @names.

    Args:
        description (str): The caption as posted.

    Returns:
        int: How many plain words it has.

    Example:
        caption_words("he did NOT see that coming #fyp @bob")  ->  6
    """
    count = 0
    for word in (description or "").split():
        if word.startswith("#") or word.startswith("@"):
            continue
        count += 1
    return count


def add_measures(video, now=None):
    """
    Work out the extra numbers used in the report for one video.

    Adds: age in days, views per day, likes/comments/shares/saves per
    1,000 views, views per follower, caption word count, hashtag count
    and simple yes/no features (question, emoji, original sound).

    Args:
        video (dict): One saved video.
        now (float): The time to measure age from (seconds since 1970);
                     the collection time of the video is used if missing.

    Returns:
        dict: The same video with the new keys added.
    """
    if now is None:
        now = time.time()
    created = video.get("created") or now
    age_days = max((now - created) / 86400, 0.5)   # at least half a day, so new videos are not divided by ~0
    views = video["views"] or 0
    video["age_days"] = age_days
    video["views_per_day"] = views / age_days
    for name in ["likes", "comments", "shares", "saves"]:
        value = video.get(name)
        if views >= MIN_VIEWS_FOR_RATES and value is not None:
            video[name + "_per_1k"] = value * 1000 / views
        else:
            video[name + "_per_1k"] = None
    followers = video.get("followers")
    if followers:
        video["views_per_follower"] = views / followers
    else:
        video["views_per_follower"] = None
    description = video.get("desc") or ""
    video["caption_words"] = caption_words(description)
    video["hashtag_count"] = len(video.get("hashtags") or [])
    video["has_question"] = "?" in description
    video["has_emoji"] = bool(EMOJI_PATTERN.search(description))
    return video


def bucket_name(value, buckets):
    """
    Find which group a number falls into.

    Args:
        value (float): The number, for example a length in seconds.
        buckets (list): (label, lowest, highest) groups.

    Returns:
        str: The group's label, or "unknown" when the value is missing.

    Example:
        bucket_name(25, DURATION_BUCKETS)  ->  "20-30 s"
    """
    if value is None:
        return "unknown"
    for label, lowest, highest in buckets:
        if lowest <= value < highest:
            return label
    return "unknown"


def median_or_none(values):
    """
    Return the middle value of a list, ignoring missing values.

    Args:
        values (list): Numbers, possibly with None among them.

    Returns:
        float or None: The median, or None when nothing is left.
    """
    kept = []
    for value in values:
        if value is not None:
            kept.append(value)
    if not kept:
        return None
    return statistics.median(kept)


def group_summary(videos, feature_of):
    """
    Split videos into groups by one feature and describe each group.

    Medians are used rather than averages because a single huge hit
    would otherwise make a whole group look good.

    Args:
        videos (list[dict]): Videos with measures added.
        feature_of (function): Takes a video and returns its group label.

    Returns:
        list[dict]: One row per group with the number of videos, median
                    views, median views per day, the share of the group
                    that is a "hit" (top quarter by views per day) and
                    median comments/shares/saves per 1,000 views.
    """
    hit_line = top_quarter_line(videos)
    groups = {}
    for video in videos:
        label = feature_of(video)
        if label not in groups:
            groups[label] = []
        groups[label].append(video)
    rows = []
    for label, members in groups.items():
        hits = 0
        for video in members:
            if video["views_per_day"] >= hit_line:
                hits += 1
        rows.append({
            "group": label,
            "videos": len(members),
            "median_views": median_or_none([v["views"] for v in members]),
            "median_views_per_day": median_or_none([v["views_per_day"] for v in members]),
            "hit_share": hits / len(members),
            "comments_per_1k": median_or_none([v["comments_per_1k"] for v in members]),
            "shares_per_1k": median_or_none([v["shares_per_1k"] for v in members]),
            "saves_per_1k": median_or_none([v["saves_per_1k"] for v in members]),
            "likes_per_1k": median_or_none([v["likes_per_1k"] for v in members]),
        })
    return rows


def top_quarter_line(videos):
    """
    Find the views-per-day level that only the best quarter of videos reach.

    Args:
        videos (list[dict]): Videos with measures added.

    Returns:
        float: The 75th-percentile views per day (0 when there are no videos).
    """
    if not videos:
        return 0
    speeds = sorted(v["views_per_day"] for v in videos)
    position = int(len(speeds) * 0.75)
    position = min(position, len(speeds) - 1)
    return speeds[position]


def hashtag_lift(videos, minimum_uses=5):
    """
    Compare how often each hashtag appears among hits versus the rest.

    A "lift" of 2.0 means the hashtag is twice as common among the top
    quarter (by views per day) as among the other three quarters.

    Args:
        videos (list[dict]): Videos with measures added.
        minimum_uses (int): Ignore hashtags used fewer times than this.

    Returns:
        list[dict]: Hashtag, total uses, uses among hits, and lift,
                    sorted from highest lift to lowest.
    """
    hit_line = top_quarter_line(videos)
    hits = []
    others = []
    for video in videos:
        if video["views_per_day"] >= hit_line:
            hits.append(video)
        else:
            others.append(video)
    if not hits or not others:
        return []
    counts = {}
    for video in videos:
        # set() so a hashtag typed twice in one caption counts once
        for tag in set(t.lower() for t in (video.get("hashtags") or [])):
            if tag not in counts:
                counts[tag] = {"hits": 0, "others": 0}
            if video["views_per_day"] >= hit_line:
                counts[tag]["hits"] += 1
            else:
                counts[tag]["others"] += 1
    rows = []
    for tag, count in counts.items():
        total = count["hits"] + count["others"]
        if total < minimum_uses:
            continue
        hit_rate = count["hits"] / len(hits)
        other_rate = count["others"] / len(others)
        # +0.5 smoothing so a tag never seen among "others" does not divide by zero
        lift = (count["hits"] + 0.5) / len(hits) / ((count["others"] + 0.5) / len(others))
        rows.append({"hashtag": tag, "uses": total, "among_hits": count["hits"],
                     "hit_rate": hit_rate, "other_rate": other_rate, "lift": lift,
                     "generic": tag in GENERIC_HASHTAGS})
    rows.sort(key=sort_by_lift, reverse=True)
    return rows


def sort_by_lift(row):
    """
    Give the value used to sort hashtag rows (their lift).

    Args:
        row (dict): One hashtag row from hashtag_lift.

    Returns:
        float: The lift.
    """
    return row["lift"]


def top_videos(videos, key, count=15, minimum_views=0):
    """
    List the best videos by one number.

    Args:
        videos (list[dict]): Videos with measures added.
        key (str): The number to rank by, for example "shares" or "comments_per_1k".
        count (int): How many to return.
        minimum_views (int): Skip videos with fewer views (useful for rates).

    Returns:
        list[dict]: The top videos, best first.
    """
    usable = []
    for video in videos:
        if video.get(key) is None or video["views"] < minimum_views:
            continue
        usable.append(video)
    usable.sort(key=lambda video: video[key], reverse=True)   # lambda: "sort by this video's value"
    return usable[:count]


def format_number(value):
    """
    Write a number the short way people read on TikTok (1.2M, 34K).

    Args:
        value (float or None): The number.

    Returns:
        str: The short text, or "-" when missing.

    Example:
        format_number(1234567)  ->  "1.2M"
    """
    if value is None:
        return "-"
    if value >= 1_000_000_000:
        return f"{value / 1_000_000_000:.1f}B"
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"{value / 1_000:.0f}K"
    if value >= 10:
        return f"{value:.0f}"
    return f"{value:.2f}"


def group_table(title, rows, order=None):
    """
    Turn group_summary rows into a Markdown table.

    Args:
        title (str): Heading above the table.
        rows (list[dict]): Rows from group_summary.
        order (list[str]): Group labels in the order to show them;
                           otherwise sorted by number of videos.

    Returns:
        str: The Markdown text.
    """
    if order:
        ordered = []
        for label in order + ["unknown"]:
            for row in rows:
                if row["group"] == label:
                    ordered.append(row)
        for row in rows:
            if row not in ordered:
                ordered.append(row)
        rows = ordered
    else:
        rows = sorted(rows, key=lambda row: row["videos"], reverse=True)
    lines = [f"### {title}", "",
             "| Group | Videos | Median views | Median views/day | Hit share | Comments/1k | Shares/1k | Saves/1k | Likes/1k |",
             "|---|---|---|---|---|---|---|---|---|"]
    for row in rows:
        lines.append(f"| {row['group']} | {row['videos']} | {format_number(row['median_views'])} | "
                     f"{format_number(row['median_views_per_day'])} | {row['hit_share']:.0%} | "
                     f"{format_number(row['comments_per_1k'])} | {format_number(row['shares_per_1k'])} | "
                     f"{format_number(row['saves_per_1k'])} | {format_number(row['likes_per_1k'])} |")
    return "\n".join(lines) + "\n"


def short_caption(video, limit=70):
    """
    Shorten a caption for a table cell (and remove characters that break tables).

    Args:
        video (dict): One video.
        limit (int): Maximum characters.

    Returns:
        str: The shortened caption.
    """
    text = (video.get("desc") or "").replace("|", "/").replace("\n", " ").strip()
    if len(text) > limit:
        text = text[:limit - 1] + "…"
    return text or "(no caption)"


def video_table(title, videos):
    """
    Turn a list of videos into a Markdown table with their numbers.

    Args:
        title (str): Heading above the table.
        videos (list[dict]): Videos to list.

    Returns:
        str: The Markdown text.
    """
    lines = [f"### {title}", "",
             "| Creator | Followers | Length | Views | Comments | Shares | Saves | Age (days) | Caption |",
             "|---|---|---|---|---|---|---|---|---|"]
    for video in videos:
        lines.append(f"| @{video.get('author')} | {format_number(video.get('followers'))} | "
                     f"{video.get('duration') or '-'} s | {format_number(video['views'])} | "
                     f"{format_number(video.get('comments'))} | {format_number(video.get('shares'))} | "
                     f"{format_number(video.get('saves'))} | {video['age_days']:.0f} | {short_caption(video)} |")
    return "\n".join(lines) + "\n"


def source_group(video):
    """
    Name where a video was found ("foryou" or "search", "profile" and so on).

    Args:
        video (dict): One video.

    Returns:
        str: The first part of its source label.
    """
    return (video.get("source") or "unknown").split(":")[0]


def build_report(videos):
    """
    Write the full Markdown report for a set of videos.

    Args:
        videos (list[dict]): Videos with measures added.

    Returns:
        str: The report text.
    """
    parts = [f"Videos analysed: **{len(videos)}** (adverts removed). "
             f"Hit = top quarter by views per day (at least {format_number(top_quarter_line(videos))}/day).\n"]
    parts.append(group_table("Where the video was found", group_summary(videos, source_group)))
    parts.append(group_table("Length", group_summary(
        videos, lambda v: bucket_name(v.get("duration"), DURATION_BUCKETS)),
        [b[0] for b in DURATION_BUCKETS]))
    parts.append(group_table("Creator size (followers)", group_summary(
        videos, lambda v: bucket_name(v.get("followers"), FOLLOWER_BUCKETS)),
        [b[0] for b in FOLLOWER_BUCKETS]))
    parts.append(group_table("Age when seen", group_summary(
        videos, lambda v: bucket_name(v["age_days"], AGE_BUCKETS)),
        [b[0] for b in AGE_BUCKETS]))
    parts.append(group_table("Caption length", group_summary(
        videos, lambda v: bucket_name(v["caption_words"], CAPTION_BUCKETS)),
        [b[0] for b in CAPTION_BUCKETS]))
    parts.append(group_table("Hashtags", group_summary(
        videos, lambda v: bucket_name(v["hashtag_count"], HASHTAG_BUCKETS)),
        [b[0] for b in HASHTAG_BUCKETS]))
    parts.append(group_table("Caption asks a question", group_summary(
        videos, lambda v: "question" if v["has_question"] else "no question")))
    parts.append(group_table("Caption has an emoji", group_summary(
        videos, lambda v: "emoji" if v["has_emoji"] else "no emoji")))
    parts.append(group_table("Sound", group_summary(
        videos, lambda v: "original sound" if v.get("music_original") else "existing song/sound")))
    parts.append(group_table("Photo slideshow or video", group_summary(
        videos, lambda v: "slideshow" if v.get("is_slideshow") else "video")))

    lift_rows = hashtag_lift(videos)
    lines = ["### Hashtags most over-represented among hits (used 5+ times)", "",
             "| Hashtag | Uses | Among hits | Lift |", "|---|---|---|---|"]
    for row in lift_rows[:25]:
        lines.append(f"| #{row['hashtag']}{' (generic)' if row['generic'] else ''} | {row['uses']} | "
                     f"{row['among_hits']} | {row['lift']:.1f}x |")
    lines.append("")
    lines.append("Least common among hits:")
    lines.append("")
    lines.append("| Hashtag | Uses | Among hits | Lift |")
    lines.append("|---|---|---|---|")
    for row in lift_rows[-10:]:
        lines.append(f"| #{row['hashtag']}{' (generic)' if row['generic'] else ''} | {row['uses']} | "
                     f"{row['among_hits']} | {row['lift']:.1f}x |")
    parts.append("\n".join(lines) + "\n")

    parts.append(video_table("Most views", top_videos(videos, "views")))
    parts.append(video_table("Fastest (views per day)", top_videos(videos, "views_per_day")))
    parts.append(video_table("Most comments", top_videos(videos, "comments")))
    parts.append(video_table("Most shares", top_videos(videos, "shares")))
    parts.append(video_table("Most saves", top_videos(videos, "saves")))
    parts.append(video_table("Highest comments per 1k views (100k+ views)",
                             top_videos(videos, "comments_per_1k", minimum_views=100_000)))
    parts.append(video_table("Highest shares per 1k views (100k+ views)",
                             top_videos(videos, "shares_per_1k", minimum_views=100_000)))
    parts.append(video_table("Most views per follower (breakout reach, 100k+ views)",
                             top_videos(videos, "views_per_follower", minimum_views=100_000)))
    return "\n".join(parts)


def main():
    """
    Read the saved videos, write the report and print where it went.

    Command-line inputs: the .jsonl file(s) and --output for the report path.
    """
    parser = argparse.ArgumentParser(description="Analyse saved TikTok video numbers.")
    parser.add_argument("paths", nargs="+", help="one or more .jsonl files")
    parser.add_argument("--output", default="docs/research/tiktok_analysis.md",
                        help="where to write the Markdown tables")
    arguments = parser.parse_args()
    videos = []
    for path in arguments.paths:
        videos.extend(load_videos(path))
    for video in videos:
        add_measures(video)
    report = build_report(videos)
    Path(arguments.output).write_text(report, encoding="utf-8")
    print(f"{len(videos)} videos analysed → {arguments.output}")


if __name__ == "__main__":
    main()

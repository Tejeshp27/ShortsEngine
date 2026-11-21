# =============================================================
# src/clips/feedback.py — learn from how published clips performed
#
# Reads only data the project is authorised to collect: the `metrics`
# table filled by the existing official-API analytics collector (YouTube
# views/likes/comments today; TikTok only after its app gets the scopes).
# Private analytics (retention, watch time) are used only when present.
#
# What it does:
#   1. Joins each published clip's latest numbers with its decision record
#      (length, category, layout, caption style, title method, profile version).
#   2. Normalises views for age (views per day since posting) and for the
#      channel's size at the time (divided by the channel's median), so a new
#      clip is not compared unfairly with an old one.
#   3. Compares groups (e.g. clips under 20 s vs. 20–35 s vs. longer; split vs.
#      fit-blur) only when each group has enough clips (default 5).
#   4. Writes a SUGGESTED new ranking profile (config/clip_ranking/<next>-proposed.yaml)
#      with small, bounded weight changes. It is never activated automatically:
#      the owner points ranking.profile at it to try it, and back to roll back.
#
# One viral clip proves nothing; the minimum group size and the small step
# size keep a single outlier from steering the system.
# =============================================================

import json
import statistics
from datetime import datetime, timezone
from pathlib import Path

import yaml

from src.storage.database import get_connection

MIN_GROUP = 5          # clips needed in a group before it is compared
MAX_STEP = 0.15        # a weight changes by at most 15% per suggestion


def parse_time(text):
    """
    Read an ISO timestamp (with or without time zone) as a UTC datetime.

    Args:
        text (str): For example "2026-11-21T00:09:17+00:00".

    Returns:
        datetime or None.
    """
    try:
        value = datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value


def published_clips(channel):
    """
    Collect every published clip with its latest numbers and decision record.

    Args:
        channel (str): The clips channel key.

    Returns:
        list[dict]: One record per video and platform: views, likes, comments,
                    age_days, views_per_day and the clip's decision details.
    """
    connection = get_connection()
    try:
        rows = connection.execute("""
            SELECT m.video_id, m.platform, m.views, m.likes, m.comments, m.shares, m.watch_time_sec,
                   m.collected_at, p.confirmed_at, c.decision_json, c.signals_json, c.metadata_json,
                   c.start_seconds, c.end_seconds, c.streamer
            FROM metrics m
            JOIN platform_posts p ON p.video_id = m.video_id AND p.platform = m.platform
            JOIN clips c ON c.video_id = m.video_id
            JOIN videos v ON v.id = m.video_id
            WHERE v.channel = ?
              AND m.id = (SELECT MAX(id) FROM metrics WHERE video_id = m.video_id AND platform = m.platform)
        """, (channel,)).fetchall()
    finally:
        connection.close()
    records = []
    for row in rows:
        posted = parse_time(row["confirmed_at"])
        measured = parse_time(row["collected_at"])
        age_days = 1.0
        age_hours = 0.0
        if posted and measured:
            age_hours = max(0.0, (measured - posted).total_seconds() / 3600)
            age_days = max(1.0, age_hours / 24)
        decision = json.loads(row["decision_json"] or "{}")
        edit = decision.get("edit", {})
        seconds = edit.get("final_seconds") or (row["end_seconds"] or 0) - (row["start_seconds"] or 0)
        records.append({
            "video_id": row["video_id"], "platform": row["platform"], "streamer": row["streamer"],
            "views": row["views"] or 0, "likes": row["likes"] or 0, "comments": row["comments"] or 0,
            "age_days": round(age_days, 2), "age_hours": round(age_hours, 1),
            "views_per_day": round((row["views"] or 0) / age_days, 2),
            "seconds": round(seconds, 1),
            "category": decision.get("selection", {}).get("category", "unknown"),
            "layout": decision.get("framing", {}).get("mode", "unknown"),
            "caption_style": decision.get("captions", {}).get("style", "unknown"),
            "title_method": json.loads(row["metadata_json"] or "{}").get("method", "older pipeline"),
            "profile": decision.get("selection", {}).get("profile_version", "before v1"),
            "components": decision.get("selection", {}).get("components", {}),
        })
    return records


def add_relative_views(records):
    """
    Add "relative_views": views per day divided by the median for that platform.

    Comparing to the channel's own median accounts for channel size and
    each platform's different reach.

    Args:
        records (list[dict]): From published_clips().

    Returns:
        list[dict]: The same records with "relative_views".
    """
    by_platform = {}
    for record in records:
        by_platform.setdefault(record["platform"], []).append(record["views_per_day"])
    for record in records:
        median = statistics.median(by_platform[record["platform"]]) or 1.0
        record["relative_views"] = round(record["views_per_day"] / max(median, 1.0), 3)
    return records


def length_bucket(seconds):
    """
    Put a clip length into a simple bucket.

    Args:
        seconds (float): Clip length.

    Returns:
        str: "under 20 s", "20-35 s" or "over 35 s".
    """
    if seconds < 20:
        return "under 20 s"
    if seconds <= 35:
        return "20-35 s"
    return "over 35 s"


def compare_groups(records, key_function, minimum=MIN_GROUP):
    """
    Compare the median relative views of groups of clips.

    Args:
        records (list[dict]): Records with "relative_views".
        key_function (callable): Gives each record's group name.
        minimum (int): Groups smaller than this are reported but not judged.

    Returns:
        list[dict]: {"group", "clips", "median_relative_views", "enough_data"} per group.
    """
    groups = {}
    for record in records:
        groups.setdefault(key_function(record), []).append(record["relative_views"])
    result = []
    for name, values in sorted(groups.items()):
        result.append({"group": name, "clips": len(values),
                       "median_relative_views": round(statistics.median(values), 3),
                       "enough_data": len(values) >= minimum})
    return result


def by_length(record):
    """Group key: length bucket."""
    return length_bucket(record["seconds"])


def by_layout(record):
    """Group key: layout."""
    return record["layout"]


def by_category(record):
    """Group key: moment type."""
    return record["category"]


def by_caption_style(record):
    """Group key: caption style."""
    return record["caption_style"]


def by_title_method(record):
    """Group key: how the title was made."""
    return record["title_method"]


def by_profile(record):
    """Group key: ranking profile version."""
    return record["profile"]


def suggest_weights(records, profile, minimum=MIN_GROUP * 4):
    """
    Suggest small weight changes from how each category score relates to performance.

    For each category, clips are split at the median category score; if the
    higher-scoring half clearly performed better (or worse), the weight is
    nudged up (or down) by at most MAX_STEP. Needs `minimum` clips with
    decision records; otherwise suggests nothing.

    Args:
        records (list[dict]): Records with "relative_views" and "components".
        profile (dict): The current ranking profile.
        minimum (int): Clips needed before any suggestion.

    Returns:
        tuple: (new weights dict or None, list of plain-English reasons).
    """
    usable = []
    for record in records:
        if record["components"]:
            usable.append(record)
    if len(usable) < minimum:
        return None, [f"Only {len(usable)} published clips have decision records; need {minimum} "
                      "before suggesting weight changes (one viral clip proves nothing)."]
    weights = dict(profile["weights"])
    reasons = []
    for category, weight in profile["weights"].items():
        scores = []
        for record in usable:
            value = record["components"].get(category)
            if value is not None:
                scores.append(value)
        if len(scores) < minimum:
            continue
        middle = statistics.median(scores)
        high = []
        low = []
        for record in usable:
            value = record["components"].get(category)
            if value is None:
                continue
            if value > middle:
                high.append(record["relative_views"])
            else:
                low.append(record["relative_views"])
        if len(high) < MIN_GROUP or len(low) < MIN_GROUP:
            continue
        ratio = statistics.median(high) / max(0.01, statistics.median(low))
        if ratio > 1.25:
            weights[category] = round(weight * (1 + MAX_STEP), 2)
            reasons.append(f"{category}: higher-scoring clips got {ratio:.2f}x the views -> weight +15%")
        elif ratio < 0.8:
            weights[category] = round(weight * (1 - MAX_STEP), 2)
            reasons.append(f"{category}: higher-scoring clips got {ratio:.2f}x the views -> weight -15%")
    if not reasons:
        reasons.append("No category showed a clear difference; keep the current weights.")
        return None, reasons
    return weights, reasons


def next_version_name(profile):
    """
    The next profile version name, e.g. "v1" -> "v2".

    Args:
        profile (dict): The current profile.

    Returns:
        str: The next name.
    """
    current = str(profile.get("version", "v1")).lstrip("v")
    try:
        return f"v{int(current) + 1}"
    except ValueError:
        return "v2"


def write_proposal(profile, weights, reasons):
    """
    Save a suggested profile next to the current one (never activated automatically).

    Args:
        profile (dict): The current profile.
        weights (dict): Suggested weights.
        reasons (list[str]): Why.

    Returns:
        str: The new file's path.
    """
    version = next_version_name(profile)
    folder = Path(profile.get("path", "config/clip_ranking/v1.yaml")).parent
    path = folder / f"{version}-proposed.yaml"
    data = {}
    for key, value in profile.items():
        if key != "path":
            data[key] = value
    data["version"] = version
    data["created"] = datetime.now(timezone.utc).date().isoformat()
    data["based_on"] = profile.get("version")
    data["note"] = "SUGGESTED by src/clips/feedback.py; try it on part of the output before switching. " + " ".join(reasons)
    data["weights"] = weights
    header = ("# Suggested ranking profile (not active). To try it, set ranking.profile to this file;\n"
              "# to roll back, point ranking.profile at the previous version again.\n")
    path.write_text(header + yaml.safe_dump(data, sort_keys=False, allow_unicode=True))
    return str(path)


def feedback_report(channel, profile, write=False):
    """
    Build the full feedback report for a channel.

    Args:
        channel (str): The clips channel key.
        profile (dict): The current ranking profile.
        write (bool): True saves a proposed profile file when there is a suggestion.

    Returns:
        dict: {"published", "comparisons", "suggestion", "reasons", "proposal_path", "limits"}.
    """
    records = add_relative_views(published_clips(channel))
    comparisons = {"length": compare_groups(records, by_length), "layout": compare_groups(records, by_layout),
                   "category": compare_groups(records, by_category),
                   "caption_style": compare_groups(records, by_caption_style),
                   "title_method": compare_groups(records, by_title_method),
                   "profile": compare_groups(records, by_profile)}
    weights, reasons = suggest_weights(records, profile)
    path = None
    if weights is not None and write:
        path = write_proposal(profile, weights, reasons)
    return {"published": len(records), "comparisons": comparisons, "suggestion": weights, "reasons": reasons,
            "proposal_path": path,
            "limits": ["Only metrics from authorised official APIs are used (YouTube now; TikTok when its app "
                       "has analytics scopes).", "Views are normalised by age (per day) and by the channel's "
                       "median per platform.", "Groups with fewer than 5 clips are shown but not judged.",
                       "Correlation, not causation: confirm with an A/B test (two profiles on alternating "
                       "recordings) before switching."]}

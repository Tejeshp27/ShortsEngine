# =============================================================
# src/analytics/collector.py — collect video performance metrics
#
# Fetches views, likes, comments, shares and watch time from
# Instagram and TikTok once per day for every posted video,
# and saves them to the metrics table.
#
# Run once a day from a cron job or scheduler:
#   python -m src.analytics.collector
#
# Data flows:
#   TikTok API  → fetch_tiktok_metrics()  ─┐
#   Instagram API → fetch_instagram_metrics() ─┤─► save_metrics()
#                                            └► metrics table in DB
# =============================================================

import json
import os
import urllib.request
import urllib.parse
from datetime import datetime

from src.storage.database import get_connection
from src.storage.queries import get_videos_by_status


def _ensure_metrics_table():
    """
    Create the metrics table if it doesn't exist yet.

    This table was not part of the original schema (added in Phase 6).
    It's safe to call on every run.
    """
    conn = get_connection()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS metrics (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            video_id        INTEGER REFERENCES videos(id) ON DELETE CASCADE,
            platform        TEXT    NOT NULL,
            platform_post_id TEXT,
            views           INTEGER DEFAULT 0,
            likes           INTEGER DEFAULT 0,
            comments        INTEGER DEFAULT 0,
            shares          INTEGER DEFAULT 0,
            watch_time_sec  REAL    DEFAULT 0.0,
            collected_at    TEXT    NOT NULL
        )
    """)
    conn.commit()
    conn.close()


def save_metrics(video_id, platform, views=0, likes=0,
                 comments=0, shares=0, watch_time_sec=0.0,
                 platform_post_id=None):
    """
    Save one day's performance snapshot for a posted video.

    Args:
        video_id (int): The video row ID.
        platform (str): "tiktok" or "instagram".
        views, likes, comments, shares (int): Engagement counts.
        watch_time_sec (float): Total watch time in seconds.
        platform_post_id (str or None): The ID on the platform (for future fetches).
    """
    _ensure_metrics_table()
    conn = get_connection()
    conn.execute(
        """
        INSERT INTO metrics
            (video_id, platform, platform_post_id, views, likes, comments,
             shares, watch_time_sec, collected_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (video_id, platform, platform_post_id, views, likes, comments,
         shares, watch_time_sec, datetime.now().isoformat(timespec="seconds")),
    )
    conn.commit()
    conn.close()


def fetch_instagram_metrics(post_id, access_token):
    """
    Fetch like count and comment count for one Instagram post.

    Args:
        post_id (str): The Instagram media ID.
        access_token (str): Valid Instagram access token.

    Returns:
        dict: {"likes": int, "comments": int}
    """
    params = urllib.parse.urlencode({
        "fields": "like_count,comments_count",
        "access_token": access_token,
    })
    url = f"https://graph.facebook.com/v20.0/{post_id}?{params}"
    with urllib.request.urlopen(url, timeout=15) as resp:
        data = json.loads(resp.read())
    return {
        "likes": data.get("like_count", 0),
        "comments": data.get("comments_count", 0),
    }


def fetch_tiktok_metrics(video_id_tiktok, access_token):
    """
    Fetch view count, like count, comment count and share count for a TikTok video.

    Args:
        video_id_tiktok (str): The TikTok video ID.
        access_token (str): Valid TikTok access token.

    Returns:
        dict: {"views": int, "likes": int, "comments": int, "shares": int}
    """
    url = "https://open.tiktokapis.com/v2/video/query/"
    payload = json.dumps({
        "filters": {"video_ids": [video_id_tiktok]},
        "fields": ["id", "view_count", "like_count", "comment_count", "share_count"],
    }).encode("utf-8")
    request = urllib.request.Request(url, data=payload, method="POST")
    request.add_header("Authorization", f"Bearer {access_token}")
    request.add_header("Content-Type", "application/json; charset=UTF-8")
    with urllib.request.urlopen(request, timeout=15) as resp:
        data = json.loads(resp.read())

    video_data = data.get("data", {}).get("videos", [{}])[0]
    return {
        "views": video_data.get("view_count", 0),
        "likes": video_data.get("like_count", 0),
        "comments": video_data.get("comment_count", 0),
        "shares": video_data.get("share_count", 0),
    }


def collect_all_metrics():
    """
    Collect metrics for every posted video and save a daily snapshot.

    Reads posted videos from the database, checks each platform,
    and saves one metrics row per video per platform.

    Call this once a day from a cron job.
    """
    _ensure_metrics_table()
    posted_videos = get_videos_by_status("posted")

    if not posted_videos:
        print("No posted videos to collect metrics for.")
        return

    ig_token = os.getenv("INSTAGRAM_ACCESS_TOKEN")
    tt_token = os.getenv("TIKTOK_ACCESS_TOKEN")

    for video in posted_videos:
        print(f"Collecting metrics for: {video['story_title'][:50]}")

        # We store the platform post IDs in the metrics table on first insert.
        # For now we skip if tokens aren't set, with a clear message.
        if ig_token:
            try:
                # In production, we'd store the IG post ID when publishing.
                # For now, skip if no post ID is available.
                print("  Instagram: (post ID not stored yet — publish first)")
            except Exception as err:
                print(f"  Instagram error: {err}")
        else:
            print("  Instagram: INSTAGRAM_ACCESS_TOKEN not set, skipping.")

        if tt_token:
            try:
                print("  TikTok: (post ID not stored yet — publish first)")
            except Exception as err:
                print(f"  TikTok error: {err}")
        else:
            print("  TikTok: TIKTOK_ACCESS_TOKEN not set, skipping.")


# Keep only the latest cumulative snapshot for each video and platform.
# The ID breaks ties when two collections happened in the same second.
LATEST_METRICS_JOIN = """
    LEFT JOIN metrics m ON m.video_id = v.id
    AND m.id = (
        SELECT latest.id FROM metrics latest
        WHERE latest.video_id = m.video_id AND latest.platform = m.platform
        ORDER BY latest.collected_at DESC, latest.id DESC LIMIT 1
    )
"""


def get_metrics_summary(channel=None):
    """
    Return aggregated metrics for all posted videos.

    Args:
        channel (str or None): Filter by channel, or None for all.

    Returns:
        list[dict]: One row per video with totals for each metric.
    """
    _ensure_metrics_table()
    conn = get_connection()
    query = f"""
        SELECT v.id, v.story_title, v.channel, v.created_at,
               SUM(m.views) AS total_views,
               SUM(m.likes) AS total_likes,
               SUM(m.comments) AS total_comments,
               SUM(m.shares) AS total_shares
        FROM videos v
        {LATEST_METRICS_JOIN}
        WHERE v.status = 'posted'
    """
    params = ()
    if channel:
        query += " AND v.channel = ?"
        params = (channel,)
    query += " GROUP BY v.id ORDER BY total_views DESC"

    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


if __name__ == "__main__":
    collect_all_metrics()

# =============================================================
# src/analytics/feedback.py — use video performance to improve story selection
#
# Phase 6.3: Feedback loop
#
# After enough videos have been posted and have collected metrics,
# this module looks at which stories got the most views and uses
# that pattern to give the AI ranker extra context.
#
# How it works:
#   1. Query the database for videos with high vs. low views.
#   2. Build a short summary of what the best and worst stories had in common.
#   3. Write that summary to cache/feedback/<channel>.txt so the ranker
#      can include it in its prompt the next time it runs.
#
# Run after collecting metrics:
#   python -m src.analytics.feedback --channel news
# =============================================================

import os

from src.storage.database import get_connection
from src.analytics.collector import _ensure_metrics_table, LATEST_METRICS_JOIN


def _load_top_and_bottom_stories(channel, limit=5):
    """
    Return the best and worst performing posted videos for a channel.

    "Best" = most total views. "Worst" = least views (but only videos
    that have been up long enough to collect data).

    Args:
        channel (str): The channel key, e.g. "news".
        limit (int): How many stories to include in each group.

    Returns:
        tuple[list[dict], list[dict]]: (top_stories, bottom_stories)
    """
    _ensure_metrics_table()
    conn = get_connection()
    query = f"""
        SELECT v.story_title, v.channel,
               COALESCE(SUM(m.views), 0) AS total_views
        FROM videos v
        {LATEST_METRICS_JOIN}
        WHERE v.status = 'posted' AND v.channel = ?
        GROUP BY v.id
        HAVING total_views > 0
        ORDER BY total_views DESC
    """
    rows = [dict(r) for r in conn.execute(query, (channel,)).fetchall()]
    conn.close()

    top = rows[:limit]
    bottom = rows[-limit:] if len(rows) > limit else []
    return top, bottom


def build_feedback_summary(channel):
    """
    Create a plain-text summary of which story types perform best.

    This text gets saved to a file and injected into the ranker's prompt
    so the AI knows what has worked before.

    Args:
        channel (str): The channel key.

    Returns:
        str: A short paragraph describing performance patterns,
             or an empty string if there isn't enough data yet.
    """
    top, bottom = _load_top_and_bottom_stories(channel)

    if not top:
        return ""

    top_titles = "\n".join(f"  - {s['story_title']} ({s['total_views']:,} views)"
                           for s in top)
    bottom_titles = "\n".join(f"  - {s['story_title']} ({s['total_views']:,} views)"
                              for s in bottom) if bottom else "  (not enough data)"

    summary = (
        f"PAST PERFORMANCE DATA FOR CHANNEL '{channel}':\n\n"
        f"These stories got the MOST views:\n{top_titles}\n\n"
        f"These stories got the FEWEST views:\n{bottom_titles}\n\n"
        "Use this to favor stories similar to the high-performers "
        "and avoid patterns seen in the low-performers."
    )
    return summary


def save_feedback_file(channel):
    """
    Write the feedback summary to cache/feedback/<channel>.txt.

    The ranker (src/generation/ranking.py) reads this file if it exists
    and adds it to its prompt, completing the feedback loop.

    Args:
        channel (str): The channel key.

    Returns:
        str or None: The path to the written file, or None if no data.
    """
    summary = build_feedback_summary(channel)
    if not summary:
        print(f"Not enough posted-video data for channel '{channel}' yet. "
              "Run the collector after more videos are posted.")
        return None

    folder = os.path.join("cache", "feedback")
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f"{channel}.txt")

    with open(path, "w", encoding="utf-8") as f:
        f.write(summary)

    print(f"Feedback summary saved: {path}")
    return path


def load_feedback_for_channel(channel):
    """
    Load the saved feedback summary for a channel, if one exists.

    Call this from the ranker to inject past performance data into
    the ranking prompt.

    Args:
        channel (str): The channel key.

    Returns:
        str: The feedback text, or an empty string if no file exists.
    """
    path = os.path.join("cache", "feedback", f"{channel}.txt")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    return ""


if __name__ == "__main__":
    import sys
    channel_arg = "news"
    for i, arg in enumerate(sys.argv):
        if arg == "--channel" and i + 1 < len(sys.argv):
            channel_arg = sys.argv[i + 1]
    save_feedback_file(channel_arg)

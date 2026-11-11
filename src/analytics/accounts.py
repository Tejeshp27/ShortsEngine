# =============================================================
# src/analytics/accounts.py — daily follower and view statistics
#
# Once a day, for every connected account, this file:
#   1. saves the account's follower count, and
#   2. saves the views/likes/comments/shares of each video you
#      confirmed as publicly posted on that platform.
#
# Only CONFIRMED public post IDs are used. A TikTok upload ID only
# identifies a video sitting in the TikTok inbox, not a public video,
# so it must never be treated as one.
#
# It runs automatically while the dashboard is open (every 15
# minutes it checks for anything not yet collected today), or by hand:
#     python -m src.analytics.accounts [--channel news] [--dry-run]
# =============================================================

import argparse
import time

from dotenv import load_dotenv

from src.analytics.collector import _ensure_metrics_table, save_metrics
from src.dashboard.accounts import (TIKTOK_BASE, access_token, api_json, fetch_profile,
                                    graph_base, safe_error, save_snapshot)
from src.storage.database import create_tables, get_connection
from src.storage.finance import utc_now

# How long one collector may "hold" an account before another may try.
COLLECTION_LOCK_SECONDS = 600
FIRST_CHECK_DELAY_SECONDS = 30
CHECK_EVERY_SECONDS = 15 * 60

TIKTOK_STAT_FIELDS = ("view_count", "like_count", "comment_count", "share_count")


# ---------------------------------------------------------------------------
# Reading statistics from the platforms
# ---------------------------------------------------------------------------

def fetch_tiktok_post_metrics(post_id, token):
    """
    Get a public TikTok video's lifetime views, likes, comments and shares.

    Args:
        post_id (str): The public video ID.
        token (str): The account's access token.

    Returns:
        dict: "views", "likes", "comments" and "shares".

    Raises:
        ValueError: If TikTok can't find the video or leaves out a number.
    """
    response = api_json("POST", TIKTOK_BASE + "/video/query/", token,
                        params={"fields": "id," + ",".join(TIKTOK_STAT_FIELDS)},
                        json={"filters": {"video_ids": [post_id]}})
    videos = response.get("data", {}).get("videos", [])
    if not videos:
        raise ValueError("TikTok could not find this public video for the connected account.")
    video = videos[0]
    for field in TIKTOK_STAT_FIELDS:
        if field not in video:
            raise ValueError("TikTok did not return all requested video statistics.")
    return {"views": video["view_count"], "likes": video["like_count"],
            "comments": video["comment_count"], "shares": video["share_count"]}


def fetch_instagram_post_metrics(post_id, token):
    """
    Get an Instagram post's lifetime views, likes, comments and shares.

    Likes and comments come from the post itself; views and shares come
    from its "insights" (Meta's statistics endpoint).

    Args:
        post_id (str): The Instagram media ID.
        token (str): The access token.

    Returns:
        dict: "views", "likes", "comments" and "shares".

    Raises:
        ValueError: If the insights are not available.
    """
    media = api_json("GET", graph_base() + f"/{post_id}", token,
                     params={"fields": "like_count,comments_count"})
    insights = api_json("GET", graph_base() + f"/{post_id}/insights", token,
                        params={"metric": "views,shares"})

    # Insights arrive as a list like [{"name": "views", "values": [{"value": 120}]}].
    values = {}
    for item in insights.get("data", []):
        entries = item.get("values", [])
        if entries:
            values[item["name"]] = entries[-1]["value"]
    if "views" not in values or "shares" not in values:
        raise ValueError("Instagram insights are unavailable for this post. Check insights permission and try later.")

    return {"views": values["views"], "shares": values["shares"],
            "likes": media.get("like_count", 0), "comments": media.get("comments_count", 0)}


def fetch_post_metrics(platform, post_id, token):
    """
    Get a public post's lifetime statistics from its platform.

    Args:
        platform (str): "tiktok" or "instagram".
        post_id (str): The public post ID.
        token (str): The account's access token.

    Returns:
        dict: "views", "likes", "comments" and "shares".
    """
    if platform == "tiktok":
        return fetch_tiktok_post_metrics(post_id, token)
    return fetch_instagram_post_metrics(post_id, token)


# ---------------------------------------------------------------------------
# Small database helpers
# ---------------------------------------------------------------------------

def load_accounts(channel=None):
    """
    List connected accounts.

    Args:
        channel (str or None): Only this channel, or None for all.

    Returns:
        list[dict]: The account rows.
    """
    query = "SELECT * FROM accounts"
    values = []
    if channel:
        query += " WHERE channel = ?"
        values.append(channel)
    connection = get_connection()
    try:
        accounts = []
        for row in connection.execute(query, values):
            accounts.append(dict(row))
        return accounts
    finally:
        connection.close()


def posts_missing_today(account):
    """
    List an account's public posts that have no statistics saved today.

    Args:
        account (dict): The account row.

    Returns:
        list: platform_posts rows still to collect today.
    """
    today = utc_now()[:10]
    connection = get_connection()
    try:
        return connection.execute("""
            SELECT p.* FROM platform_posts p
            JOIN videos v ON v.id = p.video_id
            WHERE v.channel = ? AND p.platform = ?
              AND NOT EXISTS (
                  SELECT 1 FROM metrics m
                  WHERE m.video_id = p.video_id AND m.platform = p.platform
                    AND substr(m.collected_at, 1, 10) = ?
              )
        """, (account["channel"], account["platform"], today)).fetchall()
    finally:
        connection.close()


def set_account_error(account_id, message):
    """
    Save (or clear, with "") the error shown on an account's channel page.

    Args:
        account_id (int): The account.
        message (str): The error message, or "" if all went well.
    """
    connection = get_connection()
    try:
        connection.execute("UPDATE accounts SET last_error = ? WHERE id = ?", (message, account_id))
        connection.commit()
    finally:
        connection.close()


def claim_collection(account_id):
    """
    Reserve an account for collection for ten minutes.

    This stops the automatic collector and a "Collect now" click from
    reading and saving the same day's numbers at the same moment. A
    reservation older than ten minutes is cleared, in case a collector
    stopped without releasing it.

    Args:
        account_id (int): The account.

    Returns:
        bool: True if this collector got the reservation.
    """
    connection = get_connection()
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("DELETE FROM collection_locks WHERE expires_at < ?", (time.time(),))
        cursor = connection.execute("INSERT OR IGNORE INTO collection_locks VALUES (?, ?)",
                                    (account_id, time.time() + COLLECTION_LOCK_SECONDS))
        connection.commit()
        return cursor.rowcount == 1
    finally:
        connection.close()


def release_collection(account_id):
    """
    Give up an account's collection reservation.

    Args:
        account_id (int): The account.
    """
    connection = get_connection()
    try:
        connection.execute("DELETE FROM collection_locks WHERE account_id = ?", (account_id,))
        connection.commit()
    finally:
        connection.close()


# ---------------------------------------------------------------------------
# Collecting
# ---------------------------------------------------------------------------

def collect_account(account):
    """
    Save today's followers and post statistics for one account.

    Followers are only fetched once per day; posts already measured
    today are skipped.

    Args:
        account (dict): The account row.

    Raises:
        Exception: Any platform or token problem (sync_channel saves it).
    """
    token = access_token(account)

    synced_today = account["last_synced_at"] and account["last_synced_at"][:10] == utc_now()[:10]
    if not synced_today:
        profile = fetch_profile(account["platform"], token, account["platform_account_id"])
        connection = get_connection()
        try:
            save_snapshot(connection, account["id"], profile["followers"])
            connection.commit()
        finally:
            connection.close()

    for post in posts_missing_today(account):
        metrics = fetch_post_metrics(account["platform"], post["post_id"], token)
        save_metrics(post["video_id"], account["platform"], platform_post_id=post["post_id"], **metrics)


def sync_channel(channel=None, dry_run=False):
    """
    Collect today's statistics for one channel or for all channels.

    Args:
        channel (str or None): Only this channel, or None for all.
        dry_run (bool): Only count the accounts; contact no platform.

    Returns:
        str: A one-line summary for the job page or terminal.
    """
    _ensure_metrics_table()
    accounts = load_accounts(channel)
    if dry_run:
        return f"Dry run: {len(accounts)} connected account(s); no platform requests made."

    completed = 0
    errors = 0
    for account in accounts:
        if not claim_collection(account["id"]):
            continue   # another collector is working on this account
        try:
            collect_account(account)
            set_account_error(account["id"], "")
            completed += 1
        except Exception as error:
            # One account failing must not stop the others; its error is
            # shown on its channel page instead.
            errors += 1
            set_account_error(account["id"], safe_error(error))
        finally:
            release_collection(account["id"])

    return f"Checked {completed} account(s). {errors} account(s) need attention; see their channel pages."


def daily_collection_loop(stopped):
    """
    Keep checking for uncollected statistics while the dashboard runs.

    Runs in a background thread started by app.py. Waits 30 seconds after
    start-up, then checks every 15 minutes. Nothing is ever posted here.

    Args:
        stopped (threading.Event): Set when the dashboard shuts down.

    Returns:
        Nothing.
    """
    # stopped.wait(n) pauses up to n seconds; it returns True early if the
    # dashboard is shutting down, which ends the loop.
    if stopped.wait(FIRST_CHECK_DELAY_SECONDS):
        return
    while True:
        try:
            sync_channel()
        except Exception:
            # A temporary database problem must not crash the web app.
            pass
        if stopped.wait(CHECK_EVERY_SECONDS):
            return


def main():
    """Collect statistics from the command line (see the top of this file)."""
    parser = argparse.ArgumentParser(description="Collect daily connected-account statistics")
    parser.add_argument("--channel", help="only this channel, e.g. news")
    parser.add_argument("--dry-run", action="store_true", help="count accounts without contacting platforms")
    args = parser.parse_args()
    load_dotenv()
    create_tables()
    print(sync_channel(args.channel, args.dry_run))


if __name__ == "__main__":
    main()

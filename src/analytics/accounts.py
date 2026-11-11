# =============================================================
# Once-a-day account and video statistics from official APIs.
# Only confirmed public post IDs are queried. TikTok upload IDs
# identify inbox deliveries and must never be treated as video IDs.
# =============================================================

import argparse
import time

from dotenv import load_dotenv

from src.analytics.collector import _ensure_metrics_table, save_metrics
from src.dashboard.accounts import (TIKTOK_BASE, access_token, api_json, fetch_profile,
                                    graph_base, safe_error, save_snapshot)
from src.storage.database import create_tables, get_connection
from src.storage.finance import utc_now


def fetch_post_metrics(platform, post_id, token):
    """Return cumulative engagement for public post_id on platform using token."""
    if platform == "tiktok":
        response = api_json("POST", TIKTOK_BASE + "/video/query/", token,
                            params={"fields": "id,view_count,like_count,comment_count,share_count"},
                            json={"filters": {"video_ids": [post_id]}})
        videos = response.get("data", {}).get("videos", [])
        if not videos:
            raise ValueError("TikTok could not find this public video for the connected account.")
        video = videos[0]
        for field in ("view_count", "like_count", "comment_count", "share_count"):
            if field not in video:
                raise ValueError("TikTok did not return all requested video statistics.")
        return {"views": video["view_count"], "likes": video["like_count"],
                "comments": video["comment_count"], "shares": video["share_count"]}
    media = api_json("GET", graph_base() + f"/{post_id}", token,
                     params={"fields": "like_count,comments_count"})
    insights = api_json("GET", graph_base() + f"/{post_id}/insights", token,
                        params={"metric": "views,shares"})
    values = {}
    for item in insights.get("data", []):
        entries = item.get("values", [])
        if entries:
            values[item["name"]] = entries[-1]["value"]
    if "views" not in values or "shares" not in values:
        raise ValueError("Instagram insights are unavailable for this post. Check insights permission and try later.")
    return {"views": values["views"], "shares": values["shares"],
            "likes": media.get("like_count", 0), "comments": media.get("comments_count", 0)}


def sync_channel(channel=None, dry_run=False):
    """Collect today's statistics for channel or all channels; return a summary.

    dry_run reports eligible accounts without making API requests. Successful
    snapshots are skipped on repeated runs; errors remain visible per account.
    """
    _ensure_metrics_table()
    connection = get_connection()
    try:
        query = "SELECT * FROM accounts"
        values = []
        if channel:
            query += " WHERE channel = ?"
            values.append(channel)
        accounts = connection.execute(query, values).fetchall()
    finally:
        connection.close()
    if dry_run:
        return f"Dry run: {len(accounts)} connected account(s); no platform requests made."
    completed = 0
    errors = 0
    for row in accounts:
        account = dict(row)
        if not claim_collection(account["id"]):
            continue
        try:
            token = access_token(account)
            if not account["last_synced_at"] or account["last_synced_at"][:10] != utc_now()[:10]:
                profile = fetch_profile(account["platform"], token, account["platform_account_id"])
                connection = get_connection()
                try:
                    save_snapshot(connection, account["id"], profile["followers"])
                    connection.commit()
                finally:
                    connection.close()
            connection = get_connection()
            try:
                posts = connection.execute("""
                    SELECT p.* FROM platform_posts p JOIN videos v ON v.id=p.video_id
                    WHERE v.channel=? AND p.platform=? AND NOT EXISTS (
                        SELECT 1 FROM metrics m WHERE m.video_id=p.video_id AND m.platform=p.platform
                        AND substr(m.collected_at, 1, 10)=?
                    )
                """, (account["channel"], account["platform"], utc_now()[:10])).fetchall()
            finally:
                connection.close()
            for post in posts:
                metrics = fetch_post_metrics(account["platform"], post["post_id"], token)
                save_metrics(post["video_id"], account["platform"], platform_post_id=post["post_id"], **metrics)
            connection = get_connection()
            try:
                connection.execute("UPDATE accounts SET last_error='' WHERE id=?", (account["id"],))
                connection.commit()
            finally:
                connection.close()
            completed += 1
        except Exception as error:
            errors += 1
            connection = get_connection()
            try:
                connection.execute("UPDATE accounts SET last_error=? WHERE id=?", (safe_error(error), account["id"]))
                connection.commit()
            finally:
                connection.close()
        finally:
            connection = get_connection()
            try:
                connection.execute("DELETE FROM collection_locks WHERE account_id=?", (account["id"],))
                connection.commit()
            finally:
                connection.close()
    return f"Checked {completed} account(s). {errors} account(s) need attention; see their channel pages."


def claim_collection(account_id):
    """Reserve account_id's collection for ten minutes; return whether acquired.

    This prevents a dashboard refresh and daily collector writing the same day's
    metrics at once. An expired lock allows recovery after a process stops.
    """
    connection = get_connection()
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("DELETE FROM collection_locks WHERE expires_at < ?", (time.time(),))
        cursor = connection.execute("INSERT OR IGNORE INTO collection_locks VALUES (?, ?)", (account_id, time.time() + 600))
        connection.commit()
        return cursor.rowcount == 1
    finally:
        connection.close()


def daily_collection_loop(stopped):
    """Check for missing daily snapshots every fifteen minutes until stopped.

    stopped is a threading.Event set when the dashboard shuts down. Per-account
    errors are shown on channel pages; no public posting occurs in this loop.
    """
    while not stopped.wait(30):
        try:
            sync_channel()
        except Exception:
            # A temporary database outage must not stop the web application.
            pass
        if stopped.wait(15 * 60):
            return


def main():
    """Read optional --channel/--dry-run arguments and collect daily statistics."""
    parser = argparse.ArgumentParser(description="Collect daily connected-account statistics")
    parser.add_argument("--channel")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    load_dotenv()
    create_tables()
    print(sync_channel(args.channel, args.dry_run))


if __name__ == "__main__":
    main()

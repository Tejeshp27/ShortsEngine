# =============================================================
# Verify accepted clip uploads through official platform status APIs.
# Upload acceptance, TikTok inbox delivery and public posts are distinct.
# Polling never retries a file upload or clears duplicate reservations.
# =============================================================

import json
import time

import httpx

from src.storage.database import get_connection


def create_table():
    """Add a persistent upload-verification table if needed; return nothing and retain all delivery history."""
    connection = get_connection()
    try:
        connection.execute("""CREATE TABLE IF NOT EXISTS publishing_checks (
            video_id INTEGER NOT NULL REFERENCES videos(id), platform TEXT NOT NULL,
            checked_at REAL NOT NULL, status TEXT NOT NULL, public_url TEXT NOT NULL DEFAULT '',
            details TEXT NOT NULL DEFAULT '{}', PRIMARY KEY(video_id, platform))""")
        connection.commit()
    finally:
        connection.close()


def fetch_status(platform, post_id, token):
    """
    Query one official upload status using its platform, ID and private access token.

    Args:
        platform: youtube or tiktok.
        post_id: YouTube video ID or TikTok upload publish_id.
        token: A valid account token; it is never logged or returned.

    Returns:
        State, actual public URL when confirmed, and safe platform details. Processing is not public success.
    """
    headers = {"Authorization": "Bearer " + token}
    if platform == "youtube":
        response = httpx.get("https://www.googleapis.com/youtube/v3/videos",
                             params={"part":"status", "id":post_id}, headers=headers, timeout=30)
        response.raise_for_status()
        items = response.json().get("items", [])
        if not items:
            return {"status":"not_found", "public_url":"", "details":{}}
        status = items[0].get("status", {})
        public = status.get("uploadStatus") == "processed" and status.get("privacyStatus") == "public"
        url = "https://www.youtube.com/shorts/" + post_id if public else ""
        return {"status":"public" if public else status.get("uploadStatus", "unknown"),
                "public_url":url, "details":status}
    if platform != "tiktok":
        raise ValueError("Unsupported upload status platform.")
    response = httpx.post("https://open.tiktokapis.com/v2/post/publish/status/fetch/",
                          json={"publish_id":post_id}, headers=headers, timeout=30)
    response.raise_for_status()
    answer = response.json()
    if answer.get("error", {}).get("code", "ok") != "ok":
        raise ValueError("TikTok could not verify this upload; its reservation is retained.")
    data = answer.get("data", {})
    state = data.get("status", "unknown")
    url = ""
    ids = data.get("publicaly_available_post_id", [])
    if state == "PUBLISH_COMPLETE" and ids:
        # Fetch the real share URL rather than inventing a username from a display name.
        shared = httpx.post("https://open.tiktokapis.com/v2/video/query/",
                            params={"fields":"id,share_url"},
                            json={"filters":{"video_ids":[str(ids[0])]}}, headers=headers, timeout=30)
        shared.raise_for_status()
        videos = shared.json().get("data", {}).get("videos", [])
        if videos:
            url = videos[0].get("share_url", "")
    return {"status":"public" if url else state, "public_url":url,
            "details":{"fail_reason":data.get("fail_reason", ""), "public_post_ids":ids}}


def verify_deliveries():
    """Check at most ten due clip deliveries every five minutes each; return verified rows without re-uploading anything."""
    from src.dashboard.accounts import access_token, get_account, safe_error

    create_table()
    connection = get_connection()
    try:
        rows = connection.execute("""
            SELECT d.*, v.channel FROM publishing_deliveries d
            JOIN videos v ON v.id=d.video_id JOIN clips c ON c.video_id=v.id
            LEFT JOIN publishing_checks p ON p.video_id=d.video_id AND p.platform=d.platform
            WHERE d.status='succeeded' AND d.post_id IS NOT NULL AND d.platform IN ('youtube','tiktok')
              AND (p.checked_at IS NULL OR (p.checked_at < ? AND p.status <> 'public'))
            ORDER BY COALESCE(p.checked_at,0) LIMIT 10
        """, (time.time()-300,)).fetchall()
    finally:
        connection.close()
    results = []
    for row in rows:
        try:
            account = get_account(row["channel"], row["platform"])
            if account is None:
                raise ValueError("Reconnect this clip channel's account to verify the upload.")
            result = fetch_status(row["platform"], row["post_id"], access_token(account))
        except Exception as error:
            result = {"status":"verification_pending", "public_url":"", "details":{"error":safe_error(error)}}
        connection = get_connection()
        try:
            connection.execute("""INSERT INTO publishing_checks VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(video_id,platform) DO UPDATE SET checked_at=excluded.checked_at,
                status=excluded.status,public_url=excluded.public_url,details=excluded.details""",
                (row["video_id"], row["platform"], time.time(), result["status"],
                 result["public_url"], json.dumps(result["details"])))
            connection.commit()
        finally:
            connection.close()
        if result["status"] == "public":
            from src.publishing.scheduler import record_public_post
            public_id = row["post_id"]
            if row["platform"] == "tiktok":
                public_id = str(result["details"]["public_post_ids"][0])
            record_public_post(row["video_id"], row["platform"], public_id)
        results.append({"video_id":row["video_id"], "platform":row["platform"], **result})
    return results

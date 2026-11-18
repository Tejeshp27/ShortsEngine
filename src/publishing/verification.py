# =============================================================
# Verify accepted clip uploads through official platform status APIs.
# Upload acceptance, TikTok inbox delivery and public posts are distinct.
# Polling never retries a file upload or clears duplicate reservations.
# =============================================================

import json
import time

import httpx

from src.storage.database import get_connection

# How long to wait before asking a platform about the same upload again.
# A video still being processed is checked often; a video that only
# changes when a person acts (making it public, posting from the TikTok
# inbox) is checked rarely so it cannot use up the daily API quota.
CHECK_AGAIN_SOON_SECONDS = 5 * 60
CHECK_AGAIN_LATER_SECONDS = 6 * 60 * 60
MAXIMUM_CHECKS_PER_RUN = 10

# Answers that never change on their own: stop asking about these uploads.
# (YouTube uses lowercase words; TikTok uses capitals.)
FINISHED_STATUSES = {"failed", "rejected", "deleted", "not_found", "FAILED"}

# Answers that only change after a person does something on the platform:
# a processed YouTube video that is still private, a TikTok video waiting in
# the inbox, or a TikTok post that finished but is not public.
WAITING_FOR_PERSON_STATUSES = {"processed", "SEND_TO_USER_INBOX", "PUBLISH_COMPLETE"}


def is_due_for_check(last_status, last_checked_at, now):
    """
    Decide whether an upload should be checked again on this run.

    Args:
        last_status: The answer saved from the previous check, or None if never checked.
        last_checked_at: When that check happened (seconds since 1970), or None.
        now: The current time in the same units.

    Returns:
        True if the platform should be asked again now, otherwise False.

    Example:
        is_due_for_check(None, None, 1000)            # → True (never checked)
        is_due_for_check("failed", 0, 10 ** 9)        # → False (will never change)
    """
    if last_checked_at is None:
        return True
    if last_status == "public" or last_status in FINISHED_STATUSES:
        return False
    waited = now - last_checked_at
    if last_status in WAITING_FOR_PERSON_STATUSES:
        return waited >= CHECK_AGAIN_LATER_SECONDS
    return waited >= CHECK_AGAIN_SOON_SECONDS


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
    """
    Ask YouTube/TikTok about accepted clip uploads that are due a check; never re-upload anything.

    Returns:
        A list with one result per upload checked on this run (at most ten).
        How often each upload is checked depends on its last answer; see is_due_for_check.
    """
    from src.dashboard.accounts import access_token, get_account, safe_error

    create_table()
    connection = get_connection()
    try:
        candidates = connection.execute("""
            SELECT d.*, v.channel, p.status AS last_status, p.checked_at AS last_checked_at
            FROM publishing_deliveries d
            JOIN videos v ON v.id=d.video_id JOIN clips c ON c.video_id=v.id
            LEFT JOIN publishing_checks p ON p.video_id=d.video_id AND p.platform=d.platform
            WHERE d.status='succeeded' AND d.post_id IS NOT NULL AND d.platform IN ('youtube','tiktok')
              AND (p.status IS NULL OR p.status <> 'public')
            ORDER BY COALESCE(p.checked_at,0)
        """).fetchall()
    finally:
        connection.close()
    # Oldest checks come first, so every upload gets its turn.
    now = time.time()
    rows = []
    for candidate in candidates:
        if len(rows) >= MAXIMUM_CHECKS_PER_RUN:
            break
        if is_due_for_check(candidate["last_status"], candidate["last_checked_at"], now):
            rows.append(candidate)
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

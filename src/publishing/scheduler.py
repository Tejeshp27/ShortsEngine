# =============================================================
# src/publishing/scheduler.py — post approved videos on schedule
#
# Reads each channel's posting schedule from its YAML config,
# checks whether it's time to post, and calls the right publisher
# (Instagram or TikTok). Run once per hour from a cron job or
# the Docker entrypoint.
#
# To activate, add to channels/news.yaml:
#   schedule:
#     timezone: "America/New_York"
#     post_times: ["09:00", "17:00"]   # post at 9am and 5pm
#     platforms: [tiktok, instagram]
#
# Safety rules:
#   - Never posts the same video twice (checks "posted" status in DB).
#   - Only posts if the current time is within 30 minutes of a schedule slot.
#   - If no approved video is ready, it skips quietly.
# =============================================================

import os
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

import yaml

from src.storage.database import create_tables, get_connection
from src.storage.queries import get_videos_by_status, get_video_by_id, mark_posted


def _load_all_channels():
    """
    Load every YAML file from the channels/ folder.

    Returns:
        list[tuple[str, dict]]: List of (channel_key, config_dict) pairs.
    """
    channels = []
    folder = "channels"
    if not os.path.isdir(folder):
        return channels
    for filename in os.listdir(folder):
        if filename.endswith(".yaml") or filename.endswith(".yml"):
            key = filename.rsplit(".", 1)[0]
            path = os.path.join(folder, filename)
            with open(path, "r", encoding="utf-8") as f:
                config = yaml.safe_load(f)
            channels.append((key, config))
    return channels


def _is_posting_time(schedule, now_local_hour, now_local_minute, window_minutes=30):
    """
    Check whether the current time falls within any scheduled posting slot.

    Args:
        schedule (dict): The 'schedule' block from the channel config.
        now_local_hour (int): Current hour in the channel's timezone (0-23).
        now_local_minute (int): Current minute (0-59).
        window_minutes (int): How many minutes before/after the target to allow.

    Returns:
        bool: True if it's time to post.
    """
    post_times = schedule.get("post_times", [])
    for time_str in post_times:
        # Time is stored as "HH:MM" in the config.
        parts = time_str.split(":")
        target_hour = int(parts[0])
        target_minute = int(parts[1]) if len(parts) > 1 else 0

        # Convert both times to minutes since midnight for easy comparison.
        current_minutes = now_local_hour * 60 + now_local_minute
        target_minutes = target_hour * 60 + target_minute

        if abs(current_minutes - target_minutes) <= window_minutes:
            return True
    return False


def find_posting_slot(schedule, now_utc):
    """Return the nearest scheduled UTC time as text, or None outside its window.

    schedule contains a named timezone and post_times. now_utc is an aware
    datetime. Adjacent dates are checked so windows work across midnight.
    """
    if schedule.get("timezone"):
        local_time = now_utc.astimezone(ZoneInfo(schedule["timezone"]))
    else:
        local_time = now_utc.astimezone(timezone(
            timedelta(hours=schedule.get("utc_offset_hours", 0))))
    nearest = None
    nearest_distance = float("inf")
    for day_offset in (-1, 0, 1):
        day = local_time + timedelta(days=day_offset)
        for time_text in schedule.get("post_times", []):
            parts = time_text.split(":")
            hour = int(parts[0])
            minute = int(parts[1]) if len(parts) > 1 else 0
            target = day.replace(hour=hour, minute=minute, second=0, microsecond=0)
            target_utc = target.astimezone(timezone.utc)
            # Skip a nonexistent local time during the spring clock change.
            if target_utc.astimezone(local_time.tzinfo) != target:
                continue
            distance = abs((now_utc - target_utc).total_seconds())
            if distance <= 1800 and distance < nearest_distance:
                nearest = target_utc.isoformat()
                nearest_distance = distance
    return nearest


def select_slot_video(channel, slot):
    """Return the video reserved for channel and slot, or reserve a ready one.

    The database lock makes simultaneous scheduler runs choose the same video.
    Returns a video row, or None when no approved rendered video is available.
    """
    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT video_id FROM publishing_slots WHERE channel = ? AND slot = ?",
            (channel, slot),
        ).fetchone()
        if row:
            return get_video_by_id(row["video_id"])
        for video in get_videos_by_status("approved", channel=channel):
            if video["video_path"] and os.path.isfile(video["video_path"]):
                conn.execute("INSERT INTO publishing_slots VALUES (?, ?, ?)",
                             (channel, slot, video["id"]))
                conn.commit()
                return video
        return None
    finally:
        conn.close()


def claim_delivery(video_id, platform):
    """Reserve an upload for a video and platform; return whether this run owns it.

    Successful or uncertain uploads are never sent again automatically. A crash
    or network error can happen after acceptance, so uncertain sends need review.
    """
    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        ready = conn.execute("""
            SELECT id FROM videos WHERE id=? AND status='approved'
            AND NOT EXISTS (SELECT 1 FROM jobs WHERE video_id=videos.id AND status IN ('queued', 'running'))
            AND NOT EXISTS (SELECT 1 FROM platform_posts WHERE video_id=videos.id AND platform=?)
        """, (video_id, platform)).fetchone()
        if not ready:
            return False
        cursor = conn.execute(
            "INSERT OR IGNORE INTO publishing_deliveries VALUES (?, ?, 'sending', NULL)",
            (video_id, platform),
        )
        conn.commit()
        return cursor.rowcount == 1
    finally:
        conn.close()


def finish_delivery(video_id, platform, status, post_id=None):
    """Save an upload outcome and optional returned ID; return nothing.

    video_id identifies the video, platform identifies its destination, and
    status is succeeded or uncertain. Uncertain uploads require manual checking.
    """
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE publishing_deliveries SET status = ?, post_id = ? "
            "WHERE video_id = ? AND platform = ?",
            (status, post_id, video_id, platform),
        )
        conn.commit()
    finally:
        conn.close()


def run_scheduler(dry_run=False):
    """Upload one approved video per channel slot; dry_run only prints the plan.

    Completed destinations are skipped on later runs. A video is marked posted
    only after all configured destinations succeed (TikTok success means inbox).
    """
    if not dry_run:
        create_tables()
    now_utc = datetime.now(timezone.utc)
    for channel_key, config in _load_all_channels():
        schedule = config.get("schedule")
        if not schedule:
            continue
        slot = find_posting_slot(schedule, now_utc)
        if slot is None:
            continue
        platforms = list(dict.fromkeys(schedule.get("platforms", config.get("platforms", []))))
        supported = bool(platforms)
        for platform in platforms:
            if platform not in ("tiktok", "instagram"):
                supported = False
        if not supported:
            print(f"[{channel_key}] Configure supported posting platforms first.")
            continue
        if dry_run:
            print(f"[{channel_key}] [DRY RUN] Would check slot {slot}: {platforms}")
            continue
        video = select_slot_video(channel_key, slot)
        if video is None or video["status"] != "approved":
            continue
        for platform in platforms:
            # Missing credentials and the unfinished Instagram publisher must not
            # consume an upload reservation: these are safe to retry later.
            if platform == "instagram":
                print(f"[{channel_key}] Instagram publishing is not implemented; leaving approved.")
                continue
            from src.dashboard.accounts import access_token, get_account, safe_error
            account = get_account(channel_key, platform)
            token = None
            if account:
                try:
                    token = access_token(account)
                except ValueError as error:
                    print(f"[{channel_key}] {safe_error(error)}")
                    continue
            elif config.get("publishing", {}).get("use_legacy_env_tokens", False):
                # Legacy globals are only used when this particular channel
                # explicitly opts in. Otherwise two channels could share a token.
                token = os.getenv("TIKTOK_ACCESS_TOKEN")
            if not token:
                print(f"[{channel_key}] Connect this channel's TikTok account first; leaving approved.")
                continue
            if not claim_delivery(video["id"], platform):
                continue
            try:
                from src.publishing.tiktok import upload_to_drafts, build_caption
                caption = build_caption(video["story_title"], video["story_url"], config)
                post_id = upload_to_drafts(video["video_path"], caption, access_token=token)
                finish_delivery(video["id"], platform, "succeeded", post_id)
            except Exception as error:
                finish_delivery(video["id"], platform, "uncertain")
                print(f"[{channel_key}] Upload needs manual checking before retry: {error}")
        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT platform, status FROM publishing_deliveries WHERE video_id = ? "
                "UNION ALL SELECT platform, 'succeeded' FROM platform_posts WHERE video_id = ?",
                (video["id"], video["id"]),
            ).fetchall()
        finally:
            conn.close()
        successful = set()
        for row in rows:
            if row["status"] == "succeeded":
                successful.add(row["platform"])
        all_succeeded = True
        for platform in platforms:
            if platform not in successful:
                all_succeeded = False
        if all_succeeded:
            mark_posted(video["id"])

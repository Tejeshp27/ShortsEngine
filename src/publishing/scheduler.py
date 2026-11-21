# =============================================================
# src/publishing/scheduler.py — post approved videos
#
# The one place that sends approved videos to the platforms
# (TikTok inbox, Instagram Reels, Facebook Page Reels, YouTube
# Shorts). Two things
# start a post:
#   1. The dashboard's "Post now" button → publish_video_now()
#   2. A channel's posting schedule → run_scheduler(). The dashboard
#      checks it every 5 minutes while running; it can also be run
#      from cron:  python -m src.publishing.scheduler [--dry-run]
#
# To turn on scheduled posting, add to channels/<channel>.yaml:
#   schedule:
#     timezone: "America/New_York"
#     post_times: ["09:00", "17:00"]   # post at 9am and 5pm
#
# Safety rules:
#   - Only approved videos are ever posted (human review first).
#   - Each video/platform is reserved before upload, so it is never
#     sent twice. A failure that left nothing public is released for a
#     retry; any other failure is "uncertain" and needs a manual check.
#   - Scheduled posts only happen within 30 minutes of a slot, one
#     video per slot. If no approved video is ready, it skips quietly.
# =============================================================

import os
import json
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

import yaml

from src.storage.database import create_tables, get_connection
from src.storage.queries import get_videos_by_status, get_video_by_id, mark_posted

# Platforms this program can post to.
SUPPORTED_PLATFORMS = ("tiktok", "instagram", "facebook", "youtube")

# How each platform's name is written in messages.
PLATFORM_NAMES = {"tiktok": "TikTok", "instagram": "Instagram",
                  "facebook": "Facebook", "youtube": "YouTube"}

# Platforms whose public posts the daily statistics collector can read.
# A public post on one of these is saved in platform_posts automatically.
PLATFORMS_WITH_STATISTICS = ("instagram", "youtube")

# The dashboard's background schedule check: wait a minute after start-up,
# then check every 5 minutes (well inside the 30-minute posting window).
FIRST_CHECK_DELAY_SECONDS = 60
SCHEDULE_CHECK_SECONDS = 300


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
        clip = conn.execute("SELECT * FROM clips WHERE video_id=?", (video_id,)).fetchone()
        if clip:
            from src.clips.settings import find_approved_streamer, load_clip_settings
            from src.dashboard.settings import channel_config
            config = channel_config(clip["channel"])
            settings = load_clip_settings(config)
            if not config.get("enabled", True):
                return False
            find_approved_streamer(settings, clip["streamer"], clip["channel"], clip["platform"])
            quality = json.loads(clip["quality_json"] or "{}")
            if quality and not quality.get("passed"):
                return False
            zone = ZoneInfo(config.get("schedule", {}).get("timezone", "UTC"))
            day = datetime.now(zone).date().isoformat()
            reserved = conn.execute("SELECT 1 FROM clip_publish_limits WHERE channel=? AND day=? AND video_id=?",
                                    (clip["channel"], day, video_id)).fetchone()
            count = conn.execute("SELECT COUNT(*) FROM clip_publish_limits WHERE channel=? AND day=?",
                                 (clip["channel"], day)).fetchone()[0]
            if not reserved and count >= settings.automation.max_uploads_per_day:
                return False
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
        if clip and cursor.rowcount == 1:
            conn.execute("INSERT OR IGNORE INTO clip_publish_limits VALUES (?, ?, ?)", (clip["channel"], day, video_id))
        conn.commit()
        return cursor.rowcount == 1
    finally:
        conn.close()


def delivery_block_reason(video_id, platform):
    """
    Explain in plain words why claim_delivery() refused an upload.

    claim_delivery() only answers yes/no (so its safety logic stays in one
    place); this reads the same facts again to tell the owner what to do.

    Args:
        video_id (int): The video.
        platform (str): "tiktok", "youtube", ...

    Returns:
        str: The reason, e.g. "daily upload limit reached (3 of 3 today) ...".
    """
    conn = get_connection()
    try:
        delivery = conn.execute("SELECT status FROM publishing_deliveries WHERE video_id=? AND platform=?",
                                (video_id, platform)).fetchone()
        if delivery is not None:
            return f"already {delivery['status']} on this platform (never sent twice)"
        if conn.execute("SELECT 1 FROM platform_posts WHERE video_id=? AND platform=?",
                        (video_id, platform)).fetchone():
            return "already posted on this platform"
        video = conn.execute("SELECT status FROM videos WHERE id=?", (video_id,)).fetchone()
        if video is None:
            return "the video no longer exists"
        if video["status"] != "approved":
            return f"the video is '{video['status']}', not approved"
        if conn.execute("SELECT 1 FROM jobs WHERE video_id=? AND status IN ('queued', 'running')",
                        (video_id,)).fetchone():
            return "another job is still working on this video; try again when it finishes"
        clip = conn.execute("SELECT * FROM clips WHERE video_id=?", (video_id,)).fetchone()
        if clip:
            from src.clips.settings import load_clip_settings
            from src.dashboard.settings import channel_config
            config = channel_config(clip["channel"])
            if not config.get("enabled", True):
                return "the clips channel is disabled in its YAML"
            quality = json.loads(clip["quality_json"] or "{}")
            if quality and not quality.get("passed"):
                return "the clip failed its quality check"
            settings = load_clip_settings(config)
            timezone_name = config.get("schedule", {}).get("timezone", "UTC")
            day = datetime.now(ZoneInfo(timezone_name)).date().isoformat()
            count = conn.execute("SELECT COUNT(*) FROM clip_publish_limits WHERE channel=? AND day=?",
                                 (clip["channel"], day)).fetchone()[0]
            limit = settings.automation.max_uploads_per_day
            if count >= limit:
                return (f"daily upload limit reached ({count} of {limit} videos today, {timezone_name}). "
                        "It resets at midnight, or raise 'Daily upload limit' in the clips page settings")
        return "not ready to send"
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


def release_delivery(video_id, platform):
    """Remove an upload reservation after a failure that left nothing public.

    Used only when a publisher raised NothingPublishedError, so the next
    attempt (button or schedule) is allowed to try that platform again.
    """
    conn = get_connection()
    try:
        conn.execute(
            "DELETE FROM publishing_deliveries WHERE video_id = ? AND platform = ? AND status = 'sending'",
            (video_id, platform),
        )
        conn.commit()
    finally:
        conn.close()


def record_public_post(video_id, platform, post_id):
    """Save a confirmed public post ID so the daily collector finds its views.

    Used for Instagram Reels and public YouTube uploads. (TikTok's inbox
    upload ID and a private YouTube upload are not public posts.)
    """
    conn = get_connection()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO platform_posts (video_id, platform, post_id, confirmed_at) "
            "VALUES (?, ?, ?, ?)",
            (video_id, platform, post_id, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
    finally:
        conn.close()


def channel_platforms(config):
    """Return the platforms a channel posts to, without repeats.

    The schedule block's own list wins; otherwise the channel's main
    `platforms` list is used. Example: ["tiktok", "instagram", "facebook"].
    """
    schedule = config.get("schedule") or {}
    listed = schedule.get("platforms", config.get("platforms", []))
    platforms = []
    for platform in listed:
        if platform not in platforms:
            platforms.append(platform)
    return platforms


def video_platforms(video, config):
    """
    Read channel defaults and an optional clip metadata override; return this video's configured destinations.

    Args:
        video: The finished video's database row, including its ID and channel.
        config: The channel settings loaded from YAML.

    Returns:
        The enabled destinations selected for this video.
    """
    from src.clips.store import get_clip_for_video
    platforms = channel_platforms(config)
    clip = get_clip_for_video(video["id"])
    if clip:
        metadata = json.loads(clip["metadata_json"] or "{}")
        selected = metadata.get("platforms")
        if isinstance(selected, list):
            result = []
            for platform in platforms:
                if platform in selected:
                    result.append(platform)
            return result
    return platforms


def unsupported_platforms(platforms):
    """Return the platforms in the list that this program cannot post to."""
    unsupported = []
    for platform in platforms:
        if platform not in SUPPORTED_PLATFORMS:
            unsupported.append(platform)
    return unsupported


def get_credentials(channel_key, config, platform):
    """Get the token (and account ID) a platform upload needs.

    Facebook has no connection of its own: it uses the Instagram
    connection, which manages the linked Facebook Page.

    Returns:
        dict: {"token": ..., "account_id": ...}

    Raises:
        ValueError: With a plain message when nothing usable is connected.
    """
    from src.dashboard.accounts import access_token, get_account

    connection_platform = platform
    if platform == "facebook":
        connection_platform = "instagram"

    account = get_account(channel_key, connection_platform)
    if account:
        # access_token() raises ValueError if the token has expired.
        return {"token": access_token(account), "account_id": account["platform_account_id"]}

    # Old global .env tokens are only used when this channel explicitly
    # opts in; otherwise two channels could end up sharing one account.
    # (YouTube never had one: it always uses the channel page connection.)
    uses_legacy_tokens = config.get("publishing", {}).get("use_legacy_env_tokens", False)
    if uses_legacy_tokens and connection_platform != "youtube":
        if connection_platform == "tiktok":
            token = os.getenv("TIKTOK_ACCESS_TOKEN")
            account_id = ""
        else:
            token = os.getenv("INSTAGRAM_ACCESS_TOKEN")
            account_id = os.getenv("INSTAGRAM_ACCOUNT_ID", "")
        if token and (connection_platform == "tiktok" or account_id):
            return {"token": token, "account_id": account_id}

    raise ValueError(f"Connect this channel's {PLATFORM_NAMES[connection_platform]} account first.")


def clip_caption(video, config):
    """Return the streamer-crediting caption for a clip video, or None.

    Videos from a clips channel (src/clips/) must credit and link the
    streamer; news videos return None and keep their normal caption.
    """
    from src.clips.metadata import publishing_caption
    from src.clips.store import get_clip_for_video
    clip = get_clip_for_video(video["id"])
    if clip is None:
        return None
    hashtags = config.get("publishing", {}).get("hashtags", [])
    return publishing_caption(clip, video["story_title"], hashtags)


def send_to_platform(platform, video, config, credentials):
    """Upload one video to one platform.

    TikTok lands in your TikTok inbox (you post it in the app). Instagram
    and Facebook Reels go public immediately. YouTube is public only if
    the channel asks for it and the returned privacy status is public.

    Returns:
        dict: {"id": the ID the platform sent back,
               "public": True if the post is publicly visible now}
    """
    if platform == "tiktok":
        # Drafts carry only the video; the caption is pasted in the app.
        from src.publishing import tiktok
        upload_id = tiktok.upload_to_drafts(video["video_path"], access_token=credentials["token"])
        return {"id": upload_id, "public": False}

    # A clip's caption must credit and link the streamer (see clips/maker.py).
    clip_text = clip_caption(video, config)

    if platform == "youtube":
        from src.publishing import youtube
        title = youtube.build_title(video["story_title"])
        if clip_text:
            # #Shorts helps YouTube file a vertical video under Shorts.
            description = clip_text if "#Shorts" in clip_text else clip_text + " #Shorts"
        else:
            description = youtube.build_description(video["story_title"], video["story_url"], config)
        return youtube.upload_short(video["video_path"], title, description, credentials["token"], config)

    from src.publishing import instagram
    caption = clip_text or instagram.build_caption(video["story_title"], video["story_url"], config)
    if platform == "instagram":
        media_id = instagram.post_reel(video["video_path"], caption,
                                       credentials["token"], credentials["account_id"])
        return {"id": media_id, "public": True}

    from src.publishing import facebook
    reel_id = facebook.post_page_reel(video["video_path"], caption,
                                      credentials["token"], credentials["account_id"])
    return {"id": reel_id, "public": True}


def publish_to_platform(channel_key, config, video, platform):
    """Post one approved video to one platform, safely; return a short message.

    The steps:
      1. Find the credentials. If none, stop without reserving anything.
      2. Reserve the upload (claim_delivery), so it is never sent twice.
      3. Upload. On success save the result. If nothing went public,
         release the reservation so it can be retried. Any other failure
         is "uncertain" and must be checked by hand before a retry.
    """
    from src.dashboard.accounts import safe_error
    from src.publishing.errors import NothingPublishedError

    name = PLATFORM_NAMES[platform]
    try:
        credentials = get_credentials(channel_key, config, platform)
    except ValueError as error:
        return f"{name}: {safe_error(error)} Video left approved."

    if not claim_delivery(video["id"], platform):
        return f"{name}: not sent: {delivery_block_reason(video['id'], platform)}."

    try:
        result = send_to_platform(platform, video, config, credentials)
    except NothingPublishedError as error:
        release_delivery(video["id"], platform)
        return f"{name}: failed before anything was published, safe to try again. {safe_error(error)}"
    except Exception as error:
        finish_delivery(video["id"], platform, "uncertain")
        return f"{name}: result unknown. Check {name} by hand before retrying. {safe_error(error)}"

    finish_delivery(video["id"], platform, "succeeded", result["id"])
    if result["public"] and platform in PLATFORMS_WITH_STATISTICS:
        record_public_post(video["id"], platform, result["id"])
    if platform == "youtube" and not result["public"]:
        return (f"YouTube: uploaded with non-public privacy (ID {result['id']}). Make it public in "
                "YouTube Studio, then record its post ID here to collect its views.")
    return f"{name}: done (ID {result['id']})."


def mark_posted_if_complete(video_id, platforms):
    """Mark a video posted once every platform in the list has succeeded.

    Returns:
        bool: True if the video is now fully posted.
    """
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT platform, status FROM publishing_deliveries WHERE video_id = ? "
            "UNION ALL SELECT platform, 'succeeded' FROM platform_posts WHERE video_id = ?",
            (video_id, video_id),
        ).fetchall()
    finally:
        conn.close()

    successful = set()
    for row in rows:
        if row["status"] == "succeeded":
            successful.add(row["platform"])
    for platform in platforms:
        if platform not in successful:
            return False
    mark_posted(video_id)
    return True


def publish_video(channel_key, config, video):
    """Post one approved video to all of its channel's platforms.

    Platforms that already succeeded are skipped automatically.

    Returns:
        tuple: (list of messages, one per platform; True if now fully posted)
    """
    platforms = video_platforms(video, config)
    messages = []
    if not platforms:
        return ["Select at least one destination for this video."], False
    for platform in platforms:
        messages.append(publish_to_platform(channel_key, config, video, platform))
    fully_posted = mark_posted_if_complete(video["id"], platforms)
    return messages, fully_posted


def publish_video_now(channel_key, config, video_id):
    """Post a specific approved video right away (the dashboard's "Post now" button).

    Returns:
        tuple: the same as publish_video().

    Raises:
        ValueError: If the video can't be posted (not approved, no file,
                    or the channel's platforms are not supported).
    """
    create_tables()
    video = get_video_by_id(video_id)
    if video is None or video["channel"] != channel_key:
        raise ValueError("Video not found for this channel.")
    if video["status"] != "approved":
        raise ValueError("Only approved videos can be posted.")
    if not video["video_path"] or not os.path.isfile(video["video_path"]):
        raise ValueError("Render this video before posting it.")

    platforms = video_platforms(video, config)
    if not platforms or unsupported_platforms(platforms):
        raise ValueError("Set this channel's platforms to tiktok, instagram, facebook and/or youtube.")
    return publish_video(channel_key, config, video)


def run_scheduler(dry_run=False, only_channel=None):
    """Post one approved video per channel time slot; dry_run only prints the plan.

    Only channels with a `schedule:` block take part. A video is marked
    posted only after every platform succeeds (TikTok success = inbox).
    """
    if not dry_run:
        create_tables()
    now_utc = datetime.now(timezone.utc)
    for channel_key, config in _load_all_channels():
        if only_channel and channel_key != only_channel:
            continue
        if not config.get("enabled", True):
            continue
        if config.get("type") == "clips" and config.get("acquisition", {}).get("only", False):
            continue
        if config.get("type") == "clips" and not dry_run:
            conn = get_connection()
            try:
                state = conn.execute("SELECT enabled FROM clip_automation WHERE channel=?", (channel_key,)).fetchone()
            finally:
                conn.close()
            enabled = config.get("automation", {}).get("enabled", False)
            if state:
                enabled = bool(state["enabled"])
            if not enabled:
                continue
        schedule = config.get("schedule")
        if not schedule:
            continue
        slot = find_posting_slot(schedule, now_utc)
        if slot is None:
            continue
        platforms = channel_platforms(config)
        if not platforms or unsupported_platforms(platforms):
            print(f"[{channel_key}] Configure supported posting platforms first.")
            continue
        clip_dry_run = config.get("type") == "clips" and config.get("automation", {}).get("publish_dry_run", True)
        if dry_run or clip_dry_run:
            print(f"[{channel_key}] [DRY RUN] Would check slot {slot}: {platforms}")
            continue
        video = select_slot_video(channel_key, slot)
        if video is None or video["status"] != "approved":
            continue
        messages, fully_posted = publish_video(channel_key, config, video)
        for message in messages:
            print(f"[{channel_key}] {message}")


def publishing_loop(stopped):
    """Check the posting schedule every few minutes while the dashboard runs.

    Runs in a background thread started by the dashboard (app.py). It
    does nothing unless a channel has a `schedule:` block.

    Args:
        stopped (threading.Event): Set when the dashboard shuts down.
    """
    # stopped.wait(n) pauses up to n seconds and returns True early when
    # the dashboard is shutting down, which ends the loop.
    if stopped.wait(FIRST_CHECK_DELAY_SECONDS):
        return
    while True:
        try:
            run_scheduler()
        except Exception as error:
            # One bad run (e.g. a locked database) must not stop the web app.
            print(f"Scheduled publishing check failed: {error}")
        if stopped.wait(SCHEDULE_CHECK_SECONDS):
            return


def main():
    """Run one schedule check from the command line (e.g. from cron)."""
    import argparse
    from dotenv import load_dotenv

    parser = argparse.ArgumentParser(description="Post approved videos whose time slot is now")
    parser.add_argument("--dry-run", action="store_true", help="print the plan; post nothing")
    arguments = parser.parse_args()
    load_dotenv()
    run_scheduler(dry_run=arguments.dry_run)


if __name__ == "__main__":
    main()

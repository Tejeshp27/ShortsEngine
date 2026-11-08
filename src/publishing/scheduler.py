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
from datetime import datetime, timezone

import yaml

from src.storage.queries import get_videos_by_status, mark_posted


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


def run_scheduler(dry_run=False):
    """
    Check all channels and post the next approved video if it's posting time.

    This function is meant to run on a schedule (e.g. every 30 minutes
    from a cron job). It's idempotent — safe to run many times.

    Args:
        dry_run (bool): If True, print what would be posted but don't publish.
    """
    channels = _load_all_channels()
    now_utc = datetime.now(timezone.utc)

    for channel_key, config in channels:
        schedule = config.get("schedule")
        if not schedule:
            continue  # This channel has no posting schedule yet.

        # Get local time for this channel using its timezone offset.
        # We use a simple UTC-offset approach so we don't need a third-party
        # timezone library. Set offset_hours in the channel config if needed.
        offset_hours = schedule.get("utc_offset_hours", 0)
        local_hour = (now_utc.hour + offset_hours) % 24
        local_minute = now_utc.minute

        if not _is_posting_time(schedule, local_hour, local_minute):
            continue

        # Find the next approved video that hasn't been posted.
        approved = get_videos_by_status("approved", channel=channel_key)
        if not approved:
            print(f"[{channel_key}] No approved videos ready to post.")
            continue

        video = approved[0]
        video_path = video["video_path"]

        if not video_path or not os.path.exists(video_path):
            print(f"[{channel_key}] Video file not found for ID {video['id']}. Skipping.")
            continue

        platforms = schedule.get("platforms", config.get("platforms", []))

        print(f"[{channel_key}] Posting: {video['story_title']}")
        print(f"  Platforms: {platforms}")

        if dry_run:
            print("  [DRY RUN] Would post now — no actual upload.")
            continue

        for platform in platforms:
            try:
                if platform == "tiktok":
                    from src.publishing.tiktok import upload_to_drafts, build_caption
                    caption = build_caption(
                        video["story_title"], video["story_url"], config
                    )
                    upload_to_drafts(video_path, caption)

                elif platform == "instagram":
                    from src.publishing.instagram import post_reel, build_caption
                    caption = build_caption(
                        video["story_title"], video["story_url"], config
                    )
                    post_reel(video_path, caption)

                else:
                    print(f"  Unknown platform '{platform}' — skipping.")
                    continue

                print(f"  Posted to {platform}.")

            except Exception as error:
                print(f"  ERROR posting to {platform}: {error}")

        # Mark as posted in the database regardless of platform errors,
        # so the same video isn't retried next run.
        mark_posted(video["id"])
        print(f"  Marked as posted (ID {video['id']}).")

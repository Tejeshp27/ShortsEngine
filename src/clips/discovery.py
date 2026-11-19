# =============================================================
# Public Twitch clip/VOD discovery for the existing source library.
# Only metadata is requested. Video files enter through the importer.
# Kick remains an explicit unsupported metadata-provider interface.
# =============================================================

import re
from datetime import datetime, timedelta, timezone

from src.clips.settings import approved_streamers_to_use, credit_for, load_clip_settings
from src.clips.store import save_discovered_source
from src.sources import kick, twitch


def vod_duration_seconds(value):
    """
    Convert Twitch's VOD duration text into seconds.

    Args:
        value: A duration such as 2h3m4s, 45m or 8s.

    Returns:
        Total seconds, or a ValueError for malformed duration text.
    """
    match = re.fullmatch(r"(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?", value or "")
    if not match or not value:
        raise ValueError("Twitch returned an invalid VOD duration.")
    hours, minutes, seconds = match.groups()
    return int(hours or 0) * 3600 + int(minutes or 0) * 60 + int(seconds or 0)


def source_details(item, source_type, streamer, user):
    """
    Normalize one public Twitch response for the existing source store.

    Args:
        item: A clip or archived VOD dictionary from Helix.
        source_type: clip or vod.
        streamer: The configured creator, used for stable attribution.
        user: The resolved Twitch user with ID and display name.

    Returns:
        Public metadata only; no file path or permission approval is supplied.
    """
    duration = item.get("duration", 0)
    if source_type == "vod":
        duration = vod_duration_seconds(duration)
    return {"platform": "twitch", "external_id": item["id"], "source_type": source_type,
            "streamer": streamer.username.lower(), "streamer_display_name": user.get("display_name", streamer.username),
            "clipper": item.get("creator_name", ""), "credit_text": credit_for(streamer),
            "clip_url": item.get("url", ""), "title": item.get("title", ""),
            "clip_created_at": item.get("created_at", ""), "duration_seconds": float(duration),
            "view_count": int(item.get("view_count", 0)), "thumbnail_url": item.get("thumbnail_url", ""),
            "game_id": item.get("game_id", ""), "vod_offset": item.get("vod_offset"),
            "source_video_id": item.get("video_id", "")}


def discover_sources(channel, config, only_streamer=None, dry_run=False):
    """
    Discover configured Twitch creators' public clips and VODs without acquiring footage.

    Args:
        channel: The existing source library's channel key.
        config: Channel YAML, including streamers and discovery request limits.
        only_streamer: Optional configured creator login for a manual run.
        dry_run: True returns a plan without network requests or database changes.

    Returns:
        New/updated counts, per-source failures, truncation warnings and unsupported Kick notices.
    """
    from src.dashboard.accounts import safe_error

    settings = load_clip_settings(config)
    streamers = approved_streamers_to_use(settings, only_streamer, channel)
    result = {"new": 0, "updated": 0, "errors": [], "warnings": [],
              "message": "Discovery only: metadata saved; no video files downloaded."}
    if dry_run:
        result["message"] = "Dry run: would discover Twitch clips and archived VODs. No network calls or changes."
        return result
    now = datetime.now(timezone.utc)
    started_at = (now - timedelta(hours=settings.discovery.hours_back)).strftime("%Y-%m-%dT%H:%M:%SZ")
    ended_at = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    for streamer in streamers:
        if streamer.platform == "kick":
            result["warnings"].append(kick.discover_sources(streamer.username)["message"])
            continue
        try:
            user = twitch.resolve_user(streamer.username)
        except (twitch.TwitchError, ValueError) as error:
            result["errors"].append(f"{streamer.username}: {safe_error(error)}")
            continue
        for source_type in ("clip", "vod"):
            try:
                if source_type == "clip":
                    page = twitch.discover_clips(user["id"], started_at, ended_at, settings.discovery.max_pages)
                else:
                    page = twitch.discover_videos(user["id"], settings.discovery.max_pages)
                for item in page["items"]:
                    created = save_discovered_source(channel, source_details(item, source_type, streamer, user))
                    key = "new" if created else "updated"
                    result[key] += 1
                if page["truncated"]:
                    result["warnings"].append(f"{streamer.username} {source_type}: page limit reached; results are partial.")
            except (twitch.TwitchError, ValueError) as error:
                result["errors"].append(f"{streamer.username} {source_type}: {safe_error(error)}")
    result["message"] = (f"Discovery only: {result['new']} new and {result['updated']} updated sources; "
                         f"{len(result['errors'])} source errors. No video files downloaded.")
    return result

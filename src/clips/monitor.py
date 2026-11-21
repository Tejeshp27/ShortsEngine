# =============================================================
# Poll official Twitch/Kick APIs and save visible monitoring states.
# Errors are shown as unknown, never as an invented offline state.
# =============================================================

import json

from src.clips.settings import load_clip_settings
from src.storage.database import get_connection
from src.storage.finance import utc_now


def check_live_sources(channel, config):
    """
    Check each enabled, approved streamer in channel settings; return statuses and persist safe errors independently.

    Args:
        channel: The channel configuration name, without its YAML extension.
        config: The channel settings loaded from YAML.

    Returns:
        Per-source statuses; the same results and safe errors are saved in SQLite.
    """
    from src.dashboard.accounts import safe_error
    from src.sources import kick, twitch

    statuses = []
    for streamer in load_clip_settings(config).streamers:
        if not streamer.enabled:
            continue
        try:
            if streamer.platform == "kick":
                details = kick.live_status(streamer.username)
            elif streamer.platform == "youtube":
                from src.clips.live_recorder import streamlink_is_live
                details = {"live": streamlink_is_live("youtube", streamer.username)}
            else:
                data = twitch.twitch_get("/streams", {"user_login": streamer.username}, twitch.get_app_token())
                streams = data.get("data", [])
                details = {"live": bool(streams)}
                if streams:
                    details.update({"title": streams[0].get("title", ""),
                                    "viewers": streams[0].get("viewer_count"),
                                    "started_at": streams[0].get("started_at", "")})
            state = "live" if details["live"] else "offline"
        except Exception as error:
            state = "unknown"
            details = {"error": safe_error(error)}
        connection = get_connection()
        try:
            connection.execute("""
                INSERT INTO stream_status VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(channel, platform, streamer) DO UPDATE SET
                    state=excluded.state, details=excluded.details, checked_at=excluded.checked_at
            """, (channel, streamer.platform, streamer.username.lower(), state, json.dumps(details), utc_now()))
            connection.commit()
        finally:
            connection.close()
        statuses.append({"streamer": streamer.username, "state": state, "details": details})
    return statuses

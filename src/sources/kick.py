# =============================================================
# Official Kick channel/live-status API. This module never obtains
# playback URLs or downloads VODs: creators supply recordings locally.
# =============================================================

import os
import time

import httpx

_app_token = {}


def discover_sources(username):
    """
    Describe the unsupported Kick clip/VOD discovery interface without making requests.

    Args:
        username: The configured Kick creator login.

    Returns:
        An empty item list and an explanation directing users to authorized manual imports.
    """
    return {"items": [], "message": f"{username}: Kick clip/VOD discovery is unavailable; upload authorized recordings."}


def get_app_token():
    """
    Read Kick app credentials from the environment; return a cached, unexpired app access token.

    Returns:
        An unexpired application access token.
    """
    client_id = os.getenv("KICK_CLIENT_ID", "")
    secret = os.getenv("KICK_CLIENT_SECRET", "")
    if not client_id or not secret:
        raise ValueError("Missing KICK_CLIENT_ID or KICK_CLIENT_SECRET in .env; local recordings still work.")
    saved = _app_token.get(client_id)
    if saved and saved["expires_at"] > time.time() + 60:
        return saved["token"]
    response = httpx.post("https://id.kick.com/oauth/token", data={
        "grant_type": "client_credentials", "client_id": client_id, "client_secret": secret,
    }, timeout=20)
    if response.status_code != 200:
        raise ValueError(f"Kick app login failed (HTTP {response.status_code}). Check your app credentials.")
    data = response.json()
    _app_token[client_id] = {"token": data["access_token"], "expires_at": time.time() + data["expires_in"]}
    return data["access_token"]


def live_status(username):
    """
    Look up a Kick slug through GET /public/v1/channels; return live state and public stream metadata.

    Args:
        username: The approved creator login name, without a site URL.

    Returns:
        Public live/offline state and available stream metadata.
    """
    response = httpx.get("https://api.kick.com/public/v1/channels", params={"slug": username},
                         headers={"Authorization": "Bearer " + get_app_token()}, timeout=20)
    if response.status_code != 200:
        raise ValueError(f"Kick channel check failed (HTTP {response.status_code}); retried on the next poll.")
    channels = response.json().get("data", [])
    if not channels:
        raise ValueError("Kick channel not found. Check the streamer name.")
    channel = channels[0]
    stream = channel.get("stream") or {}
    return {"live": bool(stream.get("is_live")), "title": channel.get("stream_title", ""),
            "viewers": stream.get("viewer_count"), "started_at": stream.get("start_time", "")}

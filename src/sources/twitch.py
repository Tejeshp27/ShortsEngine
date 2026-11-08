# =============================================================
# src/sources/twitch.py — fetch top clips from Twitch
#
# Phase 6.4: Second channel type
#
# This module talks to the Twitch Helix API to find the most-
# viewed clips for a given streamer or game. Those clips become
# the "source material" for the second channel type.
#
# IMPORTANT — usage rules:
#   - Only use clips from streamers who have given written permission.
#     Automated downloading of copyrighted broadcasts is not allowed.
#   - Short excerpts (clips) with commentary may qualify as fair use,
#     but always get permission first.
#
# Requirements (add to .env):
#   TWITCH_CLIENT_ID     — from dev.twitch.tv → Your App
#   TWITCH_CLIENT_SECRET — same app
#
# How it works:
#   1. get_app_token()   — authenticates with Twitch (client credentials)
#   2. fetch_clips()     — returns the top clips for a streamer/game
#   3. clips_to_stories() — converts them to the same dict format used
#                           by rss.py so the rest of the pipeline is unchanged
# =============================================================

import json
import os
import urllib.request
import urllib.parse
from datetime import datetime, timezone, timedelta


TWITCH_API_BASE = "https://api.twitch.tv/helix"


def get_app_token(client_id=None, client_secret=None):
    """
    Get a Twitch app access token using the client credentials flow.

    This token lets us read public data like clips and streams.
    It expires after about 60 days; in production you'd cache it.

    Args:
        client_id (str or None): Twitch client ID. Reads from TWITCH_CLIENT_ID if None.
        client_secret (str or None): Twitch client secret. Reads from TWITCH_CLIENT_SECRET.

    Returns:
        str: The access token.

    Raises:
        RuntimeError: If the credentials are missing or the request fails.
    """
    client_id = client_id or os.getenv("TWITCH_CLIENT_ID")
    client_secret = client_secret or os.getenv("TWITCH_CLIENT_SECRET")

    if not client_id or not client_secret:
        raise RuntimeError(
            "Missing TWITCH_CLIENT_ID or TWITCH_CLIENT_SECRET in .env.\n"
            "Get them from https://dev.twitch.tv/console/apps."
        )

    params = urllib.parse.urlencode({
        "client_id": client_id,
        "client_secret": client_secret,
        "grant_type": "client_credentials",
    }).encode("utf-8")

    request = urllib.request.Request(
        "https://id.twitch.tv/oauth2/token",
        data=params,
        method="POST",
    )

    with urllib.request.urlopen(request, timeout=15) as resp:
        data = json.loads(resp.read())

    if "access_token" not in data:
        raise RuntimeError(f"Twitch token request failed: {data}")

    return data["access_token"]


def _twitch_get(endpoint, params, access_token, client_id):
    """
    Make an authenticated GET request to the Twitch Helix API.

    Args:
        endpoint (str): API path, e.g. "/clips".
        params (dict): Query parameters.
        access_token (str): Bearer token.
        client_id (str): Your app's client ID (required by Twitch).

    Returns:
        dict: Parsed JSON response.
    """
    query = urllib.parse.urlencode(params)
    url = f"{TWITCH_API_BASE}{endpoint}?{query}"

    request = urllib.request.Request(url)
    request.add_header("Authorization", f"Bearer {access_token}")
    request.add_header("Client-Id", client_id)

    with urllib.request.urlopen(request, timeout=15) as resp:
        return json.loads(resp.read())


def get_broadcaster_id(username, access_token, client_id):
    """
    Look up a streamer's numeric ID from their Twitch username.

    Args:
        username (str): The streamer's login name, e.g. "shroud".
        access_token (str): Valid app access token.
        client_id (str): Your Twitch app's client ID.

    Returns:
        str or None: The broadcaster's numeric ID, or None if not found.

    Example:
        broadcaster_id = get_broadcaster_id("shroud", token, client_id)
    """
    data = _twitch_get(
        "/users",
        {"login": username},
        access_token,
        client_id,
    )
    users = data.get("data", [])
    if not users:
        return None
    return users[0]["id"]


def fetch_clips(broadcaster_username, hours_back=24, max_clips=10,
                access_token=None, client_id=None):
    """
    Fetch the most-viewed clips from a streamer's channel.

    Args:
        broadcaster_username (str): The streamer's Twitch login name.
        hours_back (int): Only return clips created in this window.
        max_clips (int): Maximum number of clips to return.
        access_token (str or None): Token from get_app_token(). Auto-fetched if None.
        client_id (str or None): Reads from TWITCH_CLIENT_ID if None.

    Returns:
        list[dict]: Raw clip objects from the Twitch API, sorted by view count.

    Raises:
        RuntimeError: If the broadcaster is not found or the API fails.
    """
    client_id = client_id or os.getenv("TWITCH_CLIENT_ID")

    if access_token is None:
        access_token = get_app_token(client_id=client_id)

    broadcaster_id = get_broadcaster_id(broadcaster_username, access_token, client_id)
    if not broadcaster_id:
        raise RuntimeError(f"Twitch broadcaster not found: '{broadcaster_username}'")

    # Twitch wants ISO 8601 timestamps for the time window filter.
    since = (datetime.now(timezone.utc) - timedelta(hours=hours_back)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )

    data = _twitch_get(
        "/clips",
        {
            "broadcaster_id": broadcaster_id,
            "first": max_clips,
            "started_at": since,
        },
        access_token,
        client_id,
    )
    return data.get("data", [])


def clips_to_stories(clips, broadcaster_username):
    """
    Convert Twitch clip objects into the story dict format used by the pipeline.

    The same format as rss.py so ranking.py and writer.py work unchanged.

    Args:
        clips (list[dict]): Raw clip dicts from fetch_clips().
        broadcaster_username (str): Used as the "source" field.

    Returns:
        list[dict]: Stories, each with title, url, source, published, summary.

    Example:
        stories = clips_to_stories(clips, "shroud")
        # stories[0]["title"] -> "Shroud clips shroud - Amazing Clip"
    """
    stories = []
    for clip in clips:
        story = {
            "title": clip.get("title", "Untitled clip"),
            "url": clip.get("url", ""),
            "source": f"Twitch/{broadcaster_username}",
            "published": clip.get("created_at", ""),
            # The summary gives the AI context: views, game, duration.
            "summary": (
                f"Clip by {clip.get('creator_name', broadcaster_username)}. "
                f"Game: {clip.get('game_id', 'unknown')}. "
                f"Duration: {clip.get('duration', 0):.0f}s. "
                f"Views: {clip.get('view_count', 0):,}."
            ),
        }
        stories.append(story)
    return stories


def fetch_twitch_stories(source_config):
    """
    Main entry point: fetch stories from a Twitch source config block.

    This matches the interface of rss.py's fetch_all_stories() so
    main.py can call either without knowing which source type it is.

    Args:
        source_config (dict): One entry from channels/<name>.yaml sources list.
                              Must have "broadcaster" and optionally "hours_back".

    Returns:
        list[dict]: Stories in the standard pipeline format.

    Example config block in channels/twitch_example.yaml:
        sources:
          - type: twitch
            broadcaster: "shroud"
            hours_back: 24
            max_clips: 10
    """
    broadcaster = source_config.get("broadcaster")
    if not broadcaster:
        print("  WARNING: Twitch source is missing 'broadcaster' — skipping.")
        return []

    hours_back = source_config.get("hours_back", 24)
    max_clips = source_config.get("max_clips", 10)

    try:
        clips = fetch_clips(broadcaster, hours_back=hours_back, max_clips=max_clips)
        stories = clips_to_stories(clips, broadcaster)
        print(f"  Twitch/{broadcaster}: {len(stories)} clip(s).")
        return stories
    except RuntimeError as error:
        print(f"  WARNING: Twitch fetch failed for '{broadcaster}': {error}")
        return []

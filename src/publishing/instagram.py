# =============================================================
# src/publishing/instagram.py — post Reels via Instagram Graph API
#
# Uses the official Meta Graph API (v20+) to publish a video as
# an Instagram Reel. The process has two steps:
#
#   1. Create a media container — tell Instagram the video URL and
#      caption, and get back a container ID.
#   2. Publish the container — Instagram processes the video and
#      makes it live. This can take 30–60 seconds.
#
# Requirements (see PROMPTS.md Phase 4.1 for setup steps):
#   INSTAGRAM_ACCESS_TOKEN — long-lived User access token
#   INSTAGRAM_ACCOUNT_ID   — your IG Professional account ID
#
# The video file must be accessible at a public URL. For Phase 4
# we upload to a temporary file host (or a cloud bucket in Phase 7).
# =============================================================

import os
import time
import urllib.request
import urllib.parse
import json


GRAPH_API_BASE = "https://graph.facebook.com/v20.0"


def _graph_post(endpoint, params):
    """
    Make a POST request to the Meta Graph API and return the JSON response.

    Args:
        endpoint (str): API path, e.g. "/123456/media".
        params (dict): Form fields to send.

    Returns:
        dict: Parsed JSON response.

    Raises:
        RuntimeError: If the API returns an error.
    """
    url = GRAPH_API_BASE + endpoint
    data = urllib.parse.urlencode(params).encode("utf-8")
    request = urllib.request.Request(url, data=data, method="POST")
    request.add_header("Content-Type", "application/x-www-form-urlencoded")

    with urllib.request.urlopen(request, timeout=30) as response:
        result = json.loads(response.read())

    if "error" in result:
        raise RuntimeError(
            f"Instagram API error: {result['error']['message']} "
            f"(code {result['error']['code']})"
        )
    return result


def _graph_get(endpoint, params):
    """
    Make a GET request to the Meta Graph API and return the JSON response.

    Args:
        endpoint (str): API path.
        params (dict): Query string parameters.

    Returns:
        dict: Parsed JSON response.
    """
    query = urllib.parse.urlencode(params)
    url = f"{GRAPH_API_BASE}{endpoint}?{query}"
    with urllib.request.urlopen(url, timeout=30) as response:
        return json.loads(response.read())


def post_reel(video_path, caption, access_token=None, account_id=None):
    """
    Upload a video file and publish it as an Instagram Reel.

    Args:
        video_path (str): Local path to the .mp4 file.
        caption (str): Caption text (hashtags go here too).
        access_token (str or None): Instagram access token. If None,
                                    reads from the INSTAGRAM_ACCESS_TOKEN
                                    environment variable.
        account_id (str or None): IG account ID. If None, reads from
                                  INSTAGRAM_ACCOUNT_ID.

    Returns:
        str: The Instagram media ID of the published Reel.

    Raises:
        RuntimeError: If the token/ID is missing or the API call fails.

    Example:
        media_id = post_reel("output/videos/news/2026-11-07-story.mp4",
                             "Big news today! #news #politics")
    """
    token = access_token or os.getenv("INSTAGRAM_ACCESS_TOKEN")
    acct_id = account_id or os.getenv("INSTAGRAM_ACCOUNT_ID")

    if not token:
        raise RuntimeError(
            "Missing INSTAGRAM_ACCESS_TOKEN in .env. "
            "See PROMPTS.md Phase 4.1 for setup instructions."
        )
    if not acct_id:
        raise RuntimeError(
            "Missing INSTAGRAM_ACCOUNT_ID in .env. "
            "See PROMPTS.md Phase 4.1 for setup instructions."
        )

    # Instagram requires the video to be at a public URL.
    # For now we raise a clear error; Phase 7 will add cloud upload.
    raise NotImplementedError(
        "Instagram publishing requires the video to be at a public URL. "
        "This will be wired up in Phase 7 when cloud storage is added. "
        "For now, post manually using the Instagram app."
    )


def build_caption(story_title, story_url, channel_config):
    """
    Build the Instagram caption from the story details and channel config.

    Args:
        story_title (str): The story headline.
        story_url (str): The source article URL.
        channel_config (dict): The channel config (for tone and niche).

    Returns:
        str: A caption with hashtags, under Instagram's 2200-character limit.

    Example:
        caption = build_caption("Big story", "https://npr.org/...", config)
    """
    niche = channel_config.get("niche", "news")

    # Basic hashtags for a news channel.
    hashtags = "#news #politics #currentevents #explained #learnontiktok"

    caption = (
        f"{story_title}\n\n"
        f"Source: {story_url}\n\n"
        f"{hashtags}"
    )

    # Instagram allows 2200 characters; truncate the title if needed.
    if len(caption) > 2200:
        caption = caption[:2197] + "..."

    return caption

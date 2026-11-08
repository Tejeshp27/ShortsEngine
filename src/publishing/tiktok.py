# =============================================================
# src/publishing/tiktok.py — upload videos to TikTok drafts
#
# Uses the official TikTok Content Posting API to upload a video
# to your TikTok drafts/inbox. You then open the TikTok app and
# tap "Post" yourself — this avoids needing full Direct Post
# approval (which TikTok only grants to audited apps).
#
# The two upload methods TikTok supports:
#   - FILE_UPLOAD: send the video file directly (up to 4 GB)
#   - PULL_FROM_URL: give TikTok a public URL to fetch (simpler)
#
# We use FILE_UPLOAD so no public URL is needed.
#
# Requirements (see PROMPTS.md Phase 4.3 for setup steps):
#   TIKTOK_ACCESS_TOKEN — OAuth user access token
#   TIKTOK_CLIENT_KEY   — your app's client key (for refresh)
#   TIKTOK_CLIENT_SECRET
#
# TikTok access tokens expire after 24 hours; the refresh flow
# is handled by the OAuth helpers in src/dashboard/oauth.py.
# =============================================================

import json
import os
import urllib.request
import urllib.parse


TIKTOK_API_BASE = "https://open.tiktokapis.com/v2"


def _tiktok_post(endpoint, payload, access_token):
    """
    Make an authenticated POST request to the TikTok API.

    Args:
        endpoint (str): API path, e.g. "/post/publish/inbox/video/init/".
        payload (dict): JSON body.
        access_token (str): Bearer token.

    Returns:
        dict: Parsed JSON response.

    Raises:
        RuntimeError: If the API returns an error.
    """
    url = TIKTOK_API_BASE + endpoint
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, method="POST")
    request.add_header("Authorization", f"Bearer {access_token}")
    request.add_header("Content-Type", "application/json; charset=UTF-8")

    with urllib.request.urlopen(request, timeout=30) as response:
        result = json.loads(response.read())

    error = result.get("error", {})
    if error.get("code", "ok") != "ok":
        raise RuntimeError(
            f"TikTok API error: {error.get('message', 'unknown')} "
            f"(code {error.get('code', '?')})"
        )
    return result


def upload_to_drafts(video_path, caption, access_token=None):
    """
    Upload a video to TikTok drafts (inbox) so you can review and post manually.

    TikTok's "inbox" / draft mode is called "UPLOAD_TO_INBOX" in the API.
    The video lands in your TikTok notification inbox — open the app to post it.

    Args:
        video_path (str): Local path to the .mp4 file.
        caption (str): Caption/description for the video (max 2200 chars).
        access_token (str or None): TikTok OAuth token. If None, reads from
                                    TIKTOK_ACCESS_TOKEN env var.

    Returns:
        str: TikTok's publish_id for this upload.

    Raises:
        RuntimeError: If the token is missing or the API call fails.

    Example:
        publish_id = upload_to_drafts(
            "output/videos/news/2026-11-07-story.mp4",
            "Big news explained! #news",
        )
    """
    token = access_token or os.getenv("TIKTOK_ACCESS_TOKEN")

    if not token:
        raise RuntimeError(
            "Missing TIKTOK_ACCESS_TOKEN in .env. "
            "See PROMPTS.md Phase 4.3 for setup instructions."
        )

    if not os.path.exists(video_path):
        raise RuntimeError(f"Video file not found: {video_path}")

    video_size = os.path.getsize(video_path)

    # Step 1: Tell TikTok we're about to upload and get an upload URL.
    init_payload = {
        "post_info": {
            "title": caption[:150],       # TikTok uses "title" for the caption
            "privacy_level": "SELF_ONLY", # keeps it private until you post manually
            "disable_duet": False,
            "disable_comment": False,
            "disable_stitch": False,
        },
        "source_info": {
            "source": "FILE_UPLOAD",
            "video_size": video_size,
            "chunk_size": video_size,     # single chunk (up to 64 MB)
            "total_chunk_count": 1,
        },
    }

    init_response = _tiktok_post(
        "/post/publish/inbox/video/init/", init_payload, token
    )
    publish_id = init_response["data"]["publish_id"]
    upload_url = init_response["data"]["upload_url"]

    # Step 2: Upload the video bytes.
    with open(video_path, "rb") as f:
        video_bytes = f.read()

    upload_request = urllib.request.Request(
        upload_url,
        data=video_bytes,
        method="PUT",
    )
    upload_request.add_header("Content-Type", "video/mp4")
    upload_request.add_header("Content-Range", f"bytes 0-{video_size - 1}/{video_size}")

    with urllib.request.urlopen(upload_request, timeout=120) as resp:
        if resp.status not in (200, 201, 204):
            raise RuntimeError(f"TikTok video upload failed with HTTP {resp.status}")

    print(f"  Uploaded to TikTok inbox (publish_id: {publish_id})")
    print("  Open the TikTok app → inbox → tap the notification to review and post.")
    return publish_id


def build_caption(story_title, story_url, channel_config):
    """
    Build a TikTok caption from story details and channel config.

    TikTok captions support hashtags and are capped at 2200 characters.

    Args:
        story_title (str): The story headline.
        story_url (str): Source URL (for reference; TikTok may truncate it).
        channel_config (dict): Channel config dict.

    Returns:
        str: TikTok caption text.
    """
    hashtags = "#news #politics #currentevents #learnontiktok #fyp"
    caption = f"{story_title}\n\n{hashtags}"
    if len(caption) > 2200:
        caption = caption[:2197] + "..."
    return caption

# =============================================================
# src/publishing/tiktok.py — upload videos to TikTok drafts
#
# Uses the official TikTok Content Posting API to upload a video
# to your TikTok drafts/inbox. You then open the TikTok app and
# tap "Post" yourself — this avoids needing full Direct Post
# approval (which TikTok only grants to audited apps).
#
# The inbox upload carries ONLY the video file: TikTok's API has no
# caption field for it (you write the caption in the app). So
# build_caption() makes the text you paste; the dashboard shows it
# on each video's page.
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
import urllib.error
import urllib.parse

from src.publishing.errors import NothingPublishedError


TIKTOK_API_BASE = "https://open.tiktokapis.com/v2"


# Files up to this size are uploaded as a single chunk.
SINGLE_CHUNK_LIMIT = 64 * 1024 ** 2


def tiktok_error_details(error):
    """
    Read TikTok's own explanation from a failed request.

    Args:
        error (urllib.error.HTTPError): The failed request.

    Returns:
        str: For example " invalid_params: The chunk size is invalid", or "" if unreadable.
            Only TikTok's error code and message are kept (never tokens).
    """
    try:
        body = json.loads(error.read() or b"{}")
    except (ValueError, OSError):
        return ""
    details = body.get("error", {})
    code = str(details.get("code", ""))[:60]
    message = str(details.get("message", ""))[:200]
    if not code and not message:
        return ""
    return f" {code}: {message}"


def chunk_plan(video_size):
    """
    Given a file's size in bytes, return TikTok chunk size/count with trailing bytes merged into the final chunk.

    Args:
        video_size: The total video file size, in bytes.

    Returns:
        A pair containing the upload chunk size and total number of chunks.
    """
    if not 0 < video_size <= 4 * 1024 ** 3:
        raise NothingPublishedError("TikTok files must be nonempty and no larger than 4 GB.")
    # Up to 64 MiB, send the whole file as one chunk (chunk_size = file size),
    # the simplest case in TikTok's transfer guide.
    if video_size <= SINGLE_CHUNK_LIMIT:
        return video_size, 1
    # Larger files: 32 MiB chunks. The final chunk can be larger than the
    # others (up to 128 MiB), as TikTok's transfer guide requires.
    chunk_size = 32 * 1024 ** 2
    return chunk_size, video_size // chunk_size


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


def upload_to_drafts(video_path, access_token=None):
    """
    Upload a video to TikTok drafts (inbox) so you can review and post manually.

    TikTok's "inbox" / draft mode is called "UPLOAD_TO_INBOX" in the API.
    The video lands in your TikTok notification inbox — open the app to post it.
    The caption is written in the app (this endpoint has no caption field).

    Args:
        video_path (str): Local path to the .mp4 file.
        access_token (str or None): TikTok OAuth token. If None, reads from
                                    TIKTOK_ACCESS_TOKEN env var.

    Returns:
        str: TikTok's publish_id for this upload.

    Raises:
        RuntimeError: If the token is missing or the API call fails.

    Example:
        publish_id = upload_to_drafts("output/videos/news/2026-11-07-story.mp4")
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
    chunk_size, total_chunks = chunk_plan(video_size)

    # Step 1: Tell TikTok we're about to upload and get an upload URL.
    # The inbox endpoint accepts only "source_info" (how the file arrives);
    # caption and privacy are chosen by you in the TikTok app.
    init_payload = {
        "source_info": {
            "source": "FILE_UPLOAD",
            "video_size": video_size,
            "chunk_size": chunk_size,
            "total_chunk_count": total_chunks,
        },
    }

    try:
        init_response = _tiktok_post("/post/publish/inbox/video/init/", init_payload, token)
    except urllib.error.HTTPError as error:
        if 400 <= error.code < 500:
            raise NothingPublishedError(f"TikTok rejected upload initialization (HTTP {error.code})."
                                        + tiktok_error_details(error)) from error
        raise
    publish_id = init_response["data"]["publish_id"]
    upload_url = init_response["data"]["upload_url"]

    # Step 2: Upload the video bytes.
    with open(video_path, "rb") as video_file:
        offset = 0
        for index in range(total_chunks):
            size = chunk_size
            if index == total_chunks - 1:
                size = video_size - offset
            video_bytes = video_file.read(size)
            upload_request = urllib.request.Request(upload_url, data=video_bytes, method="PUT")
            upload_request.add_header("Content-Type", "video/mp4")
            upload_request.add_header("Content-Range", f"bytes {offset}-{offset + len(video_bytes) - 1}/{video_size}")
            with urllib.request.urlopen(upload_request, timeout=120) as resp:
                if resp.status not in (200, 201, 204, 206):
                    raise RuntimeError(f"TikTok video upload failed with HTTP {resp.status}")
            offset += len(video_bytes)

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
    hashtags = " ".join(channel_config.get("publishing", {}).get("hashtags", []))
    caption = f"{story_title}\n\n{hashtags}".strip()
    if len(caption) > 2200:
        caption = caption[:2197] + "..."
    return caption

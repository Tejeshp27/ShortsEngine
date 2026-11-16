# =============================================================
# src/publishing/instagram.py — post Reels via the Instagram Graph API
#
# Publishes a finished MP4 as a public Instagram Reel. The scheduler
# (scheduler.py) only ever calls this for videos you have approved.
#
# Meta's process has four steps:
#   1. Create a "container": tell Instagram a Reel is coming, with its
#      caption. We ask for a resumable upload, so no public URL is needed.
#   2. Upload the MP4 file straight from this computer.
#   3. Wait while Instagram processes the video (usually under a minute).
#   4. Publish the container. Only this step makes the Reel public.
#
# Steps 1–3 failing means nothing is public, so they raise
# NothingPublishedError and the scheduler may safely try again.
#
# Needs (from the channel's Instagram connection in the dashboard):
#   - an access token with instagram_content_publish permission
#   - the numeric Instagram professional account ID
# =============================================================

import os
import time

from src.publishing.errors import NothingPublishedError
from src.publishing.meta_api import graph_call, graph_version, upload_video_file

# How often, and for how long, to check whether Instagram has finished
# processing the video before giving up.
PROCESSING_CHECK_SECONDS = 5
PROCESSING_MAX_CHECKS = 60      # 60 × 5 seconds = 5 minutes

# Instagram's caption limit.
MAX_CAPTION_CHARS = 2200


def create_reel_container(account_id, caption, token):
    """
    Tell Instagram a Reel is coming and get back its container ID.

    Args:
        account_id (str): The Instagram professional account ID.
        caption (str): The Reel's caption.
        token (str): The access token.

    Returns:
        str: The container ID, used by the next steps.
    """
    data = graph_call("POST", f"/{account_id}/media", token, {
        "media_type": "REELS",
        "upload_type": "resumable",   # we will send the file ourselves
        "caption": caption,
        "share_to_feed": "true",      # also show the Reel on the profile grid
    })
    return data["id"]


def wait_until_processed(container_id, token):
    """
    Wait until Instagram has finished processing the uploaded video.

    Args:
        container_id (str): The container from create_reel_container().
        token (str): The access token.

    Returns:
        Nothing. Returns as soon as the video is ready.

    Raises:
        RuntimeError: If processing fails or takes longer than 5 minutes.
    """
    for check in range(PROCESSING_MAX_CHECKS):
        data = graph_call("GET", f"/{container_id}", token, {"fields": "status_code"})
        status = data.get("status_code")
        if status == "FINISHED":
            return
        if status in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"Instagram could not process the video (status {status}).")
        time.sleep(PROCESSING_CHECK_SECONDS)
    raise RuntimeError("Instagram took longer than 5 minutes to process the video.")


def post_reel(video_path, caption, token, account_id):
    """
    Upload a video file and publish it as a public Instagram Reel.

    Args:
        video_path (str): The .mp4 file on this computer.
        caption (str): Caption text (hashtags go here too).
        token (str): The Instagram connection's access token.
        account_id (str): The numeric Instagram professional account ID.

    Returns:
        str: The public Reel's media ID (used later to collect its views).

    Raises:
        NothingPublishedError: A step before publishing failed; nothing is public.
        RuntimeError / httpx.HTTPError: The final publish step failed. The
            Reel MAY be public, so check Instagram before trying again.

    Example:
        media_id = post_reel("output/videos/news/story.mp4", "Big news #news",
                             token, "17841400000000000")
    """
    if not os.path.isfile(video_path):
        raise NothingPublishedError(f"Video file not found: {video_path}")

    # Steps 1–3. Any failure here leaves nothing public.
    try:
        container_id = create_reel_container(account_id, caption, token)
        upload_url = f"https://rupload.facebook.com/ig-api-upload/{graph_version()}/{container_id}"
        upload_video_file(upload_url, video_path, token)
        wait_until_processed(container_id, token)
    except Exception as error:
        raise NothingPublishedError(f"Instagram upload failed before publishing: {error}") from error

    # Step 4. This makes the Reel public. Errors here are NOT wrapped,
    # because Meta may have published it even if the answer got lost.
    data = graph_call("POST", f"/{account_id}/media_publish", token, {"creation_id": container_id})
    return data["id"]


def build_caption(story_title, story_url, channel_config):
    """
    Build the Instagram (and Facebook) caption from the story and channel settings.

    Args:
        story_title (str): The story headline.
        story_url (str): The source article URL.
        channel_config (dict): The channel's settings (for its hashtags).

    Returns:
        str: A caption with the source and hashtags, at most 2,200 characters.

    Example:
        caption = build_caption("Big story", "https://npr.org/...", config)
    """
    hashtags = " ".join(channel_config.get("publishing", {}).get("hashtags", []))

    caption = (
        f"{story_title}\n\n"
        f"Source: {story_url}\n\n"
        f"{hashtags}"
    )

    if len(caption) > MAX_CAPTION_CHARS:
        caption = caption[:MAX_CAPTION_CHARS - 3] + "..."

    return caption

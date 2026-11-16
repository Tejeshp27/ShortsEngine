# =============================================================
# src/publishing/facebook.py — post Reels to a Facebook Page
#
# Publishes a finished MP4 as a public Reel on the Facebook Page that
# is linked to the channel's Instagram account. There is no separate
# Facebook connection: the Instagram connection (made through Facebook
# Login) is allowed to act for the Page when it has the
# pages_manage_posts permission.
#
# Steps:
#   1. Find the Page linked to the Instagram account, and that Page's
#      own access token (fetched each time, never stored).
#   2. "start": tell Facebook a Reel is coming; get an upload address.
#   3. Upload the MP4 file straight from this computer.
#   4. "finish": publish the Reel. Only this step makes it public.
#
# Steps 1–3 failing means nothing is public, so they raise
# NothingPublishedError and the scheduler may safely try again.
# =============================================================

import os

from src.publishing.errors import NothingPublishedError
from src.publishing.meta_api import graph_call, upload_video_file


def find_linked_page(user_token, instagram_account_id):
    """
    Find the Facebook Page connected to an Instagram professional account.

    Args:
        user_token (str): The Instagram connection's access token.
        instagram_account_id (str): The numeric Instagram account ID.

    Returns:
        dict: {"id": page ID, "token": the Page's own access token}.

    Raises:
        RuntimeError: If no Page you manage is linked to that Instagram account.
    """
    data = graph_call("GET", "/me/accounts", user_token, {
        "fields": "id,access_token,instagram_business_account",
        "limit": "100",
    })
    for page in data.get("data", []):
        linked = page.get("instagram_business_account") or {}
        if str(linked.get("id")) == str(instagram_account_id):
            return {"id": page["id"], "token": page["access_token"]}
    raise RuntimeError(
        "No Facebook Page linked to this Instagram account was found. Link the Page "
        "to Instagram, then reconnect Instagram on the channel page so it can manage the Page."
    )


def post_page_reel(video_path, caption, user_token, instagram_account_id):
    """
    Upload a video file and publish it as a public Reel on the linked Facebook Page.

    Args:
        video_path (str): The .mp4 file on this computer.
        caption (str): The Reel's description.
        user_token (str): The Instagram connection's access token.
        instagram_account_id (str): The numeric Instagram account ID, used
                                    to find the right Page.

    Returns:
        str: The Facebook video ID of the Reel.

    Raises:
        NothingPublishedError: A step before publishing failed; nothing is public.
        RuntimeError / httpx.HTTPError: The final step failed. The Reel MAY
            be public, so check the Page before trying again.
    """
    if not os.path.isfile(video_path):
        raise NothingPublishedError(f"Video file not found: {video_path}")

    # Steps 1–3. Any failure here leaves nothing public.
    try:
        page = find_linked_page(user_token, instagram_account_id)
        started = graph_call("POST", f"/{page['id']}/video_reels", page["token"],
                             {"upload_phase": "start"})
        video_id = started["video_id"]
        upload_video_file(started["upload_url"], video_path, page["token"])
    except Exception as error:
        raise NothingPublishedError(f"Facebook upload failed before publishing: {error}") from error

    # Step 4. This makes the Reel public, so errors are not wrapped.
    graph_call("POST", f"/{page['id']}/video_reels", page["token"], {
        "upload_phase": "finish",
        "video_id": video_id,
        "video_state": "PUBLISHED",
        "description": caption,
    })
    return video_id

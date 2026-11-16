# =============================================================
# src/publishing/youtube.py — upload videos as YouTube Shorts
#
# Uses the official YouTube Data API (videos.insert) with a
# "resumable upload", which takes two requests:
#   1. Send the video's details (title, description, privacy). YouTube
#      answers with a private upload address for this one video.
#   2. Send the MP4 file's bytes to that address. YouTube answers with
#      the new video's ID. Only this step creates the video.
#
# A vertical (9:16) video under 3 minutes automatically becomes a Short;
# "#Shorts" in the description helps YouTube recognise it.
#
# Important limits (see docs/control-center.md):
#   - Each upload uses ~1,600 of the free 10,000 daily quota units,
#     so about 6 uploads a day.
#   - Until Google audits the API project, YouTube forces every upload
#     to "private". Make it public in YouTube Studio, or apply for the
#     audit. We only count a video as a public post if YouTube says so.
#
# Library note: Google's google-api-python-client is the most-used way
# to call this API, but two plain HTTP requests (httpx, already used for
# every other platform) do the same job and keep all platforms alike.
# =============================================================

import json
import os

import httpx

from src.publishing.errors import NothingPublishedError

UPLOAD_START_URL = "https://www.googleapis.com/upload/youtube/v3/videos"

# YouTube's limits for titles and descriptions.
MAX_TITLE_CHARS = 100
MAX_DESCRIPTION_CHARS = 5000

# Used when the channel's YAML has no youtube: settings.
DEFAULT_PRIVACY = "public"
DEFAULT_CATEGORY_ID = "25"     # YouTube's "News & Politics" category
ALLOWED_PRIVACY = ("public", "unlisted", "private")

REQUEST_TIMEOUT_SECONDS = 60
UPLOAD_TIMEOUT_SECONDS = 600


def raise_if_google_error(response):
    """
    Stop with a clear message if YouTube refused a request.

    Args:
        response (httpx.Response): YouTube's answer.

    Returns:
        Nothing.

    Raises:
        RuntimeError: If the answer is an error (HTTP 400 or higher).
    """
    if response.status_code < 400:
        return
    message = f"HTTP {response.status_code}"
    try:
        message = response.json()["error"]["message"]
    except (ValueError, KeyError, TypeError):
        pass
    raise RuntimeError(f"YouTube API error: {message}")


def build_title(story_title):
    """
    Make a YouTube title from the story headline (at most 100 characters).

    Args:
        story_title (str): The story headline.

    Returns:
        str: The title, shortened with "..." if it was too long.
    """
    title = story_title.strip()
    if len(title) > MAX_TITLE_CHARS:
        title = title[:MAX_TITLE_CHARS - 3] + "..."
    return title


def build_description(story_title, story_url, channel_config):
    """
    Make the YouTube description: headline, source link, hashtags and #Shorts.

    Args:
        story_title (str): The story headline.
        story_url (str): The source article URL.
        channel_config (dict): The channel's settings (for its hashtags).

    Returns:
        str: The description, at most 5,000 characters.
    """
    hashtags = channel_config.get("publishing", {}).get("hashtags", [])
    hashtag_text = " ".join(hashtags)
    if "#Shorts" not in hashtags:
        hashtag_text = (hashtag_text + " #Shorts").strip()

    description = f"{story_title}\n\nSource: {story_url}\n\n{hashtag_text}"
    if len(description) > MAX_DESCRIPTION_CHARS:
        description = description[:MAX_DESCRIPTION_CHARS - 3] + "..."
    return description


def video_details(title, description, channel_config):
    """
    Build the video details YouTube needs before the upload.

    Args:
        title (str): The video title.
        description (str): The video description.
        channel_config (dict): The channel's settings; its optional
                               youtube: block sets privacy and category.

    Returns:
        dict: The "snippet" (words) and "status" (privacy) sections.

    Raises:
        NothingPublishedError: If the configured privacy is not allowed.
    """
    settings = channel_config.get("youtube", {})
    privacy = settings.get("privacy", DEFAULT_PRIVACY)
    if privacy not in ALLOWED_PRIVACY:
        raise NothingPublishedError("youtube.privacy must be public, unlisted or private.")

    return {
        "snippet": {
            "title": title,
            "description": description,
            "categoryId": str(settings.get("category_id", DEFAULT_CATEGORY_ID)),
        },
        "status": {
            "privacyStatus": privacy,
            # YouTube requires every upload to say whether it is made for
            # children. News explainers are not.
            "selfDeclaredMadeForKids": False,
        },
    }


def start_upload(details, file_size, token):
    """
    Send the video's details and get back the address to upload the file to.

    Args:
        details (dict): From video_details().
        file_size (int): The MP4's size in bytes.
        token (str): The YouTube access token.

    Returns:
        str: The one-video upload address (from the "Location" header).
    """
    response = httpx.post(
        UPLOAD_START_URL,
        params={"uploadType": "resumable", "part": "snippet,status"},
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=UTF-8",
            "X-Upload-Content-Type": "video/mp4",
            "X-Upload-Content-Length": str(file_size),
        },
        content=json.dumps(details),
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    raise_if_google_error(response)
    upload_url = response.headers.get("Location")
    if not upload_url:
        raise RuntimeError("YouTube did not return an upload address.")
    return upload_url


def send_video_file(upload_url, video_path, token):
    """
    Send the MP4 file's bytes to YouTube. This step creates the video.

    Args:
        upload_url (str): The address from start_upload().
        video_path (str): The MP4 file on this computer.
        token (str): The YouTube access token.

    Returns:
        dict: YouTube's description of the new video (its "id", "status", ...).
    """
    with open(video_path, "rb") as video_file:
        video_bytes = video_file.read()
    response = httpx.put(
        upload_url,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "video/mp4"},
        content=video_bytes,
        timeout=UPLOAD_TIMEOUT_SECONDS,
    )
    raise_if_google_error(response)
    return response.json()


def upload_short(video_path, title, description, token, channel_config):
    """
    Upload a video file to YouTube as a Short.

    Args:
        video_path (str): The .mp4 file on this computer.
        title (str): The video title (see build_title()).
        description (str): The description (see build_description()).
        token (str): The YouTube connection's access token.
        channel_config (dict): The channel's settings.

    Returns:
        dict: {"id": the YouTube video ID, "public": True if YouTube says
               the video is public (False while the project is unaudited)}.

    Raises:
        NothingPublishedError: The first step failed; no video exists.
        RuntimeError / httpx.HTTPError: The file upload failed. The video
            MAY exist, so check YouTube Studio before trying again.

    Example:
        result = upload_short("output/videos/news/story.mp4", "Big story",
                              "Big story\\n\\n#Shorts", token, config)
    """
    if not os.path.isfile(video_path):
        raise NothingPublishedError(f"Video file not found: {video_path}")

    # Step 1. Failing here means no video was created.
    try:
        details = video_details(title, description, channel_config)
        upload_url = start_upload(details, os.path.getsize(video_path), token)
    except Exception as error:
        raise NothingPublishedError(f"YouTube upload failed before sending the video: {error}") from error

    # Step 2. Errors here are not wrapped: YouTube may have the video.
    video = send_video_file(upload_url, video_path, token)
    privacy = video.get("status", {}).get("privacyStatus")
    return {"id": video["id"], "public": privacy == "public"}

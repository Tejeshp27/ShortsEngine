# =============================================================
# src/sources/twitch.py — talk to the official Twitch API (Helix)
#
# Used by the clips channel (src/clips/). Only official endpoints
# are used; nothing is scraped:
#
#   GET  /helix/users            look up a streamer's numeric ID
#   GET  /helix/clips            list a streamer's most-viewed clips
#   GET  /helix/videos           discover archived VOD metadata
#   GET  /helix/clips/downloads  get a temporary download link for a clip
#
# Public discovery uses app_get/resolve_user/discover_clips/discover_videos.
# It never calls the legacy download helpers below.
#
# Two kinds of Twitch tokens are involved:
#   - An APP token (client credentials) reads public data such as
#     the clip list. It only needs TWITCH_CLIENT_ID/SECRET from .env.
#   - A USER token is needed to download. Twitch only gives download
#     links to the streamer themselves (or one of their editors), so
#     each streamer logs in once — see src/clips/twitch_login.py.
#
# Library note: httpx is used for HTTP, the same as the publishing
# code, so every outside call in the project looks alike.
# =============================================================

import os
import time
from datetime import datetime, timedelta, timezone

import httpx

TWITCH_API_BASE = "https://api.twitch.tv/helix"
TWITCH_TOKEN_URL = "https://id.twitch.tv/oauth2/token"

# How long to wait for Twitch before giving up, in seconds.
REQUEST_TIMEOUT_SECONDS = 20

# Downloads can be large (a 60-second 1080p clip is ~30-60 MB).
DOWNLOAD_TIMEOUT_SECONDS = 180

# Twitch accepts at most this many clip IDs per download request.
MAX_CLIPS_PER_DOWNLOAD_REQUEST = 10

# The app token is kept in memory for the rest of the run, so one
# run asks Twitch for it only once.
_app_token_cache = {}


class TwitchError(Exception):
    """Raised with a plain message when a Twitch request fails."""


def twitch_credentials():
    """
    Read the Twitch app's ID and secret from .env.

    Returns:
        tuple: (client_id, client_secret)

    Raises:
        TwitchError: If either value is missing.
    """
    client_id = os.getenv("TWITCH_CLIENT_ID", "")
    client_secret = os.getenv("TWITCH_CLIENT_SECRET", "")
    if not client_id or not client_secret or client_id.startswith("your-"):
        raise TwitchError("Missing TWITCH_CLIENT_ID or TWITCH_CLIENT_SECRET in .env. "
                          "Create an app at https://dev.twitch.tv/console/apps.")
    return client_id, client_secret


def get_app_token(force_refresh=False):
    """
    Get a Twitch app access token (the "client credentials" flow).

    This token reads public data, such as the list of a streamer's clips.

    Args:
        force_refresh: True to replace a token rejected by Twitch once.

    Returns:
        str: The access token.

    Raises:
        TwitchError: If the credentials are missing or Twitch refuses them.
    """
    client_id, client_secret = twitch_credentials()
    saved = _app_token_cache.get(client_id)
    if not force_refresh and saved and saved["expires_at"] > time.time() + 60:
        return saved["token"]

    try:
        response = httpx.post(TWITCH_TOKEN_URL, timeout=REQUEST_TIMEOUT_SECONDS, data={
            "client_id": client_id,
            "client_secret": client_secret,
            "grant_type": "client_credentials",
        })
        data = response.json()
    except (httpx.HTTPError, ValueError):
        raise TwitchError("Twitch app login failed; check the connection and app credentials.") from None
    if response.status_code != 200 or "access_token" not in data:
        raise TwitchError(f"Twitch refused the app credentials (HTTP {response.status_code}).")

    _app_token_cache[client_id] = {"token": data["access_token"],
                                   "expires_at": time.time() + data.get("expires_in", 3600)}
    return data["access_token"]


def wait_for_rate_limit(response):
    """
    Wait until a Twitch rate-limit reset, returning nothing or raising for an excessive delay.

    Args:
        response: The HTTP 429 answer with a Ratelimit-Reset timestamp.

    Returns:
        Nothing after waiting; delays over two minutes defer work to a later job.
    """
    try:
        delay = max(1, float(response.headers.get("Ratelimit-Reset", "")) - time.time() + 1)
    except ValueError:
        delay = 60
    if not 0 < delay <= 120:
        raise TwitchError("Twitch rate limit resets later; retry discovery in a later job.")
    while delay > 0:
        interval = min(60, delay)
        time.sleep(interval)
        delay -= interval


def app_get(endpoint, params):
    """
    Read public metadata with one token refresh and at most two rate-limit retries.

    Args:
        endpoint: /users, /clips or /videos; media download endpoints are refused.
        params: The endpoint's public lookup or pagination parameters.

    Returns:
        Twitch's JSON dictionary, or a safe TwitchError without credentials.
    """
    if endpoint not in ("/users", "/clips", "/videos"):
        raise TwitchError("Discovery only supports public user, clip and VOD metadata.")
    client_id, _ = twitch_credentials()
    token = get_app_token()
    refreshed = False
    rate_retries = 0
    while True:
        try:
            response = httpx.get(TWITCH_API_BASE + endpoint, params=params,
                                 headers={"Authorization": "Bearer " + token, "Client-Id": client_id},
                                 timeout=REQUEST_TIMEOUT_SECONDS)
        except httpx.HTTPError:
            raise TwitchError(f"Twitch metadata request {endpoint} could not connect.") from None
        if response.status_code == 401 and not refreshed:
            token = get_app_token(force_refresh=True)
            refreshed = True
            continue
        if response.status_code == 429 and rate_retries < 2:
            wait_for_rate_limit(response)
            rate_retries += 1
            continue
        if response.status_code != 200:
            raise TwitchError(f"Twitch metadata request {endpoint} failed (HTTP {response.status_code}).")
        try:
            return response.json()
        except ValueError:
            raise TwitchError(f"Twitch metadata request {endpoint} returned invalid JSON.") from None


def resolve_user(login):
    """
    Resolve a configured Twitch login to its public identity.

    Args:
        login: A creator login such as jasontheween.

    Returns:
        The user dictionary with numeric ID, login and display name.
    """
    users = app_get("/users", {"login": login.lower()}).get("data", [])
    if not users:
        raise TwitchError(f"Twitch user '{login}' was not found. Check the channel YAML.")
    return users[0]


def metadata_pages(endpoint, params, max_pages):
    """
    Read a bounded list of public metadata pages without following media URLs.

    Args:
        endpoint: /clips or /videos.
        params: Fixed filters, including the requested page size.
        max_pages: Maximum requests for this creator and source type.

    Returns:
        A dictionary containing items and whether the page limit truncated results.
    """
    items = []
    seen_cursors = set()
    params = dict(params)
    cursor = ""
    for page in range(max_pages):
        data = app_get(endpoint, dict(params))
        items.extend(data.get("data", []))
        cursor = data.get("pagination", {}).get("cursor", "")
        if not cursor:
            return {"items": items, "truncated": False}
        if cursor in seen_cursors:
            raise TwitchError("Twitch repeated a pagination cursor; discovery stopped safely.")
        seen_cursors.add(cursor)
        params["after"] = cursor
    return {"items": items, "truncated": bool(cursor)}


def discover_clips(broadcaster_id, started_at, ended_at, max_pages=10):
    """
    List clip metadata within a fixed date window; never request video bytes.

    Args:
        broadcaster_id: The creator's numeric Twitch ID.
        started_at: Inclusive UTC window start in Twitch's date format.
        ended_at: UTC window end, kept unchanged across pages.
        max_pages: Maximum pages of 100 clips; Twitch caps a query near 1,000 results.

    Returns:
        Items and a truncation flag from bounded cursor pagination.
    """
    return metadata_pages("/clips", {"broadcaster_id": broadcaster_id, "started_at": started_at,
                                    "ended_at": ended_at, "first": 100}, max_pages)


def discover_videos(broadcaster_id, max_pages=10):
    """
    List archived VOD metadata, newest first, without obtaining playable files.

    Args:
        broadcaster_id: The creator's numeric Twitch ID.
        max_pages: Maximum pages of 100 archived videos.

    Returns:
        Items and a truncation flag from bounded cursor pagination.
    """
    return metadata_pages("/videos", {"user_id": broadcaster_id, "type": "archive",
                                     "sort": "time", "first": 100}, max_pages)


def twitch_get(endpoint, params, access_token):
    """
    Make one authenticated GET request to the Twitch API.

    Args:
        endpoint (str): The API path, e.g. "/clips".
        params (dict or list): Query parameters. A list of (name, value)
                               pairs allows a name to repeat (clip_id=a&clip_id=b).
        access_token (str): An app or user token.

    Returns:
        dict: The JSON answer.

    Raises:
        TwitchError: If Twitch answers with an error.
    """
    client_id, _ = twitch_credentials()
    response = httpx.get(
        TWITCH_API_BASE + endpoint,
        params=params,
        headers={"Authorization": f"Bearer {access_token}", "Client-Id": client_id},
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    if response.status_code != 200:
        try:
            message = response.json().get("message", "")
        except ValueError:
            message = ""
        raise TwitchError(f"Twitch API {endpoint} failed (HTTP {response.status_code}) {message}".strip())
    return response.json()


def get_user(login, access_token):
    """
    Look up a Twitch user by their login name.

    Args:
        login (str): The login, e.g. "examplestreamer".
        access_token (str): An app or user token.

    Returns:
        dict: {"id": "12345", "login": "examplestreamer", "display_name": "ExampleStreamer"}

    Raises:
        TwitchError: If no such user exists.
    """
    data = twitch_get("/users", {"login": login}, access_token)
    users = data.get("data", [])
    if not users:
        raise TwitchError(f"Twitch user '{login}' was not found. Check the spelling in the channel YAML.")
    return users[0]


def rfc3339_hours_ago(hours, now=None):
    """
    Write the time `hours` ago in the format Twitch expects.

    Args:
        hours (int): How many hours back.
        now (datetime or None): The current time (tests pass a fixed one).

    Returns:
        str: For example "2026-11-16T18:00:00Z".
    """
    if now is None:
        now = datetime.now(timezone.utc)
    since = now - timedelta(hours=hours)
    return since.strftime("%Y-%m-%dT%H:%M:%SZ")


def fetch_top_clips(broadcaster_id, hours_back, max_clips, access_token):
    """
    Get a streamer's most-viewed clips from the last few hours.

    Args:
        broadcaster_id (str): The streamer's numeric ID (from get_user).
        hours_back (int): Only clips created in this many recent hours.
        max_clips (int): How many clips to return at most.
        access_token (str): An app or user token.

    Returns:
        list[dict]: Twitch clip objects, most-viewed first. Each has
                    "id", "url", "title", "view_count", "created_at",
                    "duration" (seconds), "broadcaster_name" and more.
    """
    data = twitch_get("/clips", {
        "broadcaster_id": broadcaster_id,
        "started_at": rfc3339_hours_ago(hours_back),
        "first": 100,
    }, access_token)
    clips = data.get("data", [])

    # Twitch normally returns clips by views already; sorting again makes
    # sure "top clips" really means the most-viewed ones.
    def view_count_of(clip):
        """Return a clip's view count (used to sort the clips)."""
        return clip.get("view_count", 0)

    clips.sort(key=view_count_of, reverse=True)
    return clips[:max_clips]


def get_download_links(broadcaster_id, editor_id, clip_ids, user_token):
    """
    Ask Twitch for temporary download links for some clips.

    This is Twitch's official clip-download endpoint. It only works
    with a user token from the streamer themselves (or one of their
    editors) that includes the "channel:manage:clips" or
    "editor:manage:clips" permission.

    Args:
        broadcaster_id (str): The streamer's numeric ID.
        editor_id (str): The ID of the user whose token this is
                         (the streamer's own ID when they logged in).
        clip_ids (list[str]): Up to 10 clip IDs.
        user_token (str): That user's access token.

    Returns:
        dict: clip ID → download link (the landscape version). Clips
              Twitch would not give a link for are left out.
    """
    params = [("broadcaster_id", broadcaster_id), ("editor_id", editor_id)]
    for clip_id in clip_ids[:MAX_CLIPS_PER_DOWNLOAD_REQUEST]:
        params.append(("clip_id", clip_id))

    data = twitch_get("/clips/downloads", params, user_token)
    links = {}
    for item in data.get("data", []):
        # We want the original wide video: we make our own vertical
        # layout. Twitch's ready-made portrait version is a fallback.
        link = item.get("landscape_download_url") or item.get("portrait_download_url")
        if link:
            links[item["clip_id"]] = link
    return links


def download_file(url, destination_path):
    """
    Download a file to disk, writing to a temporary name first.

    Writing to "<name>.part" and renaming at the end means a cut-off
    download never looks like a finished file.

    Args:
        url (str): The temporary link from get_download_links().
        destination_path (str): Where to save the file.

    Returns:
        str: destination_path.

    Raises:
        TwitchError: If the download fails.
    """
    folder = os.path.dirname(destination_path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    partial_path = destination_path + ".part"

    # stream() reads the file in pieces, so a large clip never has to
    # fit in memory all at once.
    with httpx.stream("GET", url, timeout=DOWNLOAD_TIMEOUT_SECONDS, follow_redirects=True) as response:
        if response.status_code != 200:
            raise TwitchError(f"Clip download failed (HTTP {response.status_code}).")
        with open(partial_path, "wb") as file:
            for piece in response.iter_bytes():
                file.write(piece)

    os.replace(partial_path, destination_path)
    return destination_path

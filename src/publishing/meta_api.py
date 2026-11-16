# =============================================================
# src/publishing/meta_api.py — small helpers shared by the
# Instagram and Facebook publishers
#
# Instagram and Facebook are both owned by Meta and use the same
# "Graph API" (graph.facebook.com). Both also accept a video file
# sent straight from this computer to rupload.facebook.com, which
# Meta calls a "resumable upload". That means we do NOT need to put
# the video on a public website first.
#
# This file holds the two pieces both publishers need (the shared
# NothingPublishedError lives in errors.py):
#   - graph_call(): one request to the Graph API
#   - upload_video_file(): send the MP4's bytes to Meta
# =============================================================

import os

import httpx

from src.dashboard.accounts import graph_base

# Normal Graph API requests are quick; uploading a video can take longer.
REQUEST_TIMEOUT_SECONDS = 60
UPLOAD_TIMEOUT_SECONDS = 600


def graph_version():
    """
    Get the Graph API version in use, such as "v24.0".

    Returns:
        str: The version part of the Graph API address.
    """
    # graph_base() returns e.g. "https://graph.facebook.com/v24.0".
    return graph_base().rsplit("/", 1)[1]


def raise_if_meta_error(response):
    """
    Read Meta's answer and stop with a clear message if it is an error.

    Args:
        response (httpx.Response): The answer from Meta.

    Returns:
        dict: The answer as a dictionary when it succeeded.

    Raises:
        RuntimeError: If Meta reports an error or the answer is not JSON.
    """
    try:
        data = response.json()
    except ValueError:
        raise RuntimeError(f"Meta returned an unreadable answer (HTTP {response.status_code}).")

    if response.status_code >= 400 or "error" in data:
        error = data.get("error", {})
        message = error.get("message", "unknown error")
        code = error.get("code", response.status_code)
        raise RuntimeError(f"Meta API error: {message} (code {code})")
    return data


def graph_call(method, path, token, fields=None):
    """
    Send one request to Meta's Graph API and return its answer.

    Args:
        method (str): "GET" or "POST".
        path (str): The part after the version, e.g. "/12345/media".
        token (str): The access token. It goes in a private header,
                     never in the address, so it cannot leak into logs.
        fields (dict or None): Values to send. For GET they go in the
                               address; for POST they go in the body.

    Returns:
        dict: Meta's answer.

    Raises:
        RuntimeError: If Meta refuses the request.
        httpx.HTTPError: If Meta cannot be reached.

    Example:
        graph_call("GET", "/me/accounts", token, {"fields": "id,name"})
    """
    url = graph_base() + path
    headers = {"Authorization": f"OAuth {token}"}
    if method == "GET":
        response = httpx.get(url, headers=headers, params=fields, timeout=REQUEST_TIMEOUT_SECONDS)
    else:
        response = httpx.post(url, headers=headers, data=fields, timeout=REQUEST_TIMEOUT_SECONDS)
    return raise_if_meta_error(response)


def upload_video_file(upload_url, video_path, token):
    """
    Send a video file's bytes to Meta's upload server in one piece.

    Args:
        upload_url (str): The rupload.facebook.com address Meta gave us.
        video_path (str): The MP4 file on this computer.
        token (str): The access token for this upload.

    Returns:
        dict: Meta's answer, usually {"success": true}.

    Raises:
        RuntimeError: If Meta refuses the upload.
    """
    file_size = os.path.getsize(video_path)
    headers = {
        "Authorization": f"OAuth {token}",
        # "offset: 0" means "this is the start of the file". We send the
        # whole file at once; our videos are small (well under 100 MB).
        "offset": "0",
        "file_size": str(file_size),
    }
    with open(video_path, "rb") as video_file:
        video_bytes = video_file.read()
    response = httpx.post(upload_url, headers=headers, content=video_bytes,
                          timeout=UPLOAD_TIMEOUT_SECONDS)
    return raise_if_meta_error(response)

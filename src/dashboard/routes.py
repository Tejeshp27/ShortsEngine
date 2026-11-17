# =============================================================
# src/dashboard/routes.py — the control-center pages and forms
#
# Pages:   /channels/<channel>, /money, /library, /new, /jobs
# Forms:   connect/disconnect accounts, add money entries, find
#          stories, write/render videos, record a public post
#
# Login is checked once for every request by the middleware in
# app.py. On top of that, every form that changes something calls
# require_form_token(), which stops other websites from submitting
# forms in your name ("CSRF" protection).
#
# Slow work (writing, rendering, collecting statistics) is never
# done inside a page request: it is saved as a job (jobs.py) and run
# by a separate process (worker.py).
#
# "Form(...)" tells FastAPI to read that value from the submitted
# form; "Form(...)" with three dots means the field is required.
# =============================================================

import re
import secrets
import time
from datetime import date
from urllib.parse import urlencode, urlsplit

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response

from src.dashboard import accounts, data, jobs, media
from src.dashboard.app import render_page, require_form_token
from src.dashboard.settings import channel_config, job_settings, list_channels
from src.storage.database import get_connection
from src.storage.finance import add_money_entry, money_summary, utc_now
from src.storage.queries import delete_video_record, get_video_by_id, is_file_used_by_video

router = APIRouter()

# Platforms that have their own account connection (Facebook borrows Instagram's).
PLATFORMS = accounts.ACCOUNT_PLATFORMS

# An account login (OAuth) must be finished within 10 minutes.
OAUTH_STATE_LIFETIME_SECONDS = 600


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def redirect(path):
    """
    Send the browser to another page after a form was handled.

    Status 303 tells the browser to load the new page with GET, so
    pressing refresh afterwards does not send the form again.

    Args:
        path (str): Where to go, e.g. "/money".

    Returns:
        RedirectResponse: The redirect.
    """
    return RedirectResponse(path, status_code=303)


def new_submission_id():
    """
    Make a random ID for a form, so a double-click is only handled once.

    Returns:
        str: A random value, e.g. "Qm3x...".
    """
    return secrets.token_urlsafe(24)


def queue(channel, kind, payload, dry_run, submission_id):
    """
    Save a job, start its worker, and send the browser to its progress page.

    Args:
        channel (str): The channel.
        kind (str): "discover", "generate", "render" or "sync".
        payload (dict): The job's options.
        dry_run (bool): True means no paid calls.
        submission_id (str): The form's random ID.

    Returns:
        RedirectResponse: To /jobs/<job_id>.
    """
    job_id, created = jobs.enqueue_job(channel, kind, payload, dry_run, submission_id)
    # A repeated form submission returns the existing job; don't start it twice.
    if created:
        jobs.launch_job(job_id)
    return redirect(f"/jobs/{job_id}")


def check_platform(platform):
    """
    Refuse any platform except TikTok, Instagram and YouTube.

    Args:
        platform (str): The platform from the page address.

    Raises:
        ValueError: For anything else.
    """
    if platform not in PLATFORMS:
        raise ValueError("Choose TikTok, Instagram or YouTube.")


def check_article_url(url):
    """
    Refuse a pasted article address that is not an ordinary web link.

    Args:
        url (str): The pasted address.

    Raises:
        ValueError: For non-http(s) links, links with a login, or very
                    long addresses.
    """
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError("Enter an ordinary public article URL.")
    if parts.username or len(url) > 2048:
        raise ValueError("Enter an ordinary public article URL.")


# ---------------------------------------------------------------------------
# Channel pages and connected accounts
# ---------------------------------------------------------------------------

def most_viewed(videos, count):
    """
    Pick the most-viewed videos.

    Args:
        videos (list[dict]): From data.video_rows().
        count (int): How many to return.

    Returns:
        list[dict]: Up to `count` videos with known views, most viewed first.
    """
    measured = []
    for video in videos:
        if video["views"] is not None:
            measured.append(video)
    measured.sort(key=views_of, reverse=True)
    return measured[:count]


def views_of(video):
    """
    Get a video's view count (used for sorting).

    Args:
        video (dict): A video row.

    Returns:
        int: Its views.
    """
    return video["views"]


def find_channel_card(channel):
    """
    Get the overview card (totals) for one channel.

    Args:
        channel (str): The channel.

    Returns:
        dict or None: The card, or None if the channel has none.
    """
    for card in data.overview()["channel_cards"]:
        if card["key"] == channel:
            return card
    return None


@router.get("/channels/{channel}")
def channel_page(request: Request, channel: str):
    """Show one channel's page.

    The page lists the channel's connected accounts, its totals, follower
    and view charts, its five most viewed videos and its eight newest ones.

    Inputs:
        request: the incoming browser request.
        channel: the channel's file name without ".yaml", for example "news".

    Returns:
        The rendered channel page, or an error if the channel does not exist.
    """
    config = channel_config(channel)
    videos = data.video_rows(channel)
    return render_page(request, "channel.html", {
        "channel": channel,
        "config": config,
        "card": find_channel_card(channel),
        "accounts": data.account_rows(channel),
        "recent_videos": videos[:8],
        "best_videos": most_viewed(videos, 5),
        "followers_chart": data.history_chart("followers", channel),
        "views_chart": data.history_chart("views", channel),
        "submission_id": new_submission_id(),
    })


@router.post("/channels/{channel}/sync")
def sync_accounts(request: Request, channel: str, form_token: str = Form(""),
                  submission_id: str = Form("")):
    """Start a background job that collects today's statistics for a channel.

    Inputs:
        request: the incoming browser request.
        channel: the channel's file name without ".yaml", for example "news".
        form_token: the secret token that proves the form came from our page.
        submission_id: a one-time number from the form, so a double click
            does not start the same job twice.

    Returns:
        A redirect to the new job's progress page.
    """
    require_form_token(request, form_token)
    return queue(channel, "sync", {}, False, submission_id)


@router.post("/channels/{channel}/accounts/{platform}/token")
def connect_token(request: Request, channel: str, platform: str,
                  access_token: str = Form(""), account_id: str = Form(""),
                  use_environment: bool = Form(False), form_token: str = Form("")):
    """
    Connect an account using a token pasted by hand (or imported from .env).

    The token is checked with the platform before it is saved, then stored
    encrypted. A pasted token has no known expiry date and cannot be
    renewed automatically; the channel page says so.
    """
    require_form_token(request, form_token)
    channel_config(channel)
    check_platform(platform)
    if platform == "youtube":
        # A pasted Google token stops working within an hour and can't be renewed.
        raise ValueError("Connect YouTube with the Google login button instead of pasting a token.")

    if use_environment:
        access_token = accounts.required_env(platform.upper() + "_ACCESS_TOKEN")
        if platform == "instagram":
            account_id = accounts.required_env("INSTAGRAM_ACCOUNT_ID")

    access_token = access_token.strip()
    if not access_token or len(access_token) > 10000:
        raise ValueError("Enter a valid access token.")

    profile = accounts.fetch_profile(platform, access_token, account_id.strip())
    accounts.save_connection(channel, platform, profile, {"access_token": access_token})
    return redirect(f"/channels/{channel}")


@router.post("/channels/{channel}/accounts/{platform}/disconnect")
def disconnect_account(request: Request, channel: str, platform: str, form_token: str = Form("")):
    """
    Remove a channel's account, its saved token and its follower history.

    The follower snapshots are removed automatically because their table
    is linked to accounts with "ON DELETE CASCADE".
    """
    require_form_token(request, form_token)
    channel_config(channel)
    connection = get_connection()
    try:
        connection.execute("DELETE FROM accounts WHERE channel = ? AND platform = ?", (channel, platform))
        connection.commit()
    finally:
        connection.close()
    return redirect(f"/channels/{channel}")


@router.post("/channels/{channel}/accounts/{platform}/connect")
def start_oauth(request: Request, channel: str, platform: str,
                account_id: str = Form(""), form_token: str = Form("")):
    """
    Start the official login for an account and send the browser to it.

    A random "state" value is saved together with this browser's form
    token. The callback below only accepts the login if both match, so
    nobody can trick another browser into finishing a login.
    """
    require_form_token(request, form_token)
    channel_config(channel)
    account_id = account_id.strip()
    if platform == "instagram" and not account_id.isdigit():
        raise ValueError("Enter the Instagram professional account ID before connecting.")

    state = secrets.token_urlsafe(32)
    url = accounts.authorization_url(platform, state)

    connection = get_connection()
    try:
        # Remove old, unused login attempts while we're here.
        connection.execute("DELETE FROM oauth_states WHERE created_at < ?",
                           (time.time() - OAUTH_STATE_LIFETIME_SECONDS,))
        connection.execute("INSERT INTO oauth_states VALUES (?, ?, ?, ?, ?, ?)",
                           (state, channel, platform, account_id, form_token, time.time()))
        connection.commit()
    finally:
        connection.close()
    return redirect(url)


def is_valid_login_attempt(attempt, platform, browser_token):
    """
    Check a returning account login against the attempt we saved.

    Args:
        attempt: The saved oauth_states row, or None.
        platform (str): The platform in the callback address.
        browser_token (str): This browser's form token.

    Returns:
        bool: True if the login is current, for this platform, and was
              started by this same browser.
    """
    if not attempt:
        return False
    if attempt["platform"] != platform:
        return False
    if attempt["created_at"] < time.time() - OAUTH_STATE_LIFETIME_SECONDS:
        return False
    # compare_digest takes the same time whether or not the values
    # match, so the comparison doesn't leak the token bit by bit.
    return secrets.compare_digest(attempt["session_token"], browser_token)


@router.get("/oauth/{platform}/callback")
def oauth_callback(request: Request, platform: str, state: str = "", code: str = "", error: str = ""):
    """
    Finish an account login: check the state, get the token, save the account.

    The saved state is deleted straight away, so the same login link
    cannot be used twice.
    """
    connection = get_connection()
    try:
        connection.execute("BEGIN IMMEDIATE")
        attempt = connection.execute("SELECT * FROM oauth_states WHERE state = ?", (state,)).fetchone()
        browser_token = request.session.get("form_token", "")
        if not is_valid_login_attempt(attempt, platform, browser_token):
            raise HTTPException(400, "The account login expired or belongs to another browser. Connect again.")
        connection.execute("DELETE FROM oauth_states WHERE state = ?", (state,))
        connection.commit()
    finally:
        connection.close()

    if error or not code:
        raise ValueError("Account connection was cancelled or refused. You can try again from the channel page.")

    credentials = accounts.exchange_code(platform, code)
    profile = accounts.fetch_profile(platform, credentials["access_token"], attempt["account_id"])
    accounts.save_connection(attempt["channel"], platform, profile, credentials)
    return redirect(f"/channels/{attempt['channel']}")


# ---------------------------------------------------------------------------
# Money
# ---------------------------------------------------------------------------

@router.get("/money")
def money_page(request: Request, channel: str = ""):
    """Show money made and money spent.

    Inputs:
        request: the incoming browser request.
        channel: optional channel name from the address bar, for example
            /money?channel=news. Empty means "all channels".

    Returns:
        The rendered money page with monthly totals and the full ledger.
    """
    context = money_summary(channel or None)
    context.update({
        "channel": channel,
        "channels": list_channels(),
        "today": date.today().isoformat(),
        "submission_id": new_submission_id(),
    })
    return render_page(request, "money.html", context)


@router.post("/money")
def save_money(request: Request, channel: str = Form(...), platform: str = Form(...),
               kind: str = Form(...), amount: str = Form(...), entry_date: str = Form(...),
               note: str = Form(""), submission_id: str = Form(""), form_token: str = Form("")):
    """Save a payout or expense that was typed into the Money page form.

    Inputs:
        request: the incoming browser request.
        channel: which channel the money belongs to.
        platform: where it came from or went to, for example "tiktok".
        kind: "payout" for money made or "expense" for money spent.
        amount: the amount in US dollars, as typed, for example "12.50".
        entry_date: the day it happened, as YYYY-MM-DD.
        note: an optional note.
        submission_id: a one-time number so a double click saves only once.
        form_token: the secret token that proves the form came from our page.

    Returns:
        A redirect back to the Money page for that channel.
    """
    require_form_token(request, form_token)
    channel_config(channel)
    add_money_entry(channel, platform, kind, amount, entry_date, note, submission_id)
    return redirect("/money?" + urlencode({"channel": channel}))


@router.post("/money/{entry_id}/delete")
def delete_money(request: Request, entry_id: int, form_token: str = Form("")):
    """Delete a payout or expense that was typed in by hand.

    Only hand-typed entries can be deleted here; costs recorded
    automatically by AI calls are never removed.

    Inputs:
        request: the incoming browser request.
        entry_id: the entry's number in the money_entries table.
        form_token: the secret token that proves the form came from our page.

    Returns:
        A redirect back to the Money page.
    """
    require_form_token(request, form_token)
    connection = get_connection()
    try:
        connection.execute("DELETE FROM money_entries WHERE id = ?", (entry_id,))
        connection.commit()
    finally:
        connection.close()
    return redirect("/money")


# ---------------------------------------------------------------------------
# Video library and private video files
# ---------------------------------------------------------------------------

@router.get("/library")
def library_page(request: Request, channel: str = "", status: str = "", q: str = "", page: int = 1):
    """Show the video library, one page at a time.

    Inputs:
        request: the incoming browser request.
        channel: optional channel to show only, for example "news".
        status: optional status to show only, for example "approved".
        q: optional words to look for in video titles (cut to 200 letters).
        page: which page of results to show, starting at 1.

    Returns:
        The rendered library page.
    """
    search = q[:200]
    context = media.library(channel, status, search, page)
    context.update({
        "channel": channel,
        "status": status,
        "q": search,
        "channels": list_channels(),
        # The current filters, ready to add to "next page" links.
        "filter_query": urlencode({"channel": channel, "status": status, "q": search}),
    })
    return render_page(request, "library.html", context)


@router.get("/library/untracked")
def untracked_video(request: Request, path: str):
    """Play a video file found in output/ that the database does not know about.

    Inputs:
        request: the incoming browser request.
        path: the file's location inside output/, for example
            "news/2026-11-10-budget-vote.mp4". Paths outside output/ are refused.

    Returns:
        The rendered player page, or a "not found" error (404).
    """
    video = media.requested_video(path)
    if video is None:
        raise HTTPException(404, "Video not found")
    title = video.stem.replace("-", " ")
    return render_page(request, "untracked.html", {"relative": path, "title": title})


def require_delete_confirmation(confirmed):
    """
    Refuse a delete unless the "I understand" box was ticked.

    Args:
        confirmed (bool): Whether the confirmation box was ticked.

    Raises:
        ValueError: If it was not ticked.
    """
    if not confirmed:
        raise ValueError("Tick the confirmation box to delete this video. Deleting cannot be undone.")


def remove_files_of_deleted_video(deleted):
    """
    Delete the MP4 and script of a video whose record was just removed.

    A file is kept if another video record still points at it (for
    example, two renders made from the same script).

    Args:
        deleted (sqlite3.Row): The removed video row.

    Raises:
        ValueError: If a file could not be deleted (the record is already
                    gone, so the file now shows as "Not in database").
    """
    try:
        if deleted["video_path"] and not is_file_used_by_video("video_path", deleted["video_path"]):
            media.delete_video_file(deleted["video_path"])
        if deleted["script_path"] and not is_file_used_by_video("script_path", deleted["script_path"]):
            media.delete_script_files(deleted["script_path"])
    except OSError as error:
        raise ValueError(
            "The video was removed from the database, but a file could not be deleted "
            f"({error.strerror}). It now shows as 'Not in database' in the library."
        ) from error


@router.post("/video/{video_id}/delete")
def delete_video(request: Request, video_id: int, confirmed: bool = Form(False),
                 form_token: str = Form(default="")):
    """Delete a video: its database record, MP4, script and storyboard.

    Money already spent on it stays on the Money page. Videos that were
    sent to a platform, or that a job is working on, are refused.

    Inputs:
        request: the incoming browser request.
        video_id: the video's number in the database.
        confirmed: whether the "cannot be undone" box was ticked.
        form_token: the secret token that proves the form came from our page.

    Returns:
        A redirect to the video library, or an error page explaining why
        the video was not deleted.
    """
    require_form_token(request, form_token)
    if not get_video_by_id(video_id):
        raise HTTPException(404, "Video not found")
    require_delete_confirmation(confirmed)
    deleted = delete_video_record(video_id)
    remove_files_of_deleted_video(deleted)
    return redirect("/library")


@router.post("/library/untracked/delete")
def delete_untracked_video(request: Request, path: str = Form(...), confirmed: bool = Form(False),
                           form_token: str = Form(default="")):
    """Delete an MP4 in output/ that has no database record.

    Inputs:
        request: the incoming browser request.
        path: the file's location inside output/, for example
            "videos/news/old-story.mp4". Paths outside output/ are refused.
        confirmed: whether the "cannot be undone" box was ticked.
        form_token: the secret token that proves the form came from our page.

    Returns:
        A redirect to the video library, or an error page.
    """
    require_form_token(request, form_token)
    video = media.requested_video(path)
    if video is None:
        raise HTTPException(404, "Video not found")
    require_delete_confirmation(confirmed)
    # Re-check that no record uses this file: a video saved since the page
    # loaded must be deleted from its own page, which keeps its costs.
    _, known_paths = media.tracked_entries()
    if media.relative_video(video) in known_paths:
        raise ValueError("This file belongs to a video record. Delete it from the video's own page.")
    media.delete_video_file(video)
    return redirect("/library")


@router.get("/media/video/{path:path}")
def video_file(path: str):
    """
    Send a private MP4 to the browser's video player.

    FileResponse supports "range" requests, which lets the player skip
    ahead without downloading the whole file. The login middleware has
    already checked the browser is logged in.
    """
    video = media.requested_video(path)
    if video is None:
        raise HTTPException(404, "Video not found")
    return FileResponse(video, media_type="video/mp4")


@router.get("/media/thumbnail/{path:path}")
def video_thumbnail(path: str):
    """Send the small preview picture for a video.

    Inputs:
        path: the video's location inside output/. Paths outside output/
            are refused.

    Returns:
        The JPEG preview picture, an empty answer (code 204) if no picture
        could be made, or a "not found" error (404).
    """
    video = media.requested_video(path)
    if video is None:
        raise HTTPException(404, "Video not found")
    preview = media.thumbnail(video)
    if preview is None:
        return Response(status_code=204)
    return FileResponse(preview, media_type="image/jpeg")


# ---------------------------------------------------------------------------
# Making new videos
# ---------------------------------------------------------------------------

def stories_from_job(job_id, channel):
    """
    Get the stories found by a finished "find stories" job.

    Args:
        job_id (str): The job, or "" for none.
        channel (str): The channel the job must belong to.

    Returns:
        tuple: (list of story dicts, the job's explanation or None)
    """
    from src.dashboard.worker import saved_story

    if not job_id:
        return [], None
    job = jobs.job_record(job_id)
    if not job or job["channel"] != channel:
        return [], None

    stories = []
    for story_id in job["result"].get("story_ids", []):
        stories.append(saved_story(channel, story_id))
    return stories, job["result"].get("reason")


def article_channels(channels):
    """
    Keep only the channels whose videos are written from articles.

    The "New video" page finds stories and writes scripts, which clips
    channels don't do (they use python -m src.clips instead).

    Args:
        channels (list[dict]): From settings.list_channels().

    Returns:
        list[dict]: The same entries, without clips channels.
    """
    from src.clips.settings import is_clips_channel
    kept = []
    for channel in channels:
        if channel["error"]:
            kept.append(channel)
            continue
        if not is_clips_channel(channel_config(channel["key"])):
            kept.append(channel)
    return kept


@router.get("/new")
def new_video_page(request: Request, channel: str = "", source_job: str = ""):
    """Show the "make a video" page.

    Here the user picks a channel, finds stories (or pastes an article
    link), and sees what writing and rendering would cost.

    Inputs:
        request: the incoming browser request.
        channel: which channel to make a video for. Defaults to the first one.
        source_job: optional id of a finished "find stories" job; its
            stories are listed on the page.

    Returns:
        The rendered "new video" page.
    """
    channels = article_channels(list_channels())
    if not channel and channels:
        channel = channels[0]["key"]
    config = channel_config(channel)
    from src.clips.settings import is_clips_channel
    if is_clips_channel(config):
        raise ValueError(f"'{channel}' is a clips channel. Make its videos with: "
                         f"python -m src.clips --channel {channel} run")

    stories, reason = stories_from_job(source_job, channel)
    if reason is None:
        reason = "Find recent stories from this channel's configured sources."

    return render_page(request, "new.html", {
        "channels": channels,
        "channel": channel,
        "config": config,
        "stories": stories,
        "reason": reason,
        "settings": job_settings(),
        "submission_id": new_submission_id(),
        "script_estimate": jobs.estimate_job(config, "generate", {"render": False}),
        "free_estimate": jobs.estimate_job(config, "generate", {"render": True, "free_voice": True}),
        "paid_estimate": jobs.estimate_job(config, "generate", {"render": True, "free_voice": False}),
        "ranking_estimate": jobs.estimate_job(config, "discover", {"rank": True}),
    })


@router.post("/new/discover")
def find_stories(request: Request, channel: str = Form(...), topic: str = Form(""),
                 rank: bool = Form(False), dry_run: bool = Form(False),
                 submission_id: str = Form(""), form_token: str = Form("")):
    """Start a background job that finds current stories for a channel.

    Inputs:
        request: the incoming browser request.
        channel: which channel to find stories for.
        topic: optional words such as "NVIDIA". Stories must mention them,
            and web news is searched too when the channel allows it.
        rank: True to let the cheap AI model put the best stories first
            (costs a little money).
        dry_run: True to practise without any paid AI call.
        submission_id: a one-time number so a double click starts one job.
        form_token: the secret token that proves the form came from our page.

    Returns:
        A redirect to the new job's progress page.
    """
    require_form_token(request, form_token)
    payload = {"topic": topic[:200], "rank": rank}
    return queue(channel, "discover", payload, dry_run, submission_id)


@router.post("/new/generate")
def create_video(request: Request, channel: str = Form(...), story_id: int = Form(0),
                 url: str = Form(""), title: str = Form(""), render: bool = Form(False),
                 free_voice: bool = Form(True), dry_run: bool = Form(False),
                 submission_id: str = Form(""), form_token: str = Form("")):
    """
    Start a job that writes a sourced script (and optionally renders it).

    Exactly one source must be given: a found story OR a pasted link.
    The result is a draft for review; nothing is ever published.
    """
    require_form_token(request, form_token)
    url = url.strip()
    has_story = bool(story_id)
    has_url = bool(url)
    if has_story == has_url:
        raise ValueError("Choose one story or enter one article URL.")

    if has_url:
        check_article_url(url)
    if has_story:
        # Imported here: worker.py imports the AI client, which the
        # rest of this file doesn't need.
        from src.dashboard.worker import saved_story
        saved_story(channel, story_id)   # raises if the story doesn't exist

    payload = {"story_id": story_id, "url": url, "title": title[:300],
               "render": render, "free_voice": free_voice, "animated": True}
    return queue(channel, "generate", payload, dry_run, submission_id)


@router.post("/video/{video_id}/render")
def render_video(request: Request, video_id: int, free_voice: bool = Form(True),
                 dry_run: bool = Form(False), submission_id: str = Form(""),
                 form_token: str = Form("")):
    """Start a background job that turns a saved script into an animated video.

    Inputs:
        request: the incoming browser request.
        video_id: the video's number in the database.
        free_voice: True for the free voice, False for the paid voice.
        dry_run: True to practise without any paid call.
        submission_id: a one-time number so a double click starts one job.
        form_token: the secret token that proves the form came from our page.

    Returns:
        A redirect to the new job's progress page. Refuses videos that do
        not exist or that were already posted.
    """
    require_form_token(request, form_token)
    video = get_video_by_id(video_id)
    if not video:
        raise HTTPException(404, "Video not found")
    if video["status"] == "posted":
        raise ValueError("Create a new draft instead of rendering over a posted video.")
    payload = {"video_id": video_id, "free_voice": free_voice, "animated": True}
    return queue(video["channel"], "render", payload, dry_run, submission_id)


@router.post("/video/{video_id}/clip")
def recut_clip(request: Request, video_id: int, start: float = Form(...), end: float = Form(...),
               hook_title: str = Form(...), submission_id: str = Form(""),
               form_token: str = Form("")):
    """Start a job that re-cuts a clip video with a new moment or hook title.

    Inputs:
        request: the incoming browser request.
        video_id: the clip video's number in the database.
        start: where the moment starts in the original clip, in seconds.
        end: where it ends, in seconds.
        hook_title: the title shown during the first seconds.
        submission_id: a one-time number so a double click starts one job.
        form_token: the secret token that proves the form came from our page.

    Returns:
        A redirect to the job's progress page. The values are checked
        here first, so a mistake is shown at once instead of in the job.
    """
    from src.clips.maker import check_new_moment
    from src.clips.settings import load_clip_settings
    from src.clips.store import get_clip_for_video

    require_form_token(request, form_token)
    video = get_video_by_id(video_id)
    if not video:
        raise HTTPException(404, "Video not found")
    clip = get_clip_for_video(video_id)
    if clip is None:
        raise ValueError("This video was not made from a stream clip.")
    if video["status"] == "posted":
        raise ValueError("Create a new draft instead of changing a posted video.")
    settings = load_clip_settings(channel_config(video["channel"]))
    moment = check_new_moment(clip, start, end, hook_title, settings)
    payload = {"video_id": video_id, "start": moment["start"], "end": moment["end"],
               "hook_title": moment["hook_title"]}
    return queue(video["channel"], "clip_render", payload, False, submission_id)


@router.post("/video/{video_id}/publish")
def publish_now(request: Request, video_id: int, confirmed: bool = Form(False),
                dry_run: bool = Form(False), submission_id: str = Form(""),
                form_token: str = Form("")):
    """Start a background job that posts an approved video to its channel's platforms.

    Inputs:
        request: the incoming browser request.
        video_id: the video's number in the database.
        confirmed: the "I understand this goes public" tick box.
        dry_run: True to see where it would post without sending anything.
        submission_id: a one-time number so a double click starts one job.
        form_token: the secret token that proves the form came from our page.

    Returns:
        A redirect to the job's progress page. Refuses videos that are not
        approved or not rendered yet.
    """
    require_form_token(request, form_token)
    video = get_video_by_id(video_id)
    if not video:
        raise HTTPException(404, "Video not found")
    if video["status"] != "approved":
        raise ValueError("Only approved videos can be posted.")
    if not video["video_path"]:
        raise ValueError("Render this video before posting it.")
    if not confirmed and not dry_run:
        raise ValueError("Tick the box to confirm that Instagram and Facebook posts go public immediately.")
    return queue(video["channel"], "publish", {"video_id": video_id}, dry_run, submission_id)


# ---------------------------------------------------------------------------
# Recording a post you published yourself
# ---------------------------------------------------------------------------

def check_public_post_form(platform, post_id, confirmed):
    """
    Refuse an invalid "I published this" form.

    Args:
        platform (str): "tiktok", "instagram" or "youtube".
        post_id (str): The public post's ID (numeric, or YouTube's 11-character video ID).
        confirmed (bool): The "I have already published this" tick box.

    Raises:
        ValueError: If anything is missing or wrong.
    """
    post_id = post_id.strip()
    # YouTube video IDs are letters, digits, "-" and "_" (e.g. "dQw4w9WgXcQ");
    # TikTok and Instagram IDs are digits only.
    if platform == "youtube":
        valid_id = re.fullmatch(r"[A-Za-z0-9_-]{11}", post_id) is not None
    else:
        valid_id = post_id.isdigit() and len(post_id) <= 40
    if platform not in PLATFORMS or not valid_id or not confirmed:
        raise ValueError("Enter the public post ID and confirm it is already published. "
                         "An inbox upload ID does not qualify.")


def check_video_may_be_marked_posted(connection, video_id):
    """
    Make sure a video is reviewed and idle before it is recorded as posted.

    Only approved videos (or videos already posted on another platform)
    may be recorded, so the database never shows an unreviewed video as
    published.

    Args:
        connection: An open SQLite connection (inside a transaction).
        video_id (int): The video.

    Raises:
        ValueError: If a job is still working on it, or it isn't approved.
    """
    active = connection.execute(
        "SELECT 1 FROM jobs WHERE video_id = ? AND status IN ('queued', 'running')", (video_id,)
    ).fetchone()
    if active:
        raise ValueError("Wait for this video's job to finish before recording its public post.")

    video = connection.execute("SELECT status FROM videos WHERE id = ?", (video_id,)).fetchone()
    if video["status"] not in ("approved", "posted"):
        raise ValueError("Approve this video before recording that it was published.")


@router.post("/video/{video_id}/published")
def record_public_post(request: Request, video_id: int, platform: str = Form(...),
                       post_id: str = Form(...), confirmed: bool = Form(False),
                       form_token: str = Form("")):
    """
    Record that you published an approved video yourself, with its public post ID.

    Nothing is uploaded. The post ID lets the statistics collector find
    the post's views later.
    """
    require_form_token(request, form_token)
    if not get_video_by_id(video_id):
        raise HTTPException(404, "Video not found")
    check_public_post_form(platform, post_id, confirmed)

    now = utc_now()
    connection = get_connection()
    try:
        connection.execute("BEGIN IMMEDIATE")
        check_video_may_be_marked_posted(connection, video_id)
        connection.execute("""
            INSERT INTO platform_posts (video_id, platform, post_id, confirmed_at) VALUES (?, ?, ?, ?)
            ON CONFLICT(video_id, platform) DO UPDATE SET
                post_id = excluded.post_id, confirmed_at = excluded.confirmed_at
        """, (video_id, platform, post_id.strip(), now))
        # COALESCE keeps the first posting time if the video was already posted elsewhere.
        connection.execute(
            "UPDATE videos SET status = 'posted', posted_at = COALESCE(posted_at, ?) WHERE id = ?",
            (now, video_id),
        )
        connection.commit()
    finally:
        connection.close()
    return redirect(f"/video/{video_id}")


# ---------------------------------------------------------------------------
# Job progress
# ---------------------------------------------------------------------------

@router.get("/jobs")
def jobs_page(request: Request):
    """List the most recent background jobs.

    Jobs whose worker stopped sending heartbeats are marked as interrupted
    first, so the list never shows a dead job as still running.

    Inputs:
        request: the incoming browser request.

    Returns:
        The rendered jobs page.
    """
    jobs.recover_stale_jobs()
    return render_page(request, "jobs.html", {"jobs": jobs.recent_jobs()})


@router.get("/jobs/{job_id}")
def job_page(request: Request, job_id: str):
    """Show one background job's progress, result or error.

    Inputs:
        request: the incoming browser request.
        job_id: the job's id, from the address, for example /jobs/abc123.

    Returns:
        The rendered job page, or a "not found" error (404).
    """
    jobs.recover_stale_jobs()
    job = jobs.job_record(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return render_page(request, "job.html", {"job": job})


@router.get("/jobs/{job_id}/status")
def job_status(job_id: str):
    """
    Return a job's status as JSON, for the progress bar to check every few seconds.

    Only the status, progress and stage are sent — never the job's options.
    """
    jobs.recover_stale_jobs()
    job = jobs.job_record(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return JSONResponse({"status": job["status"], "progress": job["progress"], "stage": job["stage"]})

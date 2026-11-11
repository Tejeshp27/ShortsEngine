# =============================================================
# Control-center pages and form actions. Login is enforced once by
# app.py middleware; every mutation also checks the hidden form
# token. Slow work is saved as a job and run outside the web server.
# =============================================================

import secrets
import time
from datetime import date
from urllib.parse import urlencode, urlsplit

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response

from src.dashboard.app import render_page, require_form_token
from src.dashboard import accounts, data, jobs, media
from src.dashboard.settings import channel_config, job_settings, list_channels
from src.storage.database import get_connection
from src.storage.finance import add_money_entry, money_summary, utc_now
from src.storage.queries import get_video_by_id

router = APIRouter()


def redirect(path):
    """Return a redirect to local path after a successful form submission."""
    return RedirectResponse(path, status_code=303)


def queue(channel, kind, payload, dry_run, submission_id):
    """Save channel work and start a new worker once; return its progress redirect."""
    job_id, created = jobs.enqueue_job(channel, kind, payload, dry_run, submission_id)
    if created:
        jobs.launch_job(job_id)
    return redirect(f"/jobs/{job_id}")


@router.get("/channels/{channel}")
def channel_page(request: Request, channel: str):
    """Show channel's accounts, totals and best/recent videos to request's user."""
    config = channel_config(channel)
    cards = data.overview()["channel_cards"]
    card = None
    for candidate in cards:
        if candidate["key"] == channel:
            card = candidate
    videos = data.video_rows(channel)
    from operator import itemgetter
    measured = []
    for video in videos:
        if video["views"] is not None:
            measured.append(video)
    measured.sort(key=itemgetter("views"), reverse=True)
    return render_page(request, "channel.html", {"channel": channel, "config": config,
        "card": card, "accounts": data.account_rows(channel), "recent_videos": videos[:8],
        "best_videos": measured[:5], "followers_chart": data.history_chart("followers", channel),
        "views_chart": data.history_chart("views", channel), "submission_id": secrets.token_urlsafe(24)})


@router.post("/channels/{channel}/sync")
def sync_accounts(request: Request, channel: str, form_token: str = Form(""), submission_id: str = Form("")):
    """Queue channel's daily statistics after checking request's form token."""
    require_form_token(request, form_token)
    return queue(channel, "sync", {}, False, submission_id)


@router.post("/channels/{channel}/accounts/{platform}/token")
def connect_token(request: Request, channel: str, platform: str,
                  access_token: str = Form(""), account_id: str = Form(""),
                  use_environment: bool = Form(False), form_token: str = Form("")):
    """Verify and encrypt a submitted or explicitly imported platform token."""
    require_form_token(request, form_token)
    channel_config(channel)
    if platform not in ("tiktok", "instagram"):
        raise ValueError("Choose TikTok or Instagram.")
    if use_environment:
        access_token = accounts.required_env(platform.upper() + "_ACCESS_TOKEN")
        if platform == "instagram":
            account_id = accounts.required_env("INSTAGRAM_ACCOUNT_ID")
    if not access_token.strip() or len(access_token) > 10000:
        raise ValueError("Enter a valid access token.")
    profile = accounts.fetch_profile(platform, access_token.strip(), account_id.strip())
    accounts.save_connection(channel, platform, profile, {"access_token": access_token.strip()})
    return redirect(f"/channels/{channel}")


@router.post("/channels/{channel}/accounts/{platform}/disconnect")
def disconnect_account(request: Request, channel: str, platform: str, form_token: str = Form("")):
    """Remove channel/platform's local token and follower snapshots after CSRF validation."""
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
    """Start official platform login for channel, binding it to this browser."""
    require_form_token(request, form_token)
    channel_config(channel)
    if platform == "instagram" and not account_id.strip().isdigit():
        raise ValueError("Enter the Instagram professional account ID before connecting.")
    state = secrets.token_urlsafe(32)
    url = accounts.authorization_url(platform, state)
    connection = get_connection()
    try:
        connection.execute("DELETE FROM oauth_states WHERE created_at < ?", (time.time() - 600,))
        connection.execute("INSERT INTO oauth_states VALUES (?, ?, ?, ?, ?, ?)",
                           (state, channel, platform, account_id.strip(), form_token, time.time()))
        connection.commit()
    finally:
        connection.close()
    return redirect(url)


@router.get("/oauth/{platform}/callback")
def oauth_callback(request: Request, platform: str, state: str = "", code: str = "", error: str = ""):
    """Consume one valid OAuth state for request and save the verified account."""
    connection = get_connection()
    try:
        connection.execute("BEGIN IMMEDIATE")
        attempt = connection.execute("SELECT * FROM oauth_states WHERE state = ?", (state,)).fetchone()
        if (not attempt or attempt["platform"] != platform or attempt["created_at"] < time.time() - 600
                or not secrets.compare_digest(attempt["session_token"], request.session.get("form_token", ""))):
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


@router.get("/money")
def money_page(request: Request, channel: str = ""):
    """Show recorded money for channel or all channels to request's user."""
    context = money_summary(channel or None)
    context.update({"channel": channel, "channels": list_channels(), "today": date.today().isoformat(),
                    "submission_id": secrets.token_urlsafe(24)})
    return render_page(request, "money.html", context)


@router.post("/money")
def save_money(request: Request, channel: str = Form(...), platform: str = Form(...),
               kind: str = Form(...), amount: str = Form(...), entry_date: str = Form(...),
               note: str = Form(""), submission_id: str = Form(""), form_token: str = Form("")):
    """Validate request's payout/expense fields, save once, and return to Money."""
    require_form_token(request, form_token)
    channel_config(channel)
    add_money_entry(channel, platform, kind, amount, entry_date, note, submission_id)
    return redirect("/money?" + urlencode({"channel": channel}))


@router.post("/money/{entry_id}/delete")
def delete_money(request: Request, entry_id: int, form_token: str = Form("")):
    """Delete manual entry_id after checking request's form token; redirect back."""
    require_form_token(request, form_token)
    connection = get_connection()
    try:
        connection.execute("DELETE FROM money_entries WHERE id = ?", (entry_id,))
        connection.commit()
    finally:
        connection.close()
    return redirect("/money")


@router.get("/library")
def library_page(request: Request, channel: str = "", status: str = "", q: str = "", page: int = 1):
    """Show request's private library filtered by channel, status, title q and page."""
    context = media.library(channel, status, q[:200], page)
    context.update({"channel": channel, "status": status, "q": q[:200], "channels": list_channels(),
                    "filter_query": urlencode({"channel": channel, "status": status, "q": q[:200]})})
    return render_page(request, "library.html", context)


@router.get("/library/untracked")
def untracked_video(request: Request, path: str):
    """Show a private player for untracked MP4 path inside output."""
    video = media.requested_video(path)
    if video is None:
        raise HTTPException(404, "Video not found")
    return render_page(request, "untracked.html", {"relative": path, "title": video.stem.replace("-", " ")})


@router.get("/media/video/{path:path}")
def video_file(path: str):
    """Return private MP4 path with range support; login middleware runs first."""
    video = media.requested_video(path)
    if video is None:
        raise HTTPException(404, "Video not found")
    return FileResponse(video, media_type="video/mp4")


@router.get("/media/thumbnail/{path:path}")
def video_thumbnail(path: str):
    """Return a cached preview of private MP4 path, or an empty image response."""
    video = media.requested_video(path)
    if video is None:
        raise HTTPException(404, "Video not found")
    preview = media.thumbnail(video)
    if preview is None:
        return Response(status_code=204)
    return FileResponse(preview, media_type="image/jpeg")


@router.get("/new")
def new_video_page(request: Request, channel: str = "", source_job: str = ""):
    """Show channel/source choices and spending allowances to request's user."""
    channels = list_channels()
    if not channel and channels:
        channel = channels[0]["key"]
    config = channel_config(channel)
    stories = []
    reason = "Find recent stories from this channel's configured sources."
    if source_job:
        job = jobs.job_record(source_job)
        if job and job["channel"] == channel:
            from src.dashboard.worker import saved_story
            for story_id in job["result"].get("story_ids", []):
                stories.append(saved_story(channel, story_id))
            reason = job["result"].get("reason", reason)
    return render_page(request, "new.html", {"channels": channels, "channel": channel, "config": config,
        "stories": stories, "reason": reason, "settings": job_settings(), "submission_id": secrets.token_urlsafe(24),
        "script_estimate": jobs.estimate_job(config, "generate", {"render": False}),
        "free_estimate": jobs.estimate_job(config, "generate", {"render": True, "free_voice": True}),
        "paid_estimate": jobs.estimate_job(config, "generate", {"render": True, "free_voice": False}),
        "ranking_estimate": jobs.estimate_job(config, "discover", {"rank": True})})


@router.post("/new/discover")
def find_stories(request: Request, channel: str = Form(...), topic: str = Form(""),
                 rank: bool = Form(False), dry_run: bool = Form(False), submission_id: str = Form(""),
                 form_token: str = Form("")):
    """Queue current source discovery for channel, optionally filtering topic/ranking."""
    require_form_token(request, form_token)
    return queue(channel, "discover", {"topic": topic[:200], "rank": rank}, dry_run, submission_id)


@router.post("/new/generate")
def create_video(request: Request, channel: str = Form(...), story_id: int = Form(0),
                 url: str = Form(""), title: str = Form(""), render: bool = Form(False),
                 free_voice: bool = Form(True), dry_run: bool = Form(False),
                 submission_id: str = Form(""), form_token: str = Form("")):
    """Queue a sourced script/video from saved story_id or URL; never publish it."""
    require_form_token(request, form_token)
    if bool(story_id) == bool(url.strip()):
        raise ValueError("Choose one story or enter one article URL.")
    if url:
        parts = urlsplit(url.strip())
        if parts.scheme not in ("http", "https") or not parts.hostname or parts.username or len(url) > 2048:
            raise ValueError("Enter an ordinary public article URL.")
    if story_id:
        from src.dashboard.worker import saved_story
        saved_story(channel, story_id)
    return queue(channel, "generate", {"story_id": story_id, "url": url.strip(), "title": title[:300],
                 "render": render, "free_voice": free_voice, "animated": True}, dry_run, submission_id)


@router.post("/video/{video_id}/render")
def render_video(request: Request, video_id: int, free_voice: bool = Form(True),
                 dry_run: bool = Form(False), submission_id: str = Form(""), form_token: str = Form("")):
    """Queue a render of video_id, with optional free voice/dry run, after CSRF check."""
    require_form_token(request, form_token)
    video = get_video_by_id(video_id)
    if not video:
        raise HTTPException(404, "Video not found")
    if video["status"] == "posted":
        raise ValueError("Create a new draft instead of rendering over a posted video.")
    return queue(video["channel"], "render", {"video_id": video_id, "free_voice": free_voice,
                  "animated": True}, dry_run, submission_id)


@router.post("/video/{video_id}/published")
def record_public_post(request: Request, video_id: int, platform: str = Form(...),
                       post_id: str = Form(...), confirmed: bool = Form(False), form_token: str = Form("")):
    """Record the user's confirmation of an already-public post; send no upload."""
    require_form_token(request, form_token)
    video = get_video_by_id(video_id)
    if not video:
        raise HTTPException(404, "Video not found")
    if platform not in ("tiktok", "instagram") or not post_id.strip().isdigit() or len(post_id) > 40 or not confirmed:
        raise ValueError("Enter the numeric public post ID and confirm it is already published. An inbox upload ID does not qualify.")
    connection = get_connection()
    try:
        connection.execute("BEGIN IMMEDIATE")
        active = connection.execute("SELECT 1 FROM jobs WHERE video_id=? AND status IN ('queued', 'running')", (video_id,)).fetchone()
        if active:
            raise ValueError("Wait for this video's job to finish before recording its public post.")
        connection.execute("""
            INSERT INTO platform_posts (video_id, platform, post_id, confirmed_at) VALUES (?, ?, ?, ?)
            ON CONFLICT(video_id, platform) DO UPDATE SET post_id=excluded.post_id, confirmed_at=excluded.confirmed_at
        """, (video_id, platform, post_id.strip(), utc_now()))
        connection.execute("UPDATE videos SET status='posted', posted_at=COALESCE(posted_at, ?) WHERE id=?",
                           (utc_now(), video_id))
        connection.commit()
    finally:
        connection.close()
    return redirect(f"/video/{video_id}")


@router.get("/jobs")
def jobs_page(request: Request):
    """Show request's recent background jobs, recovering interrupted workers first."""
    jobs.recover_stale_jobs()
    return render_page(request, "jobs.html", {"jobs": jobs.recent_jobs()})


@router.get("/jobs/{job_id}")
def job_page(request: Request, job_id: str):
    """Show request's progress page for saved job_id, or return 404."""
    jobs.recover_stale_jobs()
    job = jobs.job_record(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return render_page(request, "job.html", {"job": job})


@router.get("/jobs/{job_id}/status")
def job_status(job_id: str):
    """Return safe polling data for job_id; login middleware protects this URL."""
    jobs.recover_stale_jobs()
    job = jobs.job_record(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return JSONResponse({"status": job["status"], "progress": job["progress"], "stage": job["stage"]})

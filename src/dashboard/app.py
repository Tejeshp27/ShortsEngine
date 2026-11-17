# =============================================================
# src/dashboard/app.py — the private web dashboard
#
# Runs a web app so you can manage ShortsEngine from a browser:
#   /login        → log in (the only page that works logged out)
#   /             → overview (videos made, cost, status)
#   /channels     → list channels
#   /videos       → review queue (approve / reject in browser)
#   /video/<id>   → detail view for one video
#   /analytics    → views, likes, comments and shares
# The control-center pages (money, library, new videos, jobs,
# channel accounts) live in routes.py and are added in create_app().
#
# Privacy: every page is locked by default. A check runs before
# EVERY request, and anything that is not the login page or the
# stylesheet is refused unless the browser is logged in. A page
# added later is therefore private automatically — nobody has to
# remember to protect it. The login rules live in auth.py.
#
# First-time setup:
#   1. Add SESSION_SECRET to .env (see .env.example).
#   2. python -m src.dashboard.set_password
#
# Run with:
#   python -m src.dashboard.app
# Then open http://localhost:8000 in your browser.
# =============================================================

import json
import os
import secrets
import sqlite3
import threading
from contextlib import asynccontextmanager

import uvicorn
from dotenv import load_dotenv
from fastapi import APIRouter, FastAPI, Form, HTTPException, Request
from fastapi.responses import PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware

from src.analytics.collector import get_metrics_summary
from src.dashboard.accounts import ACCOUNT_PLATFORMS
from src.dashboard.auth import (
    check_form_token,
    get_logged_in_user,
    is_locked_out,
    load_login_settings,
    note_failed_login,
    note_successful_login,
    start_session,
    verify_login,
)
from src.storage.database import create_tables
from src.storage.queries import (
    approve_video,
    get_all_videos,
    get_video_by_id,
    get_videos_by_status,
    reject_video,
)
from src.storage.users import count_users
from src.publishing.scheduler import PLATFORM_NAMES


# The dashboard's own folder, so templates and the stylesheet are found
# no matter which folder the app is started from.
DASHBOARD_FOLDER = os.path.dirname(os.path.abspath(__file__))
TEMPLATES_FOLDER = os.path.join(DASHBOARD_FOLDER, "templates")
STATIC_FOLDER = os.path.join(DASHBOARD_FOLDER, "static")

# Pages that work without being logged in. Keep this list as short as
# possible: everything not listed here is private.
PUBLIC_PATHS = ["/login"]

# Folders whose files are served without a login. Only public styles and
# browser JavaScript live here — never put anything private in static/.
PUBLIC_PREFIXES = ["/static/"]

# The session secret must be long enough that it cannot be guessed.
MINIMUM_SECRET_LENGTH = 32

# The statuses a video can have, in the order the review page lists them.
VIDEO_STATUSES = ["drafted", "approved", "posted", "failed"]

# Jinja2 fills in the HTML templates. It is the standard template
# engine for FastAPI, and it escapes text automatically, so a story
# title containing HTML is shown as plain text instead of being run.
templates = Jinja2Templates(directory=TEMPLATES_FOLDER)
# Platform lists every page may use, so templates never hard-code them.
templates.env.globals["account_platforms"] = ACCOUNT_PLATFORMS
templates.env.globals["platform_names"] = PLATFORM_NAMES

# A router collects the page functions below. create_app() attaches
# them to the app.
router = APIRouter()


@asynccontextmanager
async def app_lifespan(app):
    """
    Run start-up and shut-down work for the dashboard.

    On start-up, one background thread begins collecting daily follower
    and view statistics (unless DASHBOARD_AUTO_COLLECT=false) and another
    checks channel posting schedules (unless DASHBOARD_AUTO_PUBLISH=false).
    On shut-down, both threads are told to stop.

    FastAPI requires this "lifespan" pattern: everything before "yield"
    runs at start-up, everything after it runs at shut-down. The
    @asynccontextmanager decorator is what makes that pattern work.

    Args:
        app (FastAPI): The app (FastAPI passes it in; not used here).
    """
    from src.analytics.accounts import daily_collection_loop
    from src.publishing.scheduler import publishing_loop
    stopped = threading.Event()
    if os.getenv("DASHBOARD_AUTO_COLLECT", "true").lower() == "true":
        thread = threading.Thread(target=daily_collection_loop, args=(stopped,), daemon=True)
        thread.start()
    # Scheduled posting only does anything for channels with a schedule: block.
    if os.getenv("DASHBOARD_AUTO_PUBLISH", "true").lower() == "true":
        thread = threading.Thread(target=publishing_loop, args=(stopped,), daemon=True)
        thread.start()
    try:
        yield
    finally:
        stopped.set()


# ---------------------------------------------------------------------------
# The login check that runs before every request
# ---------------------------------------------------------------------------

def is_public_path(path):
    """
    Decide whether a page may be seen without logging in.

    Args:
        path (str): The path part of the address, e.g. "/videos".

    Returns:
        bool: True only for the login page and the stylesheet folder.

    Example:
        is_public_path("/login")    # → True
        is_public_path("/videos")   # → False
    """
    if path in PUBLIC_PATHS:
        return True
    for prefix in PUBLIC_PREFIXES:
        if path.startswith(prefix):
            return True
    return False


def add_privacy_headers(response):
    """
    Add instructions that tell the browser to treat a page as private.

    Args:
        response: The response about to be sent to the browser.

    Returns:
        The same response, with the extra headers set.
    """
    # Don't keep copies of private pages on disk or in shared caches.
    response.headers["Cache-Control"] = "no-store"
    # Don't allow the dashboard to be shown inside another website's frame.
    response.headers["X-Frame-Options"] = "DENY"
    # Don't guess file types (stops a text file being run as a script).
    response.headers["X-Content-Type-Options"] = "nosniff"
    # Don't tell other sites which dashboard page a link was clicked on.
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


async def require_login(request, call_next):
    """
    Check every request before it reaches a page: logged in, or refused.

    This is "middleware": code that sits between the browser and the
    page functions and sees every single request. Doing the check here,
    in one place, is what makes the whole dashboard private by default.

    (FastAPI requires middleware to be written with "async" and "await".
    "await call_next(request)" simply means: now run the real page and
    wait for its answer.)

    Args:
        request (Request): The incoming request.
        call_next: A function that runs the real page for this request.

    Returns:
        The page's response if allowed, otherwise a redirect to the
        login page (or a "401 Not logged in" for form submissions).
    """
    path = request.url.path

    if is_public_path(path):
        response = await call_next(request)
        # The stylesheet may be cached by the browser; the login page may not.
        if path in PUBLIC_PATHS:
            add_privacy_headers(response)
        return response

    username = get_logged_in_user(request.session)

    if username is None:
        if request.method == "GET":
            return RedirectResponse(url="/login", status_code=303)
        return PlainTextResponse("Not logged in.", status_code=401)

    # Remember who is logged in so the page can show their name.
    request.state.username = username
    response = await call_next(request)
    return add_privacy_headers(response)


# ---------------------------------------------------------------------------
# Small helpers used by the pages
# ---------------------------------------------------------------------------

def render_page(request, template_name, context=None, status_code=200):
    """
    Fill in an HTML template and return it as a web page.

    Every page automatically receives two extra values: "username" (who
    is logged in, for the top bar) and "form_token" (the secret value
    each form sends back; see auth.check_form_token).

    Args:
        request (Request): The current request.
        template_name (str): The template file, e.g. "home.html".
        context (dict or None): The values the template needs.
        status_code (int): The HTTP status, e.g. 200 (OK) or 401.

    Returns:
        The finished page, ready to send to the browser.
    """
    page_context = {
        "username": getattr(request.state, "username", None),
        "form_token": request.session.get("form_token", ""),
    }
    if context:
        page_context.update(context)
    return templates.TemplateResponse(
        request, template_name, page_context, status_code=status_code
    )


def require_form_token(request, form_token):
    """
    Refuse a form submission that did not come from one of our pages.

    Args:
        request (Request): The current request.
        form_token (str): The hidden token that arrived with the form.

    Raises:
        HTTPException 403: If the token is missing or wrong.
    """
    if not check_form_token(request.session, form_token):
        raise HTTPException(
            status_code=403,
            detail="This form has expired. Go back, reload the page and try again.",
        )


def get_client_address(request):
    """
    Get the network address a request came from.

    Args:
        request (Request): The current request.

    Returns:
        str: The address, e.g. "192.168.1.20", or "unknown".
    """
    if request.client is None:
        return "unknown"
    return request.client.host


def read_text_file(path):
    """
    Read a text file, or return "" if it does not exist.

    Args:
        path (str or None): The file to read.

    Returns:
        str: The file's contents, or "" if there is no such file.
    """
    from src.dashboard.media import output_file
    safe_path = output_file(path, ".md")
    if safe_path is None:
        return ""
    with open(safe_path, "r", encoding="utf-8") as f:
        return f.read()


def load_visual_plan(script_path):
    """
    Load the saved visual plan that belongs to a script, if there is one.

    Args:
        script_path (str or None): Path to the script .md file.

    Returns:
        list[dict]: One dict per segment with "segment", "visual_type"
                    and "query". Empty if there is no plan.
    """
    if not script_path:
        return []
    plan_path = os.path.splitext(script_path)[0] + "_visuals.json"
    from src.dashboard.media import output_file
    plan_path = output_file(plan_path, ".json")
    if plan_path is None:
        return []

    try:
        with open(plan_path, "r", encoding="utf-8") as f:
            plan = json.load(f)
    except (ValueError, OSError):
        return []
    if not isinstance(plan, list):
        return []

    rows = []
    for item in plan:
        if not isinstance(item, dict):
            continue
        rows.append({
            "segment": item.get("segment", "")[:60],
            "visual_type": item.get("visual_type", "text_card"),
            "query": item.get("query", ""),
        })
    return rows


def safe_web_link(url):
    """
    Return a web address only if it is an ordinary http(s) link.

    A saved "link" could in theory be something else, such as a
    "javascript:" address that runs code when clicked. Those are dropped.

    Args:
        url (str or None): The address saved with a video.

    Returns:
        str: The address, or "" if it is not a normal web link.
    """
    if url and (url.startswith("http://") or url.startswith("https://")):
        return url
    return ""


# ---------------------------------------------------------------------------
# Login and logout
# ---------------------------------------------------------------------------

@router.get("/login")
def login_page(request: Request):
    """Show the login form, or skip it if this browser is already logged in.

    Inputs:
        request: the incoming browser request (holds the session cookie).

    Returns:
        The login page, or a redirect to the overview page when the person
        is already logged in. If no password has been set yet, the page
        explains which terminal command creates one.
    """
    if get_logged_in_user(request.session) is not None:
        return RedirectResponse(url="/", status_code=303)

    error = ""
    if count_users() == 0:
        error = ("No login has been created yet. In the terminal, run: "
                 "python -m src.dashboard.set_password")
    return render_page(request, "login.html", {"error": error})


@router.post("/login")
def login_submit(request: Request, username: str = Form(default=""),
                 password: str = Form(default="")):
    """
    Check a submitted username and password.

    Too many wrong attempts from one address lock that address out for
    a while — even the correct password is refused until the lock ends,
    so there is no point in continuing to guess.
    """
    address = get_client_address(request)

    if is_locked_out(address):
        minutes = load_login_settings()["lockout_minutes"]
        error = f"Too many failed attempts. Try again in {minutes} minutes."
        return render_page(request, "login.html", {"error": error}, status_code=429)

    user = verify_login(username, password)
    if user is None:
        note_failed_login(address)
        # The same message for a wrong username and a wrong password, so
        # the form does not reveal which usernames exist.
        error = "Wrong username or password."
        return render_page(request, "login.html", {"error": error}, status_code=401)

    note_successful_login(address)
    start_session(request.session, user)
    return RedirectResponse(url="/", status_code=303)


@router.post("/logout")
def logout(request: Request, form_token: str = Form(default="")):
    """Log this browser out and send it back to the login page.

    Inputs:
        request: the incoming browser request (holds the session cookie).
        form_token: the secret token from the form, which proves the click
            came from our own page and not from another website.

    Returns:
        A redirect to /login after the session has been cleared.
    """
    require_form_token(request, form_token)
    request.session.clear()
    return RedirectResponse(url="/login", status_code=303)


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------

@router.get("/")
def dashboard_home(request: Request):
    """Show the overview page for all channels together.

    The page shows totals for every channel, follower and view history
    charts, and the four most recent background jobs.

    Inputs:
        request: the incoming browser request.

    Returns:
        The rendered home page.
    """
    # Imported here because data.py and jobs.py import from this file's
    # neighbours; importing them at the top would create an import loop.
    from src.dashboard.data import history_chart, overview
    from src.dashboard.jobs import recent_jobs, recover_stale_jobs
    recover_stale_jobs()
    context = overview()
    context["followers_chart"] = history_chart("followers")
    context["views_chart"] = history_chart("views")
    context["jobs"] = recent_jobs(4)
    return render_page(request, "home.html", context)


@router.get("/channels")
def channels_page(request: Request):
    """Show a card for every channel config found in the channels/ folder.

    Inputs:
        request: the incoming browser request.

    Returns:
        The rendered channels page.
    """
    from src.dashboard.data import overview
    return render_page(request, "channels.html", overview())


@router.get("/videos")
def videos_page(request: Request, status: str = "drafted"):
    """Show the review queue: every video that has one chosen status.

    Inputs:
        request: the incoming browser request.
        status: which videos to list, taken from the address bar, for
            example /videos?status=approved. Defaults to "drafted".

    Returns:
        The rendered review queue page.
    """
    return render_page(request, "videos.html", {
        "videos": get_videos_by_status(status),
        "status": status,
        "statuses": VIDEO_STATUSES,
    })


def posting_destinations(channel):
    """
    List the platforms an approved video of this channel would be posted to.

    Args:
        channel (str): The channel, e.g. "news".

    Returns:
        list[str]: For example ["tiktok", "instagram", "facebook"]. Empty if
                   the channel's settings can't be read.
    """
    from src.dashboard.settings import channel_config
    from src.publishing.scheduler import channel_platforms
    try:
        return channel_platforms(channel_config(channel))
    except ValueError:
        return []


def publishing_history(video_id):
    """
    Get a video's confirmed public posts and its upload attempts.

    Args:
        video_id (int): The video.

    Returns:
        tuple: (list of platform_posts rows, list of publishing_deliveries rows)
    """
    from src.storage.database import get_connection
    connection = get_connection()
    try:
        public_posts = connection.execute(
            "SELECT * FROM platform_posts WHERE video_id = ?", (video_id,)
        ).fetchall()
        deliveries = connection.execute(
            "SELECT * FROM publishing_deliveries WHERE video_id = ?", (video_id,)
        ).fetchall()
    finally:
        connection.close()
    return public_posts, deliveries


def render_estimates(channel):
    """
    Work out what re-rendering a video would cost, with each voice option.

    Args:
        channel (str): The video's channel.

    Returns:
        tuple: (free-voice allowance, channel-voice allowance) in dollars,
               or (None, None) if the channel's settings are missing.
    """
    from src.dashboard.jobs import estimate_job
    from src.dashboard.settings import channel_config
    try:
        config = channel_config(channel)
        free_voice = estimate_job(config, "render", {"free_voice": True, "animated": True})
        channel_voice = estimate_job(config, "render", {"free_voice": False, "animated": True})
    except (ValueError, KeyError):
        return None, None
    return free_voice, channel_voice


def clip_review_details(video):
    """
    Get what the review page needs for a video made from a stream clip.

    Args:
        video (dict): The video row.

    Returns:
        tuple: (clip row or None, longest allowed moment in seconds or None).
    """
    from src.clips.settings import load_clip_settings
    from src.clips.store import get_clip_for_video
    from src.dashboard.settings import channel_config

    clip = get_clip_for_video(video["id"])
    if clip is None:
        return None, None
    try:
        settings = load_clip_settings(channel_config(video["channel"]))
    except ValueError:
        return clip, None
    return clip, settings.clips.moment_max_seconds


def caption_to_paste(video, clip):
    """
    Write the caption you paste in the TikTok app (drafts carry no caption).

    Args:
        video (dict): The video row.
        clip (dict or None): Its clip row, if it was made from a stream clip.

    Returns:
        str: The caption, or "" if the channel's settings can't be read.
    """
    from src.dashboard.settings import channel_config
    try:
        config = channel_config(video["channel"])
    except ValueError:
        return ""
    if clip is not None:
        from src.clips.maker import build_post_caption
        return build_post_caption(video["story_title"], clip,
                                  config.get("publishing", {}).get("hashtags", []))
    from src.publishing.tiktok import build_caption
    return build_caption(video["story_title"], video["story_url"], config)


@router.get("/video/{video_id}")
def video_detail(request: Request, video_id: int):
    """Show everything about one video so it can be reviewed.

    The page has the video player, the script, the source link, the
    approve/reject buttons, cost estimates for rendering and the history
    of uploads and public posts. Videos made from a stream clip also get
    a form to change the moment or hook title, and every video shows
    the caption to paste into TikTok.

    Inputs:
        request: the incoming browser request.
        video_id: the video's number in the database, from the address,
            for example /video/12.

    Returns:
        The rendered video page, or a "not found" error (404).
    """
    from src.dashboard.media import relative_video

    video = get_video_by_id(video_id)
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    relative = relative_video(video["video_path"])
    public_posts, deliveries = publishing_history(video_id)
    clip, clip_max_seconds = clip_review_details(video)
    render_estimate, channel_estimate = None, None
    if clip is None:
        # Clip videos are re-cut (free) instead of re-rendered with a voice.
        render_estimate, channel_estimate = render_estimates(video["channel"])

    return render_page(request, "video_detail.html", {
        "video": video,
        "source_url": safe_web_link(video["story_url"]),
        "script_text": read_text_file(video["script_path"]),
        "has_video_file": bool(relative),
        "relative": relative,
        "public_posts": public_posts,
        "deliveries": deliveries,
        "submission_id": secrets.token_urlsafe(24),
        "render_estimate": render_estimate,
        "channel_estimate": channel_estimate,
        "visual_plan": load_visual_plan(video["script_path"]),
        "destinations": posting_destinations(video["channel"]),
        "clip": clip,
        "clip_max_seconds": clip_max_seconds,
        "post_caption": caption_to_paste(video, clip),
    })


@router.post("/video/{video_id}/approve")
def approve(request: Request, video_id: int, notes: str = Form(default=""),
            form_token: str = Form(default="")):
    """Approve a video and go back to the review queue.

    A drafted video can be approved, and so can one that was rejected
    earlier (a change of mind). Approving never publishes anything.

    Inputs:
        request: the incoming browser request.
        video_id: the video's number in the database.
        notes: optional review notes typed into the form.
        form_token: the secret token that proves the form came from our page.

    Returns:
        A redirect to the review queue. Shows an error instead if the video
        does not exist, is being rendered, or could not be changed.
    """
    require_form_token(request, form_token)
    if not get_video_by_id(video_id):
        raise HTTPException(status_code=404, detail="Video not found")
    ensure_review_available(video_id)
    # approve_video() reports False if the video could not be changed
    # (for example a job started in the meantime). Say so instead of
    # silently returning to the queue.
    if not approve_video(video_id, notes=notes):
        raise ValueError("This video could not be approved. Reload it and check its status.")
    return RedirectResponse(url="/videos", status_code=303)


@router.post("/video/{video_id}/reject")
def reject(request: Request, video_id: int, notes: str = Form(default=""),
           form_token: str = Form(default="")):
    """Reject a video and go back to the review queue.

    Inputs:
        request: the incoming browser request.
        video_id: the video's number in the database.
        notes: optional review notes, for example why it was rejected.
        form_token: the secret token that proves the form came from our page.

    Returns:
        A redirect to the review queue. Shows an error instead if the video
        does not exist, is being rendered, or could not be changed.
    """
    require_form_token(request, form_token)
    if not get_video_by_id(video_id):
        raise HTTPException(status_code=404, detail="Video not found")
    ensure_review_available(video_id)
    if not reject_video(video_id, notes=notes):
        raise ValueError("This video could not be rejected. Reload it and check its status.")
    return RedirectResponse(url="/videos", status_code=303)


def ensure_review_available(video_id):
    """
    Refuse an approve/reject decision that is not allowed right now.

    Args:
        video_id (int): The video being reviewed.

    Raises:
        ValueError: If a job is still writing/rendering the video (its
                    output isn't final yet), or it was already published.
    """
    from src.storage.database import get_connection
    connection = get_connection()
    try:
        active = connection.execute(
            "SELECT 1 FROM jobs WHERE video_id = ? AND status IN ('queued', 'running')", (video_id,)
        ).fetchone()
        posted = connection.execute(
            "SELECT 1 FROM platform_posts WHERE video_id = ?", (video_id,)
        ).fetchone()
    finally:
        connection.close()

    if active:
        raise ValueError("Wait for this video's job to finish before reviewing its output.")
    if posted or get_video_by_id(video_id)["status"] == "posted":
        raise ValueError("This video already has publishing history. Its review decision is locked.")


@router.get("/analytics")
def analytics_page(request: Request, channel: str = None):
    """
    Analytics: views, likes, comments and shares per posted video.

    Add ?channel=news to the address to show one channel only.
    """
    summary = get_metrics_summary(channel=channel)

    # The longest bar belongs to the video with the most views; the
    # others are drawn in proportion to it.
    most_views = 1
    for item in summary:
        most_views = max(most_views, item["total_views"] or 0)

    rows = []
    for item in summary:
        views = item["total_views"] or 0
        rows.append({
            "id": item["id"],
            "title": item["story_title"][:55],
            "channel": item["channel"],
            "views": views,
            "bar_width": max(1, int(views / most_views * 200)),
            "likes": item["total_likes"] or 0,
            "comments": item["total_comments"] or 0,
            "shares": item["total_shares"] or 0,
            "date": (item["created_at"] or "")[:10],
        })

    return render_page(request, "analytics.html", {"rows": rows})


# ---------------------------------------------------------------------------
# Building and starting the app
# ---------------------------------------------------------------------------

def create_app():
    """
    Build the dashboard web app, with the login check switched on.

    The app is built by a function (instead of at the top of the file)
    so that it reads SESSION_SECRET at the moment it is created. That
    lets the tests build a fresh app with their own settings.

    Returns:
        FastAPI: The app, ready to be run by uvicorn.

    Raises:
        RuntimeError: If SESSION_SECRET is missing or too short. The
                      dashboard refuses to start rather than run unprotected.

    Example:
        app = create_app()
    """
    session_secret = os.getenv("SESSION_SECRET", "")
    if len(session_secret) < MINIMUM_SECRET_LENGTH:
        raise RuntimeError(
            "Missing SESSION_SECRET in .env (it must be at least "
            f"{MINIMUM_SECRET_LENGTH} characters). Create one with:\n"
            '  python -c "import secrets; print(secrets.token_urlsafe(48))"\n'
            "and add it to .env as SESSION_SECRET=<that value>."
        )

    create_tables()
    settings = load_login_settings()

    # The automatic API documentation pages are switched off: this is a
    # private dashboard, not a public API.
    app = FastAPI(title="ShortsEngine Dashboard", lifespan=app_lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)

    # Middleware runs in the reverse of the order it is added: the LAST
    # one added sees the request FIRST. Sessions must be read before the
    # login check can look at them, so the session middleware goes last.
    app.add_middleware(BaseHTTPMiddleware, dispatch=require_login)
    app.add_middleware(
        SessionMiddleware,
        secret_key=session_secret,
        session_cookie="shortsengine_session",
        # How long a login lasts, in seconds.
        max_age=settings["session_days"] * 24 * 60 * 60,
        # "lax": the cookie is not sent when another website submits a
        # form to us, which blocks most cross-site attacks on its own.
        same_site="lax",
        # On the real server (HTTPS) set SESSION_COOKIE_SECURE=true so
        # the cookie is never sent over an unencrypted connection.
        https_only=os.getenv("SESSION_COOKIE_SECURE", "false").lower() == "true",
    )

    app.mount("/static", StaticFiles(directory=STATIC_FOLDER), name="static")
    app.include_router(router)
    from src.dashboard.routes import router as control_router
    app.include_router(control_router)
    app.add_exception_handler(ValueError, input_error_page)
    app.add_exception_handler(sqlite3.IntegrityError, database_conflict_page)
    return app


async def input_error_page(request, error):
    """
    Show a friendly error page when a page raises ValueError.

    Pages raise ValueError for problems the user can fix (a bad amount,
    a job already running...). This turns the message into a page
    instead of a crash.

    Args:
        request (Request): The current request.
        error (ValueError): The problem.

    Returns:
        The error page, with status 400 ("bad request").
    """
    from src.dashboard.accounts import safe_error
    return render_page(request, "error.html", {"message": safe_error(error)}, status_code=400)


async def database_conflict_page(request, error):
    """
    Show a friendly page when saving would break a database "unique" rule.

    The raw database message is not shown, because it can include SQL
    or values from the form.

    Args:
        request (Request): The current request.
        error (sqlite3.IntegrityError): The database error.

    Returns:
        The error page, with status 409 ("conflict").
    """
    return render_page(request, "error.html", {
        "message": "This record is already connected elsewhere or conflicts with saved data. Check the existing entry first."
    }, status_code=409)


def main():
    """
    Start the dashboard web server.

    By default it only listens on this computer (127.0.0.1), so other
    devices on your Wi-Fi cannot reach it. Set DASHBOARD_HOST=0.0.0.0
    (as the Docker setup does) to accept connections from outside.

    Example:
        python -m src.dashboard.app
    """
    load_dotenv()

    try:
        app = create_app()
    except RuntimeError as error:
        print(f"ERROR: {error}")
        raise SystemExit(1)

    if count_users() == 0:
        print("NOTE: No login exists yet. Create one with:")
        print("  python -m src.dashboard.set_password")

    host = os.getenv("DASHBOARD_HOST", "127.0.0.1")
    port = int(os.getenv("DASHBOARD_PORT", "8000"))
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()

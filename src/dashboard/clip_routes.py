# =============================================================
# Private streamer management, recording ingestion and automation UI.
# Existing login middleware protects every route; every write checks
# the same CSRF form token as the rest of the control center.
# =============================================================

import json
import re
import secrets
import shutil
import subprocess
from pathlib import Path

import yaml
from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool

from src.clips.automation import set_enabled
from src.clips.settings import find_approved_streamer, load_clip_settings
from src.dashboard.app import render_page, require_form_token
from src.dashboard.routes import queue, redirect
from src.dashboard.settings import channel_config, list_channels
from src.storage.database import get_connection

router = APIRouter()


@router.get("/clips/{channel}/sources")
def raw_sources_page(request: Request, channel: str, page: int = Query(1, ge=1, le=1000000), streamer: str = ""):
    """
    Read a private source-library page with upload controls and optional creator filtering.

    Args:
        request: The authenticated browser request.
        channel: The clips channel to inspect.
        page: One-based source page, bounded to prevent invalid database offsets.
        streamer: Optional creator login selected in the page filter.

    Returns:
        A rendered library showing acquisition states and matching discovery-upload choices.
    """
    from src.clips.acquisition import source_library
    config = clip_config(channel)
    return render_page(request, "clip_sources.html", {
        "channel":channel, "config":config, "settings":load_clip_settings(config),
        **source_library(channel, config, page, streamer), "submission_id":secrets.token_urlsafe(24),
    })


@router.get("/clips/{channel}/sources/{kind}/{source_id}/file")
def raw_source_file(channel: str, kind: str, source_id: int):
    """Serve one channel's permitted stored source by kind/ID; return private ranged video playback or a 404."""
    import mimetypes
    from src.clips.acquisition import local_source
    from src.clips.settings import StreamerNotApproved
    try:
        path = local_source(channel, clip_config(channel), kind, source_id)
    except (ValueError, StreamerNotApproved):
        path = None
    if path is None:
        raise HTTPException(404, "This source has no accessible local video.")
    return FileResponse(path, media_type=mimetypes.guess_type(path.name)[0] or "application/octet-stream",
                        headers={"Cache-Control":"no-store"})


async def store_source_upload(file, config):
    """
    Save one browser-uploaded source under a generated staging filename.

    Args:
        file: FastAPI's uploaded file, never trusted as a filesystem path.
        config: Channel settings containing the bounded upload-size limit.

    Returns:
        A completed staging path for the existing importer; removes fragments on failure.
    """
    from src.clips.finder import UPLOAD_EXTENSIONS
    extension = Path(file.filename or "").suffix.lower()
    if extension not in UPLOAD_EXTENSIONS:
        raise ValueError("Choose an MP4, MOV, MKV or WebM source video.")
    maximum = max(1, min(32768, int(config.get("acquisition", {}).get("max_upload_mb", 2048)))) * 1024 ** 2
    folder = Path("cache/clips/incoming")
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (secrets.token_hex(16) + extension)
    partial = path.with_suffix(extension + ".part")
    size = 0
    try:
        with partial.open("xb") as destination:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > maximum:
                    raise ValueError("Source exceeds this channel's browser upload limit.")
                if shutil.disk_usage(folder).free < len(chunk) + 5 * 1024 ** 3:
                    raise ValueError("Keep at least 5 GB free before importing source footage.")
                destination.write(chunk)
        if not size:
            raise ValueError("The source file is empty.")
        partial.replace(path)
        return path
    except Exception:
        partial.unlink(missing_ok=True)
        raise


@router.post("/clips/{channel}/sources/upload")
async def upload_raw_source(request: Request, channel: str, file: UploadFile = File(...),
                            username: str = Form(...), platform: str = Form(...),
                            confirmed: bool = Form(False), form_token: str = Form(""),
                            source_id: str = Form("")):
    """
    Import a permission-confirmed browser video for a creator into the existing raw cache; return to its source library.

    Args:
        request: The authenticated browser request.
        channel: The target clips channel.
        file: The uploaded completed source video.
        username: Its approved creator login, used for attribution.
        platform: twitch or kick, matching that creator.
        confirmed: The user's confirmation that the file is complete and authorized.
        form_token: The existing session's CSRF token.
        source_id: Optional existing discovery ID selected for this upload; empty imports an unlisted recording.

    Returns:
        A redirect after fingerprinting/import. No analysis, rendering or publishing job is launched here.
    """
    from src.clips.recordings import import_recording
    from src.clips.settings import StreamerNotApproved
    require_form_token(request, form_token)
    config = clip_config(channel)
    try:
        find_approved_streamer(load_clip_settings(config), username, channel, platform)
    except StreamerNotApproved as error:
        raise ValueError(str(error)) from error
    if not confirmed:
        raise ValueError("Confirm this completed source is authorized before importing it.")
    selected_id = None
    if source_id:
        from src.clips.acquisition import discovery_for_upload
        try:
            selected_id = int(source_id)
        except ValueError:
            raise ValueError("Choose a valid source discovery.") from None
        discovery_for_upload(channel, selected_id, username, platform)
    path = None
    try:
        path = await store_source_upload(file, config)
        recording_id = await run_in_threadpool(import_recording, channel, config, str(path), username, platform, True)
        if selected_id is not None:
            from src.clips.acquisition import attach_source_recording
            await run_in_threadpool(attach_source_recording, channel, selected_id, recording_id, username, platform)
    except (subprocess.CalledProcessError, OSError) as error:
        raise ValueError("The source could not be read as a completed video. Check the file and available disk space.") from error
    finally:
        await file.close()
        if path is not None:
            path.unlink(missing_ok=True)
    return redirect(f"/clips/{channel}/sources")


@router.post("/clips/{channel}/sources/discover")
def discover_raw_sources(request: Request, channel: str, submission_id: str = Form(...),
                         form_token: str = Form("")):
    """
    Queue metadata-only source discovery using the existing private job controls.

    Args:
        request: The authenticated browser request.
        channel: The clips channel whose configured creators should be checked.
        submission_id: The form identity used to prevent repeated job submissions.
        form_token: The session's CSRF token.

    Returns:
        A redirect to the existing job page; no media or publishing job is requested.
    """
    require_form_token(request, form_token)
    clip_config(channel)
    return queue(channel, "clip_discovery", {}, False, submission_id)


def clip_config(channel):
    """
    Validate a channel key and its clips settings; return the loaded YAML dictionary.

    Args:
        channel: The channel configuration name, without its YAML extension.

    Returns:
        The loaded and validated channel dictionary.
    """
    config = channel_config(channel)
    load_clip_settings(config)
    return config


def save_sections(channel, sections):
    """
    Validate proposed YAML sections and replace only those sections atomically; preserve all unrelated text/comments.

    Args:
        channel: The channel configuration name, without its YAML extension.
        sections: The proposed replacement YAML sections and their values.

    Returns:
        Nothing; the validated configuration replaces its file atomically.
    """
    config = clip_config(channel)
    config.update(sections)
    load_clip_settings(config)
    from src.dashboard import settings
    path = settings.CHANNEL_FOLDER / (channel + ".yaml")
    original = path.read_text()
    root = yaml.compose(original)
    spans = []
    found = set()
    for index, pair in enumerate(root.value):
        key = pair[0].value
        if key not in sections:
            continue
        start = pair[0].start_mark.index
        end = len(original)
        if index + 1 < len(root.value):
            # Preserve the comments before the next section as well.
            end = root.value[index + 1][0].start_mark.index
            tail = original[pair[1].end_mark.index:end]
            end -= len(tail)
        spans.append((start, end, key))
        found.add(key)
    text = original
    for start, end, key in reversed(spans):
        text = text[:start] + commented_section(key, sections[key]) + text[end:]
    for key in sections:
        if key not in found:
            text += "\n" + commented_section(key, sections[key])
    # Re-parse the final text before replacing the user's working config.
    load_clip_settings(yaml.safe_load(text))
    temporary = path.with_suffix(".yaml.part")
    temporary.write_text(text)
    temporary.replace(path)


def commented_section(key, value):
    """
    Serialize one YAML key/value with explanatory setting comments; return the text block.

    Args:
        key: The YAML section name to serialize.
        value: The section value, such as a dictionary or list of streamers.

    Returns:
        A YAML text block containing comments for each setting.
    """
    serialized = yaml.safe_dump({key: value}, sort_keys=False, allow_unicode=True)
    lines = []
    for line in serialized.splitlines():
        match = re.match(r"^(\s*)(?:- )?([a-z_]+):", line)
        if match:
            lines.append(match[1] + "# " + match[2].replace("_", " ").capitalize() + " setting for this channel.")
        lines.append(line)
    return "\n".join(lines) + "\n"


@router.get("/clips")
def clips_index(request: Request):
    """
    Show all clips channels to the logged-in request; return a private channel-picker page.

    Args:
        request: The current logged-in browser request.

    Returns:
        The private clips-channel selection page.
    """
    channels = []
    for channel in list_channels():
        if not channel["error"] and channel_config(channel["key"]).get("type") == "clips":
            channels.append(channel)
    return render_page(request, "clips_index.html", {"channels": channels})


@router.get("/clips/{channel}")
def clips_page(request: Request, channel: str):
    """
    Show streamer status, scored clips, recordings and controls for a channel; return rendered HTML.

    Args:
        request: The current logged-in browser request.
        channel: The channel configuration name, without its YAML extension.

    Returns:
        The private monitoring and automation page.
    """
    config = clip_config(channel)
    connection = get_connection()
    try:
        clips = []
        for row in connection.execute("SELECT * FROM clips WHERE channel=? ORDER BY id DESC LIMIT 100", (channel,)):
            item = dict(row)
            item["signals"] = json.loads(item["signals_json"] or "{}")
            clips.append(item)
        recordings = connection.execute("SELECT * FROM recordings WHERE channel=? ORDER BY id DESC LIMIT 50", (channel,)).fetchall()
        statuses = []
        for row in connection.execute("SELECT * FROM stream_status WHERE channel=?", (channel,)):
            item = dict(row)
            item["details"] = json.loads(item["details"])
            statuses.append(item)
        state = connection.execute("SELECT * FROM clip_automation WHERE channel=?", (channel,)).fetchone()
    finally:
        connection.close()
    return render_page(request, "clips.html", {
        "channel": channel, "config": config, "settings": load_clip_settings(config),
        "clips": clips, "recordings": recordings, "statuses": statuses, "automation": state,
        "submission_id": secrets.token_urlsafe(24),
    })


@router.post("/clips/{channel}/streamers")
def add_streamer(request: Request, channel: str, username: str = Form(...), platform: str = Form(...),
                 permission_note: str = Form(...), permission_date: str = Form(...),
                 recording_directory: str = Form(""), confirmed: bool = Form(False), form_token: str = Form("")):
    """
    Add a streamer from a permission-confirmed form to channel YAML; return to its clips page.

    Args:
        request: The current logged-in browser request.
        channel: The channel configuration name, without its YAML extension.
        username: The approved creator login name, without a site URL.
        platform: The configured platform name, such as twitch or kick.
        permission_note: The reference explaining where written creator permission was received.
        permission_date: The date written permission was granted, formatted YYYY-MM-DD.
        recording_directory: An optional folder containing completed creator-supplied recordings.
        confirmed: Whether the user explicitly confirmed the required permission or reconciliation.
        form_token: The secret form token used to reject cross-site submissions.

    Returns:
        A redirect to the updated clips page.
    """
    require_form_token(request, form_token)
    config = clip_config(channel)
    if not confirmed:
        raise ValueError("Confirm written permission before adding this source.")
    streamers = list(config.get("streamers", []))
    streamers.append({"username": username.strip().lower(), "platform": platform,
                      "permission_note": permission_note.strip(), "permission_date": permission_date,
                      "recording_directory": recording_directory.strip(), "enabled": True})
    save_sections(channel, {"streamers": streamers})
    return redirect(f"/clips/{channel}")


@router.post("/clips/{channel}/source")
def disable_streamer(request: Request, channel: str, username: str = Form(...), platform: str = Form(...),
                     form_token: str = Form("")):
    """
    Disable one source from a checked form and remove its unsent videos from approval; return to clips.

    Args:
        request: The current logged-in browser request.
        channel: The channel configuration name, without its YAML extension.
        username: The approved creator login name, without a site URL.
        platform: The configured platform name, such as twitch or kick.
        form_token: The secret form token used to reject cross-site submissions.

    Returns:
        A redirect after disabling the source and unqueueing unsent drafts.
    """
    require_form_token(request, form_token)
    config = clip_config(channel)
    for streamer in config.get("streamers", []):
        if streamer["username"].lower() == username.lower() and streamer.get("platform", "twitch") == platform:
            streamer["enabled"] = False
    save_sections(channel, {"streamers": config.get("streamers", [])})
    connection = get_connection()
    try:
        connection.execute("""
            UPDATE videos SET status='failed', approved_at=NULL, reviewer_notes='Source disabled.'
            WHERE status IN ('drafted','approved') AND id IN
                (SELECT video_id FROM clips WHERE channel=? AND streamer=? AND platform=?)
                AND NOT EXISTS (SELECT 1 FROM publishing_deliveries WHERE video_id=videos.id)
        """, (channel, username.lower(), platform))
        connection.commit()
    finally:
        connection.close()
    return redirect(f"/clips/{channel}")


@router.post("/clips/{channel}/automation")
def toggle_automation(request: Request, channel: str, enabled: bool = Form(False), form_token: str = Form("")):
    """
    Persist the submitted automation start/stop state after CSRF validation; return to clips.

    Args:
        request: The current logged-in browser request.
        channel: The channel configuration name, without its YAML extension.
        enabled: True to enable future automatic cycles, or False to pause them.
        form_token: The secret form token used to reject cross-site submissions.

    Returns:
        A redirect after saving the Start/Stop choice.
    """
    require_form_token(request, form_token)
    clip_config(channel)
    set_enabled(channel, enabled)
    return redirect(f"/clips/{channel}")


@router.post("/clips/{channel}/run")
def run_clips(request: Request, channel: str, submission_id: str = Form(...),
              dry_run: bool = Form(False), form_token: str = Form("")):
    """
    Queue a clips cycle from a checked form; return the existing job-progress page.

    Args:
        request: The current logged-in browser request.
        channel: The channel configuration name, without its YAML extension.
        submission_id: The unique form ID used to prevent duplicate jobs.
        dry_run: True to report the work without ingestion, paid calls or publishing.
        form_token: The secret form token used to reject cross-site submissions.

    Returns:
        A redirect to the queued cycle progress page.
    """
    require_form_token(request, form_token)
    clip_config(channel)
    return queue(channel, "clip_pipeline", {}, dry_run, submission_id)


@router.post("/clips/{channel}/recording")
def add_recording(request: Request, channel: str, path: str = Form(...), username: str = Form(...),
                  platform: str = Form(...), confirmed: bool = Form(False),
                  submission_id: str = Form(...), form_token: str = Form("")):
    """
    Queue an explicitly authorized local recording path from a checked form; return its background job page.

    Args:
        request: The current logged-in browser request.
        channel: The channel configuration name, without its YAML extension.
        path: The local file path to read or create.
        username: The approved creator login name, without a site URL.
        platform: The configured platform name, such as twitch or kick.
        confirmed: Whether the user explicitly confirmed the required permission or reconciliation.
        submission_id: The unique form ID used to prevent duplicate jobs.
        form_token: The secret form token used to reject cross-site submissions.

    Returns:
        A redirect to the recording ingestion job page.
    """
    require_form_token(request, form_token)
    config = clip_config(channel)
    find_approved_streamer(load_clip_settings(config), username, channel, platform)
    if not confirmed:
        raise ValueError("Confirm permission to process the recording.")
    payload = {"recording": {"path": path, "streamer": username, "platform": platform, "confirmed": True}}
    return queue(channel, "clip_pipeline", payload, False, submission_id)


@router.post("/clips/{channel}/settings")
def edit_settings(request: Request, channel: str, minimum_score: float = Form(...),
                  max_clips: int = Form(...), poll_seconds: int = Form(...), max_uploads: int = Form(...),
                  require_approval: bool = Form(False), publish_dry_run: bool = Form(False),
                  template: str = Form("gaming"), post_times: str = Form(""),
                  form_token: str = Form("")):
    """
    Validate and save analysis, schedule and automation settings from a private form; return to clips.

    Args:
        request: The current logged-in browser request.
        channel: The channel configuration name, without its YAML extension.
        minimum_score: The minimum estimated highlight score from zero to one hundred.
        max_clips: The maximum selected moments per recording.
        poll_seconds: Seconds between automatic monitoring cycles.
        max_uploads: The maximum distinct video upload attempts per local day.
        require_approval: Whether a person must approve finished clips before publishing.
        publish_dry_run: Whether scheduled publishing reports a plan without sending files.
        template: The gaming, reaction or conversation layout name.
        post_times: Comma-separated posting times in HH:MM format.
        form_token: The secret form token used to reject cross-site submissions.

    Returns:
        A redirect after validating and saving the new settings.
    """
    require_form_token(request, form_token)
    config = clip_config(channel)
    analysis = dict(config.get("analysis", {}))
    analysis.update({"minimum_score": minimum_score, "max_clips_per_recording": max_clips})
    automation = dict(config.get("automation", {}))
    automation.update({"poll_seconds": poll_seconds, "max_uploads_per_day": max_uploads,
                       "require_approval": require_approval, "publish_dry_run": publish_dry_run})
    layout = dict(config.get("layout", {}))
    layout["template"] = template
    schedule = dict(config.get("schedule", {}))
    times = []
    for text in post_times.split(","):
        text = text.strip()
        if not text:
            continue
        if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", text):
            raise ValueError("Posting times must be comma-separated HH:MM values, such as 09:00, 17:00.")
        times.append(text)
    schedule["post_times"] = times
    save_sections(channel, {"analysis": analysis, "automation": automation, "layout": layout, "schedule": schedule})
    return redirect(f"/clips/{channel}")


@router.post("/clips/{channel}/retry")
def retry_failed(request: Request, channel: str, item_id: int = Form(...), kind: str = Form(...),
                 confirmed: bool = Form(False), form_token: str = Form("")):
    """
    Reset a failed recording or unfinished clip after explicit reconciliation; return to the clips page.

    Args:
        request: The current logged-in browser request.
        channel: The channel configuration name, without its YAML extension.
        item_id: The database ID of the failed recording or unfinished clip.
        kind: Whether the retry request is for a recording or a clip.
        confirmed: Whether the user explicitly confirmed the required permission or reconciliation.
        form_token: The secret form token used to reject cross-site submissions.

    Returns:
        A redirect after resetting the selected item for another attempt.
    """
    require_form_token(request, form_token)
    config = clip_config(channel)
    if not confirmed or kind not in ("recording", "clip"):
        raise ValueError("Confirm that the cause is fixed and uncertain paid calls have been reviewed.")
    table = "recordings" if kind == "recording" else "clips"
    connection = get_connection()
    try:
        connection.execute("BEGIN IMMEDIATE")
        active = connection.execute("SELECT 1 FROM jobs WHERE channel=? AND status IN ('queued','running')", (channel,)).fetchone()
        if active:
            raise ValueError("Wait for this channel's current job to finish.")
        row = connection.execute(f"SELECT * FROM {table} WHERE id=? AND channel=?", (item_id, channel)).fetchone()
        if not row:
            raise ValueError("Source item not found.")
        find_approved_streamer(load_clip_settings(config), row["streamer"], channel, row["platform"])
        if kind == "recording" and row["status"] == "failed":
            connection.execute("UPDATE recordings SET status='pending', attempts=0, retry_at=0, error='' WHERE id=?", (item_id,))
        elif kind == "clip" and row["status"] in ("picked", "downloaded"):
            connection.execute("UPDATE clips SET render_attempts=0, render_retry_at=0, last_error='' WHERE id=?", (item_id,))
        else:
            raise ValueError("Only failed recordings and unfinished clips can be retried.")
        connection.commit()
    finally:
        connection.close()
    return redirect(f"/clips/{channel}")


@router.post("/video/{video_id}/metadata")
def edit_metadata(request: Request, video_id: int, title: str = Form(...), description: str = Form(""),
                  youtube: bool = Form(False), tiktok: bool = Form(False),
                  form_token: str = Form("")):
    """
    Save editable clip title/description from a checked form, returning approval to draft; return its review page.

    Args:
        request: The current logged-in browser request.
        video_id: The local database ID of the finished video.
        title: The publishing title supplied by the user or generated from speech.
        description: The editable publishing description, before automatic attribution.
        youtube: Whether this clip should be sent to the configured YouTube account.
        tiktok: Whether this clip should be delivered to the configured TikTok inbox.
        form_token: The secret form token used to reject cross-site submissions.

    Returns:
        A redirect to the video, now returned to review.
    """
    require_form_token(request, form_token)
    from src.clips.store import get_clip_for_video

    # The common render guard checks publishing history and active work.
    connection = get_connection()
    try:
        connection.execute("BEGIN IMMEDIATE")
        video = connection.execute("SELECT * FROM videos WHERE id=?", (video_id,)).fetchone()
        if not video or video["status"] == "posted":
            raise ValueError("Choose an unpublished clip.")
        history = connection.execute("SELECT 1 FROM publishing_deliveries WHERE video_id=?", (video_id,)).fetchone()
        public = connection.execute("SELECT 1 FROM platform_posts WHERE video_id=?", (video_id,)).fetchone()
        active = connection.execute("SELECT 1 FROM jobs WHERE video_id=? AND status IN ('queued','running')", (video_id,)).fetchone()
        if history or public or active:
            raise ValueError("Wait for active work; metadata with upload history cannot be changed.")
        clip = get_clip_for_video(video_id)
        if not clip or not title.strip() or len(title) > 100 or len(description) > 1500:
            raise ValueError("Choose a clip, a title up to 100 characters and a description up to 1500 characters.")
        metadata = json.loads(clip["metadata_json"] or "{}")
        destinations = []
        if youtube:
            destinations.append("youtube")
        if tiktok:
            destinations.append("tiktok")
        if not destinations:
            raise ValueError("Choose at least one publishing destination.")
        metadata.update({"youtube_title": title.strip(), "description": description.strip(), "platforms": destinations})
        connection.execute("UPDATE clips SET metadata_json=? WHERE id=?", (json.dumps(metadata), clip["id"]))
        connection.execute("UPDATE videos SET story_title=?, status='drafted', approved_at=NULL WHERE id=?",
                           (title.strip(), video_id))
        connection.commit()
    finally:
        connection.close()
    return redirect(f"/video/{video_id}")

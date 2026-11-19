# =============================================================
# Continuous clips pipeline on the existing persistent job system.
# One heavy worker runs at a time. No paid model is used by default;
# review and publishing dry runs remain on until explicitly configured.
# =============================================================

import json
import logging
import time
import uuid

from src.clips.settings import find_approved_streamer, load_clip_settings
from src.clips.store import clips_with_status, get_clip, update_clip
from src.storage.database import get_connection

logger = logging.getLogger(__name__)


def set_enabled(channel, enabled):
    """
    Persist the Start/Stop choice for a channel; return nothing, leaving an active worker to finish safely.

    Args:
        channel: The channel configuration name, without its YAML extension.
        enabled: True to enable future automatic cycles, or False to pause them.

    Returns:
        Nothing; the Start/Stop choice is saved in SQLite.
    """
    connection = get_connection()
    try:
        connection.execute("""
            INSERT INTO clip_automation (channel, enabled) VALUES (?, ?)
            ON CONFLICT(channel) DO UPDATE SET enabled=excluded.enabled, next_check=0
        """, (channel, int(enabled)))
        connection.commit()
    finally:
        connection.close()


def prepare_downloaded(clip, config, settings):
    """
    Score a downloaded Twitch/local clip using free signals; return its picked row or None below threshold.

    Args:
        clip: The database row for one source clip or selected recording interval.
        config: The channel settings loaded from YAML.
        settings: The validated clip settings, including analysis and duration limits.

    Returns:
        The updated picked clip, or None when no candidate passes.
    """
    from src.clips.maker import words_for
    from src.clips.signals import measure_media, select_highlights
    from src.video.clip_render import probe_video

    find_approved_streamer(settings, clip["streamer"], clip["channel"], clip["platform"])
    words = words_for(clip, settings)
    media = measure_media(clip["file_path"], probe_video(clip["file_path"])["has_audio"])
    candidates = select_highlights(clip["duration_seconds"], words, media, settings)
    if not candidates:
        update_clip(clip["id"], status="skipped", pick_reason="No highlight passed the configured score.")
        return None
    chosen = candidates[0]
    update_clip(clip["id"], status="picked", start_seconds=chosen["start"], end_seconds=chosen["end"],
                hook_title=chosen["hook_title"], highlight_score=chosen["score"],
                signals_json=json.dumps(chosen), pick_reason=chosen["note"])
    return get_clip(clip["id"])


def run_pipeline(channel, config, llm, dry_run=False, recording=None):
    """
    Run monitoring, authorized ingestion, scoring and rendering; return a summary, with no uploads performed here.

    Args:
        channel: The channel configuration name, without its YAML extension.
        config: The channel settings loaded from YAML.
        llm: The existing cached and metered AI client; it is unused when paid assessment is off.
        dry_run: True to report the work without ingestion, paid calls or publishing.
        recording: An imported recording row, or an explicit import request for a pipeline run.

    Returns:
        A summary of generated video IDs, source states and errors.
    """
    from src.clips.maker import make_clip_video, words_for
    from src.clips.metadata import create_metadata
    from src.clips.monitor import check_live_sources
    from src.clips.recordings import import_recording, process_recordings, scan_inboxes
    from src.dashboard.accounts import safe_error

    settings = load_clip_settings(config)
    if dry_run:
        return {"message": "Dry run: would monitor sources, scan completed recordings, score and render drafts. "
                "No network calls, model downloads, paid calls, ingestion or publishing performed."}
    if recording:
        import_recording(channel, config, recording["path"], recording["streamer"],
                         recording["platform"], recording.get("confirmed", False))
    statuses = check_live_sources(channel, config)
    errors = []
    # Discovery has its own six-hour job. Frequent inbox cycles never request downloads.
    try:
        scan_inboxes(channel, config, errors)
    except Exception as error:
        errors.append(safe_error(error))
    from src.clips.acquisition import acquisition_only
    if acquisition_only(config):
        from src.clips.acquisition import source_library
        library = source_library(channel, config)
        return {"video_ids":[], "sources":statuses, "errors":errors,
                "stored_sources":library["stored_count"], "listed_sources":library["listed_count"],
                "message":"Acquisition-only inbox cycle complete. Monitoring and authorized imports ran; "
                          "metadata discovery runs separately. Transcription, editing and publishing are paused."}
    process_recordings(channel, config)
    waiting = clips_with_status(channel, "picked") + clips_with_status(channel, "downloaded")
    made = []
    maximum = settings.analysis.max_clips_per_recording * settings.automation.max_recordings_per_run
    attempted = 0
    for clip in waiting:
        if attempted >= maximum:
            break
        if clip["render_attempts"] >= settings.automation.max_attempts or clip["render_retry_at"] > time.time():
            continue
        tracker = llm.cost_tracker
        event_start = len(tracker.event_ids) if tracker is not None else 0
        try:
            # Re-read current permissions even though the job stores a config snapshot.
            from src.dashboard.settings import channel_config
            current = load_clip_settings(channel_config(channel))
            find_approved_streamer(current, clip["streamer"], channel, clip["platform"])
            if not channel_config(channel).get("enabled", True):
                raise ValueError("Channel is disabled.")
            attempted += 1
            update_clip(clip["id"], render_attempts=clip["render_attempts"] + 1)
            if clip["status"] == "downloaded":
                clip = prepare_downloaded(clip, config, settings)
                if clip is None:
                    continue
            if not json.loads(clip["metadata_json"] or "{}"):
                create_metadata(clip, words_for(clip, settings), config, settings, llm)
                clip = get_clip(clip["id"])
            if clip["highlight_score"] is not None and clip["highlight_score"] < settings.analysis.minimum_score:
                update_clip(clip["id"], status="skipped", pick_reason="AI assessment fell below the configured score.")
                continue
            video_id = make_clip_video(channel, config, settings, clip, llm)
            if tracker is not None:
                all_events = tracker.event_ids
                tracker.event_ids = all_events[event_start:]
                tracker.attach_video(video_id)
                tracker.event_ids = all_events
                # Keep the run's spending total, but attach the next call
                # only after its own video has been created.
                tracker.video_id = None
            update_clip(clip["id"], last_error="")
            quality = json.loads(get_clip(clip["id"])["quality_json"] or "{}")
            if not settings.automation.require_approval and quality.get("passed") and not quality.get("warnings"):
                # Explicit opt-in applies only to technically checked clip videos.
                from src.storage.queries import approve_video
                approve_video(video_id, "Automatic approval enabled in this clips channel.")
            made.append(video_id)
        except Exception as error:
            message = safe_error(error)
            errors.append(message)
            if clip is not None:
                attempts = get_clip(clip["id"])["render_attempts"]
                delay = settings.automation.retry_seconds * 2 ** max(0, attempts - 1)
                if getattr(llm, "pending_event", None) is not None:
                    attempts = settings.automation.max_attempts
                    message = "AI request outcome is uncertain. Review its cost before resetting retries. " + message
                    llm.pending_event = None
                update_clip(clip["id"], last_error=message, render_attempts=attempts,
                            render_retry_at=time.time() + delay)
    connection = get_connection()
    try:
        for row in connection.execute("SELECT error FROM recordings WHERE channel=? AND status='failed'", (channel,)):
            errors.append(row["error"])
    finally:
        connection.close()
    return {"video_ids": made, "sources": statuses, "errors": errors,
            "message": f"Prepared {len(made)} video(s). Review them in the library. "
            "Publishing is handled separately by the configured schedule."}


def queue_due_channels(only_channel=None):
    """
    Enqueue one due cycle per enabled channel under a database lock; return started job IDs.

    Args:
        only_channel: An optional channel name to process instead of all channels.

    Returns:
        The IDs of jobs whose separate workers were started.
    """
    from src.dashboard import jobs
    from src.dashboard.settings import channel_config, list_channels

    started = []
    for item in list_channels():
        channel = item["key"]
        if only_channel and channel != only_channel:
            continue
        try:
            config = channel_config(channel)
            if config.get("type") != "clips" or not config.get("enabled", True):
                continue
            settings = load_clip_settings(config)
            started.extend(queue_due_discovery(channel, settings))
            connection = get_connection()
            try:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute("INSERT OR IGNORE INTO clip_automation (channel, enabled) VALUES (?, ?)",
                                   (channel, int(settings.automation.enabled)))
                row = connection.execute("SELECT * FROM clip_automation WHERE channel=?", (channel,)).fetchone()
                if not row["enabled"] or row["next_check"] > time.time():
                    continue
                connection.execute("UPDATE clip_automation SET next_check=? WHERE channel=?",
                                   (time.time() + settings.automation.poll_seconds, channel))
                connection.commit()
            finally:
                connection.close()
            job_id, created = jobs.enqueue_job(channel, "clip_pipeline", {}, False, uuid.uuid4().hex)
            if created:
                jobs.launch_job(job_id)
                started.append(job_id)
        except Exception as error:
            from src.dashboard.accounts import safe_error
            logger.warning("Clips cycle deferred for %s: %s", channel, safe_error(error))
    return started


def queue_due_discovery(channel, settings):
    """
    Reserve a due metadata check and launch it through the existing job queue.

    Args:
        channel: The clips channel's configuration key.
        settings: Checked channel settings, including discovery interval and Start/Stop defaults.

    Returns:
        A list containing a new discovery job ID, or an empty list when paused or not due.
    """
    from src.dashboard import jobs
    from src.dashboard.accounts import safe_error

    if not settings.discovery.enabled:
        return []
    connection = get_connection()
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("INSERT OR IGNORE INTO clip_automation (channel, enabled) VALUES (?, ?)",
                           (channel, int(settings.automation.enabled)))
        row = connection.execute("SELECT * FROM clip_automation WHERE channel=?", (channel,)).fetchone()
        if not row["enabled"] or row["next_discovery"] > time.time():
            return []
        connection.execute("UPDATE clip_automation SET next_discovery=? WHERE channel=?",
                           (time.time() + settings.discovery.interval_seconds, channel))
        connection.commit()
    finally:
        connection.close()
    try:
        job_id, created = jobs.enqueue_job(channel, "clip_discovery", {}, False, uuid.uuid4().hex)
        if created:
            jobs.launch_job(job_id)
            return [job_id]
    except Exception as error:
        connection = get_connection()
        try:
            connection.execute("UPDATE clip_automation SET next_discovery=?, last_error=? WHERE channel=?",
                               (time.time() + min(300, settings.discovery.interval_seconds), safe_error(error), channel))
            connection.commit()
        finally:
            connection.close()
        logger.warning("Source discovery deferred for %s: %s", channel, safe_error(error))
    return []


def automation_loop(stopped, only_channel=None):
    """
    Check due channels every 15 seconds until a threading Event is set; return on graceful shutdown.

    Args:
        stopped: A threading Event that stops the loop when it is set.
        only_channel: An optional channel name to process instead of all channels.

    Returns:
        Nothing; the loop ends when the stop event is set.
    """
    while not stopped.is_set():
        try:
            queue_due_channels(only_channel)
        except Exception:
            logger.exception("Could not check the clips queue")
        stopped.wait(15)

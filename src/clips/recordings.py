# =============================================================
# Import authorized local recordings and create multiple scored clips.
# Fingerprints, candidates and retry times survive restarts. Source
# files are copied atomically; drafts reuse the existing renderer/review queue.
# =============================================================

import json
import logging
import math
import re
import os
import shutil
import time
from pathlib import Path

from src.clips.finder import UPLOAD_EXTENSIONS, file_fingerprint
from src.clips.settings import credit_for, find_approved_streamer, load_clip_settings
from src.clips.signals import measure_media, select_highlights
from src.clips.store import save_found_clip, update_clip
from src.clips.transcribe import transcribe_clip
from src.storage.database import get_connection
from src.storage.finance import utc_now
from src.video.clip_render import probe_video

logger = logging.getLogger(__name__)


def recording_row(recording_id):
    """
    Read a recording ID from SQLite; return its dictionary or None if absent.

    Args:
        recording_id: The local database ID of the imported recording.

    Returns:
        The recording dictionary, or None when its ID is missing.
    """
    connection = get_connection()
    try:
        row = connection.execute("SELECT * FROM recordings WHERE id = ?", (recording_id,)).fetchone()
        return dict(row) if row else None
    finally:
        connection.close()


def import_recording(channel, config, path, username, platform, confirmed, skip_existing=False):
    """
    Import a permitted local file for a configured streamer; return its recording ID without duplicating bytes.

    Args:
        channel: The channel configuration name, without its YAML extension.
        config: The channel settings loaded from YAML.
        path: The local file path to read or create.
        username: The approved creator login name, without a site URL.
        platform: The configured platform name, such as twitch or kick.
        confirmed: Whether the user explicitly confirmed the required permission or reconciliation.
        skip_existing: True for inbox scans, where duplicate footage should not count as a new import.

    Returns:
        The new or existing recording ID, or None for an existing recording when skip_existing is True.
    """
    settings = load_clip_settings(config)
    streamer = find_approved_streamer(settings, username, channel, platform)
    if not confirmed:
        raise ValueError("Confirm you own this recording or have the creator's permission (--i-own-this).")
    path = Path(path).resolve()
    if not path.is_file() or path.suffix.lower() not in UPLOAD_EXTENSIONS:
        raise ValueError("Choose an existing MP4, MOV, MKV or WebM recording.")
    before = path.stat()
    fingerprint = file_fingerprint(str(path))
    connection = get_connection()
    try:
        existing = connection.execute("SELECT * FROM recordings WHERE fingerprint = ?", (fingerprint,)).fetchone()
    finally:
        connection.close()
    if existing:
        if existing["channel"] != channel or existing["platform"] != streamer.platform or existing["streamer"] != streamer.username.lower():
            raise ValueError("These exact bytes already belong to another source. Check the attribution.")
        if skip_existing:
            return None
        return existing["id"]
    details = probe_video(str(path))
    if details["duration"] <= 0:
        raise ValueError("The recording has no usable duration. Finish recording or remux it first.")
    engagement = read_engagement(path.with_suffix(".engagement.json"), details["duration"])
    destination = Path("cache/clips/recordings") / (fingerprint + path.suffix.lower())
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + ".part")
    shutil.copyfile(path, partial)
    after = path.stat()
    if before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns:
        partial.unlink(missing_ok=True)
        raise ValueError("The file is still changing. Wait until recording has finished.")
    os.replace(partial, destination)
    connection = get_connection()
    try:
        connection.execute("""
            INSERT OR IGNORE INTO recordings
                (channel, platform, streamer, fingerprint, file_path, duration, created_at,
                 original_path, original_size, original_mtime, engagement_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (channel, platform, streamer.username.lower(), fingerprint, str(destination),
              details["duration"], utc_now(), str(path), before.st_size, before.st_mtime_ns, json.dumps(engagement)))
        connection.commit()
        return connection.execute("SELECT id FROM recordings WHERE fingerprint = ?", (fingerprint,)).fetchone()["id"]
    finally:
        connection.close()


def scan_inboxes(channel, config, errors=None):
    """
    Check approved streamers' completed-recording folders; return imported IDs, skipping unchanged files.

    Args:
        channel: The channel configuration name, without its YAML extension.
        config: The channel settings loaded from YAML.
        errors: An optional list where safe messages about unreadable folders or unusable files are appended.

    Returns:
        The newly imported recording IDs; duplicates and individual failures do not use the import limit.
    """
    from src.dashboard.accounts import safe_error

    settings = load_clip_settings(config)
    imported = []
    for streamer in settings.streamers:
        if not streamer.enabled or not streamer.recording_directory:
            continue
        try:
            folder = Path(streamer.recording_directory).expanduser().resolve()
            if not folder.is_dir():
                continue
            paths = sorted(folder.iterdir())
        except OSError as error:
            message = safe_error(f"{credit_for(streamer)} inbox: {safe_error(error)}")
            logger.warning("%s", message)
            if errors is not None:
                errors.append(message)
            continue
        for path in paths:
            try:
                recording_id = import_inbox_file(channel, config, settings, streamer, path)
            except Exception as error:
                # One corrupt or disappearing file must not starve the remaining inboxes.
                message = safe_error(f"{credit_for(streamer)} / {path.name}: {safe_error(error)}")
                logger.warning("%s", message)
                if errors is not None:
                    errors.append(message)
                continue
            if recording_id is None:
                continue
            imported.append(recording_id)
            if len(imported) >= settings.automation.max_recordings_per_run:
                return imported
    return imported


def import_inbox_file(channel, config, settings, streamer, path):
    """
    Import one finished inbox video, skipping shortcuts, recent files and previously imported footage.

    Args:
        channel: The channel configuration name, without its YAML extension.
        config: The channel settings loaded from YAML.
        settings: The checked settings, including how long a completed file must be unchanged.
        streamer: The approved creator whose inbox contains this file.
        path: The local file found directly inside the creator's recording inbox.

    Returns:
        The new recording ID, or None when the file should be skipped.
    """
    # Do not follow a shortcut that leads outside the configured source folder.
    if path.is_symlink() or not path.is_file() or path.suffix.lower() not in UPLOAD_EXTENSIONS:
        return None
    stat = path.stat()
    if time.time() - stat.st_mtime < settings.automation.stable_file_seconds:
        return None
    connection = get_connection()
    try:
        seen = connection.execute("""
            SELECT id FROM recordings WHERE channel=? AND platform=? AND streamer=?
                AND original_path=? AND original_size=? AND original_mtime=?
        """, (channel, streamer.platform, streamer.username.lower(),
              str(path), stat.st_size, stat.st_mtime_ns)).fetchone()
    finally:
        connection.close()
    if seen:
        return None
    return import_recording(channel, config, path, streamer.username, streamer.platform, True, skip_existing=True)


def recording_words(recording, settings):
    """
    Get a recording row's cached Whisper words using configured model/language; return timed word dictionaries.

    Args:
        recording: An imported recording row, or an explicit import request for a pipeline run.
        settings: The validated clip settings, including analysis and duration limits.

    Returns:
        A list of timed words; empty when there is no audio track.
    """
    if not probe_video(recording["file_path"])["has_audio"]:
        return []
    return transcribe_clip(recording["file_path"], "recording_" + recording["fingerprint"],
                           settings.transcription.whisper_model, settings.transcription.language,
                           settings.transcription.engine)


def analyze_recording(recording_id, config):
    """
    Analyze an imported recording and persist diverse candidates once; return newly created clip IDs.

    Args:
        recording_id: The local database ID of the imported recording.
        config: The channel settings loaded from YAML.

    Returns:
        The IDs of newly created or recovered candidate clips.
    """
    recording = recording_row(recording_id)
    if recording is None:
        raise ValueError("Recording not found.")
    settings = load_clip_settings(config)
    streamer = find_approved_streamer(settings, recording["streamer"], recording["channel"], recording["platform"])
    if recording["status"] == "complete":
        return []
    words = recording_words(recording, settings)
    media_path = Path(recording["file_path"]).with_suffix(".signals.json")
    if media_path.is_file():
        media = json.loads(media_path.read_text())
    else:
        details = probe_video(recording["file_path"])
        media = measure_media(recording["file_path"], details["has_audio"])
        partial = media_path.with_suffix(".part")
        partial.write_text(json.dumps(media))
        partial.replace(media_path)
    candidates = select_highlights(recording["duration"], words, media, settings,
                                    json.loads(recording["engagement_json"]))
    clip_ids = []
    for candidate in candidates:
        if repeated_transcript(recording["channel"], streamer, candidate["transcript"]):
            continue
        identity = f"recording:{recording['fingerprint']}:{candidate['start']:.2f}:{candidate['end']:.2f}"
        clip_id = save_found_clip(recording["channel"], {
            "source": "upload", "source_clip_id": identity, "streamer": streamer.username,
            "platform": streamer.platform, "credit_text": credit_for(streamer),
            "title": candidate["hook_title"], "duration_seconds": recording["duration"],
            "ownership_note": "Authorized creator recording; SHA-256 " + recording["fingerprint"],
        })
        if clip_id is None:
            # Recover a crash between saving a source and its picked state.
            connection = get_connection()
            try:
                row = connection.execute("SELECT id, status FROM clips WHERE source='upload' AND source_clip_id=?", (identity,)).fetchone()
            finally:
                connection.close()
            if row["status"] != "found":
                continue
            clip_id = row["id"]
        update_clip(clip_id, recording_id=recording_id, file_path=recording["file_path"],
                    status="picked", start_seconds=candidate["start"], end_seconds=candidate["end"],
                    hook_title=candidate["hook_title"], highlight_score=candidate["score"],
                    pick_reason=candidate["note"], signals_json=json.dumps(candidate))
        clip_ids.append(clip_id)
    connection = get_connection()
    try:
        connection.execute("UPDATE recordings SET status='complete', error='' WHERE id=?", (recording_id,))
        connection.commit()
    finally:
        connection.close()
    return clip_ids


def process_recordings(channel, config):
    """
    Analyze due recordings with bounded persistent retries; return candidate IDs and continue after individual failures.

    Args:
        channel: The channel configuration name, without its YAML extension.
        config: The channel settings loaded from YAML.

    Returns:
        Candidate clip IDs created by due recording analyses.
    """
    from src.dashboard.accounts import safe_error

    settings = load_clip_settings(config)
    connection = get_connection()
    try:
        waiting = connection.execute("""
            SELECT * FROM recordings WHERE channel=? AND status IN ('pending', 'failed')
                AND attempts < ? AND retry_at <= ? ORDER BY id LIMIT ?
        """, (channel, settings.automation.max_attempts, time.time(),
              settings.automation.max_recordings_per_run)).fetchall()
    finally:
        connection.close()
    clips = []
    for row in waiting:
        connection = get_connection()
        try:
            connection.execute("UPDATE recordings SET attempts=attempts+1 WHERE id=?", (row["id"],))
            connection.commit()
        finally:
            connection.close()
        try:
            clips.extend(analyze_recording(row["id"], config))
        except Exception as error:
            connection = get_connection()
            try:
                delay = settings.automation.retry_seconds * 2 ** row["attempts"]
                connection.execute("UPDATE recordings SET status='failed', retry_at=?, error=? WHERE id=?",
                                   (time.time() + delay, safe_error(error), row["id"]))
                connection.commit()
            finally:
                connection.close()
    return clips


def repeated_transcript(channel, streamer, transcript):
    """
    Compare speech against recent clips from this source; return True for nearly identical text across recordings.

    Args:
        channel: The channel configuration name, without its YAML extension.
        streamer: The approved streamer settings, including username and source platform.
        transcript: The spoken text of the candidate being checked for duplicates.

    Returns:
        True when recent speech from the same creator is nearly identical.
    """
    words = set(re.findall(r"\w+", transcript.lower()))
    if len(words) < 8:
        return False
    connection = get_connection()
    try:
        rows = connection.execute("""
            SELECT signals_json FROM clips WHERE channel=? AND streamer=? AND platform=?
            AND status IN ('picked','rendered') ORDER BY id DESC LIMIT 200
        """, (channel, streamer.username.lower(), streamer.platform)).fetchall()
    finally:
        connection.close()
    for row in rows:
        previous = json.loads(row["signals_json"] or "{}")
        other = set(re.findall(r"\w+", previous.get("transcript", "").lower()))
        if other and len(words & other) / len(words | other) > 0.9:
            return True
    return False


def read_engagement(path, duration):
    """
    Read an optional creator-supplied JSON list of time/count samples; return validated counts or an empty list.

    Args:
        path: The local file path to read or create.
        duration: The recording or selected interval length, in seconds.

    Returns:
        Validated audience count samples, or an empty list when there is no sidecar.
    """
    if not path.is_file():
        return []
    if path.stat().st_size > 2_000_000:
        raise ValueError("Engagement JSON must be smaller than 2 MB; aggregate counts before import.")
    events = json.loads(path.read_text())
    if not isinstance(events, list):
        raise ValueError("Engagement JSON must be a list of {time, count} objects.")
    cleaned = []
    for event in events:
        if not isinstance(event, dict):
            raise ValueError("Each engagement sample needs time (seconds) and count.")
        seconds = float(event.get("time", -1))
        count = float(event.get("count", -1))
        if not math.isfinite(seconds) or not math.isfinite(count) or not 0 <= seconds <= duration or count < 0:
            raise ValueError("Engagement times must be inside the recording and counts must be nonnegative finite numbers.")
        cleaned.append({"time": seconds, "count": count})
    return cleaned

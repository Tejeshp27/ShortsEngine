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
from src.clips.settings import credit_for, display_name_for, find_approved_streamer, load_clip_settings
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


def import_recording(channel, config, path, username, platform, confirmed, skip_existing=False,
                     move=False, source_kind="import"):
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
        move: True moves the file into the cache instead of copying it (used for
              live-recorder chunks, so a 3 GB chunk never exists twice on disk).
        source_kind: "import" for completed files, or "live" for rolling-recorder
                     chunks that are deleted once their clips are made.

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
    if move:
        after = path.stat()
        if before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns:
            raise ValueError("The file is still changing. Wait until recording has finished.")
        # Same disk, so this is an instant rename rather than a copy.
        os.replace(path, destination)
    else:
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
                 original_path, original_size, original_mtime, engagement_json, source_kind)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (channel, platform, streamer.username.lower(), fingerprint, str(destination),
              details["duration"], utc_now(), str(path), before.st_size, before.st_mtime_ns,
              json.dumps(engagement), source_kind))
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


def analyze_recording(recording_id, config, llm=None):
    """
    Analyze an imported recording and persist diverse candidates once; return newly created clip IDs.

    Args:
        recording_id: The local database ID of the imported recording.
        config: The channel settings loaded from YAML.
        llm: The metered AI client for the editor model (None = local evidence only).

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
    engagement = json.loads(recording["engagement_json"])
    if settings.ranking.enabled:
        candidates = rank_moments(recording, words, media, engagement, config, settings, streamer, llm)
    else:
        candidates = select_highlights(recording["duration"], words, media, settings, engagement)
    clip_ids = []
    for candidate in candidates:
        if repeated_transcript(recording["channel"], streamer, candidate["transcript"]):
            continue
        if recording["source_kind"] == "live":
            from src.clips.live_recorder import overlaps_neighbouring_chunk
            # Neighbouring live chunks share their boundary minutes; never clip a moment twice.
            if overlaps_neighbouring_chunk(recording_id, candidate["start"], candidate["end"]):
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
                    pick_reason=candidate["note"], signals_json=json.dumps(candidate, default=str))
        if candidate.get("metadata"):
            from src.clips.decisions import save_part
            update_clip(clip_id, metadata_json=json.dumps(candidate["metadata"], ensure_ascii=False))
            save_part(clip_id, "selection", candidate["decision"])
            save_part(clip_id, "titles", candidate["titles"])
            if candidate.get("packaging"):
                save_part(clip_id, "packaging", candidate["packaging"])
        clip_ids.append(clip_id)
    connection = get_connection()
    try:
        connection.execute("UPDATE recordings SET status='complete', error='' WHERE id=?", (recording_id,))
        connection.commit()
    finally:
        connection.close()
    return clip_ids


def rank_moments(recording, words, media, engagement, config, settings, streamer, llm):
    """
    Rank a recording with the new multimodal ranking and prepare each chosen moment's texts.

    Local evidence (sound events, chat, speech) proposes a shortlist; the
    editor model (when enabled) reads it and judges what is actually funny;
    weak moments are rejected. Every decision is logged.

    Args:
        recording: The recording row.
        words: Timed words.
        media: Per-second volume/motion.
        engagement: Chat samples.
        config: Channel YAML.
        settings: Checked clip settings.
        streamer: The approved streamer.
        llm: The metered AI client, or None.

    Returns:
        A list of candidate dictionaries ready to be saved as picked clips
        (with "hook_title", "score", "note", "metadata", "decision", "titles").
    """
    from src.clips.audio_events import recording_events
    from src.clips.decisions import log_event, selection_record
    from src.clips.editor import review_candidates
    from src.clips.ranking import decide_candidates, load_profile, propose_candidates, rerank_with_faces
    from src.clips.titles import choose_titles

    profile_path = settings.ranking.profile
    if settings.ranking.ab_profile and recording["id"] % 2 == 1:
        # A/B test: alternate recordings use the second profile.
        profile_path = settings.ranking.ab_profile
    profile = load_profile(profile_path)
    sounds = recording_events(recording["file_path"])
    duration = recording["duration"]
    faces = None
    if settings.ranking.face_reactions:
        # Read faces on a slightly bigger pool, let them re-rank it (surprised
        # faces and more people went with breakouts), then keep the shortlist.
        pool_size = max(settings.ranking.shortlist_size, settings.ranking.face_pool_size)
        pool = propose_candidates(duration, words, media, sounds, engagement, settings, profile,
                                  pool_size=pool_size)
        tracks = candidate_face_tracks(recording["file_path"], pool, duration, settings.ranking.context_seconds)
        shortlist, tracks = rerank_with_faces(pool, tracks, profile, settings.ranking.shortlist_size)
        faces = face_marks_for(tracks, words)
    else:
        shortlist = propose_candidates(duration, words, media, sounds, engagement, settings, profile)
    name = display_name_for(streamer)
    reviews = []
    if settings.ranking.use_editor_model and llm is not None:
        from src.clips.taste import taste_block
        reviews = review_candidates(shortlist, words, sounds, engagement, duration, name, config, settings, llm,
                                    taste_block(recording["channel"]), volume=media.get("volume_db", []),
                                    faces=faces)
    result = decide_candidates(shortlist, reviews, words, duration, settings, profile)
    recent = recent_titles(recording["channel"])
    hashtags = config.get("publishing", {}).get("hashtags", [])
    outcomes = None
    if settings.packaging.enabled:
        from src.clips.packaging import published_outcomes
        outcomes = published_outcomes(recording["channel"])
    chosen = []
    for candidate in result["selected"]:
        spoken = []
        for word in words:
            if word["end"] > candidate["start"] and word["start"] < candidate["end"]:
                spoken.append(word["word"])
        transcript = " ".join(spoken)
        candidate["transcript"] = transcript
        review = candidate.get("review") or {}
        others = []
        for part in str(review.get("who", "")).replace(" and ", ",").split(","):
            if part.strip():
                others.append(part.strip())
        titles = choose_titles(review, transcript, name, streamer.username, streamer.platform, others,
                               recent, settings.titles, hashtags)
        recent.append(titles["youtube_title"])
        from src.clips.metadata import mask_post_text
        youtube_title = mask_post_text(titles["youtube_title"])
        candidate["hook_title"] = youtube_title
        candidate["note"] = candidate.get("reason", "")
        candidate["metadata"] = {"youtube_title": youtube_title, "hook_text": mask_post_text(titles["hook_text"]),
                                 "description": mask_post_text(titles["tiktok_description"]),
                                 "hashtags": titles["hashtags"], "attribution": credit_for(streamer),
                                 "method": titles["title_method"]}
        if settings.packaging.enabled:
            # Keep experimenting with titles/hashtags/caption looks, guided by past results.
            from src.clips.packaging import apply_packaging, choose_packaging
            packaging = choose_packaging(recording["channel"], settings, outcomes=outcomes)
            candidate["metadata"] = apply_packaging(candidate["metadata"], titles, review, streamer, settings, packaging)
            candidate["metadata"]["youtube_title"] = mask_post_text(candidate["metadata"]["youtube_title"])
            candidate["hook_title"] = candidate["metadata"]["youtube_title"]
            candidate["packaging"] = packaging
        candidate["titles"] = titles
        candidate["decision"] = selection_record(candidate, recording, profile, result["rejected"])
        chosen.append(candidate)
    summary_rejected = []
    for candidate in result["rejected"]:
        summary_rejected.append({"start": candidate["start"], "end": candidate["end"],
                                 "score": candidate.get("score"), "reason": candidate.get("rejected"),
                                 "what_happened": (candidate.get("review") or {}).get("what_happened", "")})
    summary_chosen = []
    for candidate in chosen:
        summary_chosen.append({"start": candidate["start"], "end": candidate["end"], "score": candidate["score"],
                               "category": candidate.get("category"), "reason": candidate.get("reason"),
                               "title": candidate["hook_title"]})
    log_event(recording["channel"], "recording_ranked",
              {"recording_id": recording["id"], "streamer": recording["streamer"], "duration": duration,
               "profile": profile.get("version"), "editor_reviewed": result["reviewed"],
               "shortlisted": len(shortlist), "chosen": summary_chosen, "rejected": summary_rejected})
    return chosen


def recent_titles(channel, limit=40):
    """
    The channel's most recent clip titles (to avoid repeating the same formula).

    Args:
        channel: The channel key.
        limit: How many titles.

    Returns:
        A list of title strings.
    """
    connection = get_connection()
    try:
        rows = connection.execute("SELECT hook_title FROM clips WHERE channel=? AND hook_title != '' "
                                  "ORDER BY id DESC LIMIT ?", (channel, limit)).fetchall()
    finally:
        connection.close()
    titles = []
    for row in rows:
        titles.append(row["hook_title"])
    return titles


def process_recordings(channel, config, llm=None):
    """
    Analyze due recordings with bounded persistent retries; return candidate IDs and continue after individual failures.

    Args:
        channel: The channel configuration name, without its YAML extension.
        config: The channel settings loaded from YAML.
        llm: The metered AI client for the editor model (optional).

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
            clips.extend(analyze_recording(row["id"], config, llm))
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


def candidate_face_marks(source, candidates, words, duration, context_seconds, after_seconds=15):
    """
    Read faces across each shortlisted moment and turn them into editor marks.

    Args:
        source (str): The recording file.
        candidates (list[dict]): Shortlisted windows (start, end).
        words (list[dict]): Timed words.
        duration (float): Recording length.
        context_seconds (float): Seconds shown before each window (same as the editor).
        after_seconds (float): Seconds shown after each window (same as the editor).

    Returns:
        list: For each candidate, a list of (second, "laughing hard"/"shocked").
    """
    tracks = candidate_face_tracks(source, candidates, duration, context_seconds, after_seconds)
    return face_marks_for(tracks, words)


def candidate_face_tracks(source, candidates, duration, context_seconds, after_seconds=15):
    """
    Read faces once a second across each candidate (plus the context the editor sees).

    Args:
        source (str): The recording file.
        candidates (list[dict]): Windows (start, end).
        duration (float): Recording length.
        context_seconds (float): Seconds read before each window.
        after_seconds (float): Seconds read after each window.

    Returns:
        list: For each candidate, its face readings (empty when faces cannot be read).
    """
    from src.clips.expressions import read_face_track

    wanted = []
    for candidate in candidates:
        start = max(0.0, candidate["start"] - context_seconds)
        end = min(duration, candidate["end"] + after_seconds)
        wanted.append((start, end))
    # Candidates often overlap, so join overlapping ranges and read each
    # stretch of video only once (reading faces is the slow part).
    readings = []
    for start, end in merge_ranges(wanted):
        readings.extend(read_face_track(source, start, end))
    tracks = []
    for start, end in wanted:
        track = []
        for reading in readings:
            if start <= reading["time"] <= end:
                track.append(reading)
        tracks.append(track)
    return tracks


def merge_ranges(ranges):
    """
    Join time ranges that overlap or touch.

    Args:
        ranges (list[tuple]): (start, end) pairs in seconds, in any order.

    Returns:
        list[tuple]: Non-overlapping (start, end) pairs, in time order.

    Example:
        merge_ranges([(10, 20), (0, 5), (15, 30)])  # [(0, 5), (10, 30)]
    """
    merged = []
    for start, end in sorted(ranges):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def face_marks_for(tracks, words):
    """
    Turn each candidate's face readings into editor marks.

    Args:
        tracks (list[list[dict]]): From candidate_face_tracks().
        words (list[dict]): Timed words.

    Returns:
        list: For each candidate, a list of (second, "laughing hard"/"shocked").
    """
    from src.clips.expressions import face_marks

    marks = []
    for track in tracks:
        marks.append(face_marks(track, words))
    return marks


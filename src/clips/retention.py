# =============================================================
# src/clips/retention.py — free disk space once footage is used
#
# The live recorder saves streams in big chunks (about 3 GB per hour
# at 1080p). Keeping them would fill the disk within hours, so:
#
#   1. Once every moment chosen from a chunk has been rendered (or has
#      failed for good), a short excerpt around each moment is kept —
#      the moment plus keep_source_padding_seconds on each side — so
#      the clip can still be re-cut from the review page. The full
#      chunk is then deleted.
#   2. Once a finished video has been delivered to every platform (or
#      was rejected), its MP4 and its excerpt are deleted after
#      delete_posted_after_hours. The database row stays, so view
#      counts and history are kept.
#
# Only footage from the live recorder is touched. Recordings imported
# by hand, and their videos, are never deleted here.
# Called by the clips supervisor (src/clips/runtime.py).
# =============================================================

import time
from datetime import datetime
from pathlib import Path
import subprocess

from src.clips.settings import load_clip_settings
from src.storage.database import get_connection
from src.storage.finance import utc_now

ROOT = Path(__file__).resolve().parents[2]
EXCERPT_FOLDER = Path("cache/clips/excerpts")


def keyframe_at_or_before(path, seconds):
    """
    Find the last full picture ("keyframe") at or before a time in a video.

    A video can only be copied without re-encoding from a keyframe, so
    an excerpt starts exactly on one; then its timing is known exactly.

    Args:
        path (str or Path): The video file.
        seconds (float): The time we want to start from.

    Returns:
        float: The keyframe's time in seconds (0.0 when none is found).
    """
    if seconds <= 0:
        return 0.0
    # Only look at keyframes (-skip_frame nokey) in the 15 seconds before the time.
    window = f"{max(0.0, seconds - 15):.3f}%{seconds + 0.05:.3f}"
    result = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-skip_frame", "nokey",
                             "-show_entries", "frame=pts_time", "-of", "csv=p=0",
                             "-read_intervals", window, str(path)], capture_output=True, text=True)
    best = 0.0
    for line in result.stdout.splitlines():
        try:
            value = float(line.strip().rstrip(","))
        except ValueError:
            continue
        if value <= seconds + 0.001 and value > best:
            best = value
    return best


def words_inside(words, start, end):
    """
    Keep the timed words between two times and make them start from zero.

    Args:
        words (list[dict]): [{"word", "start", "end"}, ...] for the whole chunk.
        start (float): The excerpt's start in the chunk, in seconds.
        end (float): The excerpt's end in the chunk, in seconds.

    Returns:
        list[dict]: The words inside the excerpt, timed from its start.

    Example:
        words_inside([{"word": "hi", "start": 12.0, "end": 12.4}], 10.0, 20.0)
        # → [{"word": "hi", "start": 2.0, "end": 2.4}]
    """
    kept = []
    for word in words:
        if word["start"] >= start and word["end"] <= end:
            kept.append({"word": word["word"], "start": round(word["start"] - start, 3),
                         "end": round(word["end"] - start, 3)})
    return kept


def keep_excerpt(clip, recording, settings):
    """
    Save a short excerpt around a clip's moment and point the clip at it.

    Args:
        clip (dict): A clip row whose file is the full chunk.
        recording (dict): The chunk's recording row.
        settings (ClipChannelSettings): Padding and transcription settings.

    Returns:
        str: The excerpt's path.
    """
    from src.clips.recordings import recording_words
    from src.clips.store import update_clip
    from src.clips.transcribe import save_transcript_cache

    padding = settings.live_recording.keep_source_padding_seconds
    wanted_start = max(0.0, clip["start_seconds"] - padding)
    start = keyframe_at_or_before(recording["file_path"], wanted_start)
    end = min(recording["duration"], clip["end_seconds"] + padding)
    EXCERPT_FOLDER.mkdir(parents=True, exist_ok=True)
    excerpt = EXCERPT_FOLDER / f"clip_{clip['id']}.mp4"
    partial = excerpt.with_suffix(".mp4.part")
    # Starting a hair after the keyframe makes FFmpeg land on that exact keyframe.
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-ss", f"{start + 0.0005:.4f}",
                    "-i", recording["file_path"], "-t", f"{end - start:.3f}", "-map", "0", "-c", "copy",
                    "-movflags", "+faststart", "-f", "mp4", str(partial)], check=True)
    partial.replace(excerpt)
    # Copy the excerpt's words from the chunk's transcript instead of running Whisper again.
    words = words_inside(recording_words(recording, settings), start, end)
    transcription = settings.transcription
    save_transcript_cache(f"excerpt_{clip['id']}", words, transcription.whisper_model,
                          transcription.language, transcription.engine)
    update_clip(clip["id"], file_path=str(excerpt),
                start_seconds=round(clip["start_seconds"] - start, 2),
                end_seconds=round(clip["end_seconds"] - start, 2),
                duration_seconds=round(end - start, 2))
    return str(excerpt)


def recording_finished(recording, clips, max_attempts):
    """
    Decide whether every clip from a recording is done, so the full recording can go.

    Args:
        recording (dict): A recording row.
        clips (list[dict]): The clips chosen from it.
        max_attempts (int): Render attempts after which a clip counts as failed for good.

    Returns:
        bool: True when nothing still needs the full recording.

    Example:
        A recording whose analysis finished and whose three clips were
        rendered → True. One clip still waiting to render → False.
    """
    analysed = recording["status"] == "complete"
    gave_up = recording["status"] == "failed" and recording["attempts"] >= max_attempts
    if not analysed and not gave_up:
        return False
    for clip in clips:
        waiting = clip["status"] in ("picked", "downloaded") and clip["render_attempts"] < max_attempts
        if waiting:
            return False
    return True


def release_processed_recordings(channel, config):
    """
    Delete live-recorder chunks whose clips are all made, keeping short excerpts.

    Args:
        channel (str): The clips channel.
        config (dict): The channel YAML.

    Returns:
        list[int]: The IDs of recordings whose full file was deleted.
    """
    settings = load_clip_settings(config)
    if not settings.live_recording.delete_raw_after_processing:
        return []
    connection = get_connection()
    try:
        recordings = connection.execute("""SELECT * FROM recordings WHERE channel=? AND source_kind='live'
            AND raw_deleted_at='' ORDER BY id""", (channel,)).fetchall()
    finally:
        connection.close()
    released = []
    for row in recordings:
        recording = dict(row)
        connection = get_connection()
        try:
            clips = []
            for clip in connection.execute("SELECT * FROM clips WHERE recording_id=?", (recording["id"],)):
                clips.append(dict(clip))
        finally:
            connection.close()
        if not recording_finished(recording, clips, settings.automation.max_attempts):
            continue
        source = Path(recording["file_path"])
        if source.is_file():
            for clip in clips:
                if clip["status"] in ("picked", "rendered") and clip["file_path"] == recording["file_path"]:
                    keep_excerpt(clip, recording, settings)
        source.unlink(missing_ok=True)
        source.with_suffix(".signals.json").unlink(missing_ok=True)
        connection = get_connection()
        try:
            connection.execute("UPDATE recordings SET raw_deleted_at=? WHERE id=?", (utc_now(), recording["id"]))
            connection.commit()
        finally:
            connection.close()
        released.append(recording["id"])
    return released


def hours_since(text, now=None):
    """
    Work out how many hours ago a saved timestamp was.

    Args:
        text (str or None): A saved ISO timestamp such as "2026-11-19T04:30:00+00:00".
        now (float or None): The current time (tests pass a fixed value).

    Returns:
        float or None: Hours since then, or None when there is no timestamp.
    """
    if not text:
        return None
    if now is None:
        now = time.time()
    return (now - datetime.fromisoformat(text).timestamp()) / 3600


def delivered_everywhere(video_id, platforms):
    """
    Check that a video was delivered to every platform the channel posts to.

    Args:
        video_id (int): The video.
        platforms (list[str]): The channel's platforms, e.g. ["tiktok", "youtube"].

    Returns:
        bool: True when every platform reports a successful delivery.
    """
    connection = get_connection()
    try:
        delivered = set()
        for row in connection.execute("SELECT platform FROM publishing_deliveries WHERE video_id=? AND status='succeeded'",
                                      (video_id,)):
            delivered.add(row["platform"])
    finally:
        connection.close()
    for platform in platforms:
        if platform not in delivered:
            return False
    return bool(platforms)


def release_published_videos(channel, config, now=None):
    """
    Delete the files of live-recorder videos that are posted everywhere or rejected.

    Args:
        channel (str): The clips channel.
        config (dict): The channel YAML.
        now (float or None): The current time (tests pass a fixed value).

    Returns:
        list[int]: The IDs of videos whose files were deleted.
    """
    from src.dashboard.media import delete_video_file

    settings = load_clip_settings(config)
    wait_hours = settings.live_recording.delete_posted_after_hours
    platforms = config.get("platforms", [])
    connection = get_connection()
    try:
        rows = connection.execute("""
            SELECT c.id AS clip_id, c.file_path AS clip_file, v.id AS video_id, v.video_path,
                   v.status, v.posted_at, v.created_at
            FROM clips c JOIN videos v ON v.id = c.video_id
            JOIN recordings r ON r.id = c.recording_id
            WHERE c.channel=? AND r.source_kind='live' AND c.files_released_at=''
        """, (channel,)).fetchall()
    finally:
        connection.close()
    released = []
    for row in rows:
        if row["status"] == "posted" and delivered_everywhere(row["video_id"], platforms):
            age = hours_since(row["posted_at"], now)
        elif row["status"] == "rejected":
            age = hours_since(row["created_at"], now)
        else:
            continue
        if age is None or age < wait_hours:
            continue
        delete_video_file(row["video_path"])
        excerpt = Path(row["clip_file"] or "")
        if excerpt.is_file() and excerpt.resolve().is_relative_to((ROOT / EXCERPT_FOLDER).resolve()):
            excerpt.unlink()
        connection = get_connection()
        try:
            connection.execute("UPDATE clips SET files_released_at=? WHERE id=?", (utc_now(), row["clip_id"]))
            connection.commit()
        finally:
            connection.close()
        released.append(row["video_id"])
    return released

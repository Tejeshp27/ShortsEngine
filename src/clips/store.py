# =============================================================
# src/clips/store.py — read and write the `clips` table
#
# Every clip a clips channel finds (or that you upload) gets one
# row. The row follows the clip through the pipeline:
#
#   found       saved from Twitch's clip list
#   downloaded  the video file is in cache/clips/
#   picked      the best moment and hook title are chosen
#   rendered    the vertical video exists and waits in the review queue
#   skipped     not usable (e.g. too short); kept so it is not re-checked
#
# The table's UNIQUE(source, source_clip_id) rule is the duplicate
# check: a clip that is already saved can never be saved again, so
# running the finder twice never downloads anything twice.
# =============================================================

from src.storage.database import get_connection
from src.storage.finance import utc_now


def is_clip_saved(source, source_clip_id):
    """
    Check whether a clip is already in the database.

    Args:
        source (str): "twitch" or "upload".
        source_clip_id (str): Twitch's clip ID, or the uploaded file's fingerprint.

    Returns:
        bool: True if the clip was saved before (by any channel).
    """
    connection = get_connection()
    try:
        row = connection.execute(
            "SELECT 1 FROM clips WHERE source = ? AND source_clip_id = ?", (source, source_clip_id)
        ).fetchone()
    finally:
        connection.close()
    return row is not None


def save_found_clip(channel, clip):
    """
    Save a newly found clip with status "found".

    Args:
        channel (str): The channel key, e.g. "twitch_clips".
        clip (dict): The clip's details: "source", "source_clip_id",
                     "streamer", "credit_text", and optionally "platform"
                     ("twitch" or "kick", default "twitch"), "clip_url",
                     "title", "view_count", "clip_created_at",
                     "duration_seconds", "ownership_note".

    Returns:
        int or None: The new row's ID, or None if the clip was already saved.
    """
    connection = get_connection()
    try:
        # INSERT OR IGNORE quietly does nothing when the UNIQUE rule
        # finds the same clip already saved — that is the duplicate check.
        cursor = connection.execute("""
            INSERT OR IGNORE INTO clips
                (channel, source, source_clip_id, streamer, platform, credit_text, clip_url, title,
                 view_count, clip_created_at, duration_seconds, ownership_note, status, found_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'found', ?)
        """, (channel, clip["source"], clip["source_clip_id"], clip["streamer"].lower(),
              clip.get("platform", "twitch"),
              clip["credit_text"], clip.get("clip_url", ""), clip.get("title", ""),
              int(clip.get("view_count", 0)), clip.get("clip_created_at", ""),
              float(clip.get("duration_seconds", 0)), clip.get("ownership_note", ""), utc_now()))
        connection.commit()
        if cursor.rowcount == 0:
            return None
        return cursor.lastrowid
    finally:
        connection.close()


def update_clip(clip_id, **fields):
    """
    Change some columns of one clip row.

    Args:
        clip_id (int): The clip.
        **fields: Column names and their new values, e.g.
                  update_clip(4, status="downloaded", file_path="cache/clips/a.mp4").
                  ("**fields" collects the named values into a dict.)

    Raises:
        ValueError: If a column name is not one this function may change.
    """
    allowed = {"status", "file_path", "start_seconds", "end_seconds", "hook_title",
               "pick_reason", "last_error", "video_id", "duration_seconds", "recording_id",
               "highlight_score", "signals_json", "quality_json", "metadata_json",
               "render_attempts", "render_retry_at"}
    assignments = []
    values = []
    for name, value in fields.items():
        if name not in allowed:
            raise ValueError(f"Unknown clip column: {name}")
        # The column names come from the fixed list above, never from a
        # user, so putting them into the SQL text is safe.
        assignments.append(f"{name} = ?")
        values.append(value)
    values.append(clip_id)

    connection = get_connection()
    try:
        connection.execute(f"UPDATE clips SET {', '.join(assignments)} WHERE id = ?", values)
        connection.commit()
    finally:
        connection.close()


def get_clip(clip_id):
    """
    Get one clip row.

    Args:
        clip_id (int): The clip.

    Returns:
        dict or None: The row, or None if it does not exist.
    """
    connection = get_connection()
    try:
        row = connection.execute("SELECT * FROM clips WHERE id = ?", (clip_id,)).fetchone()
    finally:
        connection.close()
    if row is None:
        return None
    return dict(row)


def get_clip_for_video(video_id):
    """
    Get the clip a finished video was made from.

    Args:
        video_id (int): The video's ID in the videos table.

    Returns:
        dict or None: The clip row, or None if the video is not a clip.
    """
    connection = get_connection()
    try:
        row = connection.execute("SELECT * FROM clips WHERE video_id = ?", (video_id,)).fetchone()
    finally:
        connection.close()
    if row is None:
        return None
    return dict(row)


def clips_with_status(channel, status, streamer=None):
    """
    List a channel's clips that are at one step of the pipeline.

    Args:
        channel (str): The channel key.
        status (str): For example "downloaded".
        streamer (str or None): Only this streamer's clips, or None for all.

    Returns:
        list[dict]: Matching clips, most-viewed first.
    """
    query = "SELECT * FROM clips WHERE channel = ? AND status = ?"
    values = [channel, status]
    if streamer:
        query += " AND streamer = ?"
        values.append(streamer.lower())
    query += " ORDER BY view_count DESC, id"

    connection = get_connection()
    try:
        rows = connection.execute(query, values).fetchall()
    finally:
        connection.close()
    result = []
    for row in rows:
        result.append(dict(row))
    return result

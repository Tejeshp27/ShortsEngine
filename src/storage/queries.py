# =============================================================
# src/storage/queries.py — read and write video records
#
# Every database operation in the app goes through this file.
# Keeping all SQL in one place means:
#   - the rest of the code never has to write raw SQL
#   - it is easy to swap SQLite for PostgreSQL later
#
# Functions here use the connection from database.py.
# =============================================================

from datetime import datetime

from src.storage.database import get_connection


def save_video(channel, story_title, story_url, script_path, cost_usd):
    """
    Insert a new video row with status 'drafted' and return its ID.

    Args:
        channel (str): The channel key, e.g. "news".
        story_title (str): The headline of the story.
        story_url (str): The source article URL.
        script_path (str): Path to the saved .md script file.
        cost_usd (float): Total AI cost so far for this video.

    Returns:
        int: The new row's ID (useful for linking cost events later).

    Example:
        video_id = save_video("news", "Big Story", "https://...",
                              "output/news/2026-11-07-big-story.md", 0.0134)
    """
    conn = get_connection()
    cursor = conn.execute(
        """
        INSERT INTO videos
            (channel, story_title, story_url, script_path, status,
             total_cost_usd, created_at)
        VALUES (?, ?, ?, ?, 'drafted', ?, ?)
        """,
        (channel, story_title, story_url, script_path, cost_usd,
         datetime.now().isoformat(timespec="seconds")),
    )
    video_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return video_id


def save_cost_event(video_id, channel, purpose, model,
                    input_tokens, output_tokens, cost_usd):
    """
    Record one AI call's token usage and cost linked to a video.

    Args:
        video_id (int): The ID of the video this call belongs to.
        channel (str): The channel key.
        purpose (str): What the call was for, e.g. "ranking" or "writing".
        model (str): The model used, e.g. "claude-haiku-4-5".
        input_tokens (int): Tokens sent to the model.
        output_tokens (int): Tokens the model wrote back.
        cost_usd (float): The cost of this call in US dollars.
    """
    conn = get_connection()
    conn.execute(
        """
        INSERT INTO cost_events
            (video_id, channel, purpose, model, input_tokens,
             output_tokens, cost_usd, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (video_id, channel, purpose, model, input_tokens, output_tokens,
         cost_usd, datetime.now().isoformat(timespec="seconds")),
    )
    conn.commit()
    conn.close()


def set_video_path(video_id, video_path):
    """
    Store the path to the finished MP4 video file.

    Args:
        video_id (int): The video row to update.
        video_path (str): Path to the .mp4 file.
    """
    conn = get_connection()
    conn.execute(
        "UPDATE videos SET video_path = ? WHERE id = ?",
        (video_path, video_id),
    )
    conn.commit()
    conn.close()


def set_video_cost(video_id, cost_usd):
    """
    Update the total AI cost on a video row.

    Args:
        video_id (int): The video row to update.
        cost_usd (float): New total cost in US dollars.
    """
    conn = get_connection()
    conn.execute(
        "UPDATE videos SET total_cost_usd = ? WHERE id = ?",
        (cost_usd, video_id),
    )
    conn.commit()
    conn.close()


def approve_video(video_id, notes=""):
    """
    Mark a video as approved and record the reviewer's notes.

    Args:
        video_id (int): The video row to approve.
        notes (str): Any notes the reviewer left (optional).
    """
    conn = get_connection()
    conn.execute(
        """
        UPDATE videos
        SET status = 'approved',
            reviewer_notes = ?,
            approved_at = ?
        WHERE id = ?
        """,
        (notes, datetime.now().isoformat(timespec="seconds"), video_id),
    )
    conn.commit()
    conn.close()


def reject_video(video_id, notes=""):
    """
    Mark a video as rejected and record the reviewer's notes.

    Args:
        video_id (int): The video row to reject.
        notes (str): The reason for rejection (optional but helpful).
    """
    conn = get_connection()
    conn.execute(
        """
        UPDATE videos
        SET status = 'failed',
            reviewer_notes = ?,
            approved_at = ?
        WHERE id = ?
        """,
        (notes, datetime.now().isoformat(timespec="seconds"), video_id),
    )
    conn.commit()
    conn.close()


def mark_posted(video_id):
    """
    Mark a video as posted (used in Phase 4 after publishing).

    Args:
        video_id (int): The video row to mark posted.
    """
    conn = get_connection()
    conn.execute(
        """
        UPDATE videos
        SET status = 'posted', posted_at = ?
        WHERE id = ?
        """,
        (datetime.now().isoformat(timespec="seconds"), video_id),
    )
    conn.commit()
    conn.close()


def get_videos_by_status(status, channel=None):
    """
    Fetch all videos with a given status, optionally filtered by channel.

    Args:
        status (str): One of 'drafted', 'approved', 'posted', 'failed'.
        channel (str or None): If given, only return videos for that channel.

    Returns:
        list[sqlite3.Row]: Matching rows. Each row behaves like a dict.

    Example:
        drafted = get_videos_by_status("drafted", channel="news")
        for video in drafted:
            print(video["story_title"])
    """
    conn = get_connection()
    if channel:
        rows = conn.execute(
            "SELECT * FROM videos WHERE status = ? AND channel = ? ORDER BY created_at DESC",
            (status, channel),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM videos WHERE status = ? ORDER BY created_at DESC",
            (status,),
        ).fetchall()
    conn.close()
    return rows


def get_video_by_id(video_id):
    """
    Fetch a single video row by its ID.

    Args:
        video_id (int): The row ID.

    Returns:
        sqlite3.Row or None: The row, or None if not found.
    """
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM videos WHERE id = ?", (video_id,)
    ).fetchone()
    conn.close()
    return row


def get_all_videos(channel=None):
    """
    Fetch every video row, newest first.

    Args:
        channel (str or None): If given, only return videos for that channel.

    Returns:
        list[sqlite3.Row]: All matching rows.
    """
    conn = get_connection()
    if channel:
        rows = conn.execute(
            "SELECT * FROM videos WHERE channel = ? ORDER BY created_at DESC",
            (channel,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM videos ORDER BY created_at DESC"
        ).fetchall()
    conn.close()
    return rows


def add_video_cost(video_id, cost_usd):
    """Add cost_usd dollars to video_id's total; return nothing.

    Adding in SQL preserves earlier script and render costs on repeated runs.
    """
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE videos SET total_cost_usd = total_cost_usd + ? WHERE id = ?",
            (cost_usd, video_id),
        )
        conn.commit()
    finally:
        conn.close()

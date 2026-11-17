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

from datetime import datetime, timezone

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
         datetime.now(timezone.utc).isoformat(timespec="seconds")),
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
         cost_usd, datetime.now(timezone.utc).isoformat(timespec="seconds")),
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

    A rejected video ("failed") may be approved again if you change your
    mind. A posted video, or one a job is still writing/rendering, is
    left unchanged.

    Args:
        video_id (int): The video row to approve.
        notes (str): Any notes the reviewer left (optional).

    Returns:
        bool: True if the video was approved, False if it was left unchanged.
    """
    conn = get_connection()
    cursor = conn.execute(
        """
        UPDATE videos
        SET status = 'approved',
            reviewer_notes = ?,
            approved_at = ?
        WHERE id = ? AND status IN ('drafted', 'approved', 'failed')
          AND NOT EXISTS (SELECT 1 FROM jobs WHERE video_id = videos.id AND status IN ('queued', 'running'))
        """,
        (notes, datetime.now(timezone.utc).isoformat(timespec="seconds"), video_id),
    )
    conn.commit()
    conn.close()
    # rowcount is how many rows the UPDATE changed: 1 or 0 here.
    return cursor.rowcount == 1


def reject_video(video_id, notes=""):
    """
    Mark a video as rejected and record the reviewer's notes.

    A posted video, or one a job is still writing/rendering, is left
    unchanged.

    Args:
        video_id (int): The video row to reject.
        notes (str): The reason for rejection (optional but helpful).

    Returns:
        bool: True if the video was rejected, False if it was left unchanged.
    """
    conn = get_connection()
    cursor = conn.execute(
        """
        UPDATE videos
        SET status = 'failed',
            reviewer_notes = ?,
            approved_at = ?
        WHERE id = ? AND status != 'posted'
          AND NOT EXISTS (SELECT 1 FROM jobs WHERE video_id = videos.id AND status IN ('queued', 'running'))
        """,
        (notes, datetime.now(timezone.utc).isoformat(timespec="seconds"), video_id),
    )
    conn.commit()
    conn.close()
    return cursor.rowcount == 1


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
        (datetime.now(timezone.utc).isoformat(timespec="seconds"), video_id),
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


def find_delete_blocker(conn, video):
    """
    Explain why a video may not be deleted, or say nothing if it may.

    A video that was sent to any platform keeps its record, because the
    platform still has a copy and its statistics are tied to this row.
    A video that a job is still working on can't be deleted either.

    Args:
        conn (sqlite3.Connection): An open database connection.
        video (sqlite3.Row): The video row being checked.

    Returns:
        str or None: A plain-English reason, or None if deleting is allowed.
    """
    video_id = video["id"]
    if video["status"] == "posted":
        return "This video was posted. Its record is kept for its statistics."

    sent = conn.execute(
        "SELECT 1 FROM publishing_deliveries WHERE video_id = ?", (video_id,)
    ).fetchone()
    public = conn.execute(
        "SELECT 1 FROM platform_posts WHERE video_id = ?", (video_id,)
    ).fetchone()
    if sent or public:
        return "This video was sent to a platform, so its upload history is kept."

    working = conn.execute(
        "SELECT 1 FROM jobs WHERE video_id = ? AND status IN ('queued', 'running')", (video_id,)
    ).fetchone()
    if working:
        return "A job is still working on this video. Wait for it to finish."
    return None


def keep_unitemised_cost(conn, video):
    """
    Save a deleted video's older, untracked cost as a Money-page expense.

    Older videos stored their cost only as a total on the video row (see
    LEDGER_SQL in finance.py). Without this step, deleting the row would
    make that money vanish from the Money page.

    Args:
        conn (sqlite3.Connection): An open connection, inside a transaction.
        video (sqlite3.Row): The video about to be deleted.
    """
    tracked = conn.execute(
        "SELECT COALESCE(SUM(cost_usd), 0) FROM cost_events WHERE video_id = ?",
        (video["id"],),
    ).fetchone()[0]
    # money_entries stores whole cents; a remainder under half a cent
    # rounds to 0 and is not worth a row.
    cents = round((video["total_cost_usd"] - tracked) * 100)
    if cents <= 0:
        return

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    conn.execute(
        """
        INSERT INTO money_entries
            (channel, platform, kind, amount_cents, entry_date, note, submission_id, created_at)
        VALUES (?, 'other', 'expense', ?, ?, ?, ?, ?)
        """,
        (
            video["channel"],
            cents,
            video["created_at"][:10],
            f"Earlier costs of deleted video: {video['story_title'][:120]}",
            # Video IDs are never reused, so this is unique per video.
            f"deleted-video-{video['id']}",
            now,
        ),
    )


def delete_video_record(video_id):
    """
    Remove a video from the database while keeping its spending history.

    The money already spent on the video stays on the Money page: its
    per-call costs are kept (no longer linked to a video) and any older
    total is saved as an expense. Past jobs are kept too.

    Args:
        video_id (int): The video row to delete.

    Returns:
        sqlite3.Row: The deleted row, so the caller can remove its files.

    Raises:
        ValueError: If the video doesn't exist or may not be deleted
                    (see find_delete_blocker).

    Example:
        deleted = delete_video_record(7)
        print(deleted["video_path"])   # the MP4 that can now be removed
    """
    conn = get_connection()
    try:
        # BEGIN IMMEDIATE locks the database for writing straight away, so
        # no job or upload can start on this video between the checks and
        # the delete.
        conn.execute("BEGIN IMMEDIATE")
        video = conn.execute("SELECT * FROM videos WHERE id = ?", (video_id,)).fetchone()
        if video is None:
            raise ValueError("This video no longer exists.")
        reason = find_delete_blocker(conn, video)
        if reason:
            raise ValueError(reason)

        keep_unitemised_cost(conn, video)
        # Unlink rows that must outlive the video. Without this, the
        # foreign keys would delete the costs or refuse the delete.
        conn.execute("UPDATE cost_events SET video_id = NULL WHERE video_id = ?", (video_id,))
        conn.execute("UPDATE jobs SET video_id = NULL WHERE video_id = ?", (video_id,))
        # A reserved posting slot with nothing sent can be freed.
        conn.execute("DELETE FROM publishing_slots WHERE video_id = ?", (video_id,))
        conn.execute("DELETE FROM videos WHERE id = ?", (video_id,))
        conn.commit()
        return video
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def is_file_used_by_video(column, path):
    """
    Check whether any video row still points at a file.

    Args:
        column (str): "video_path" or "script_path".
        path (str): The saved path to look for.

    Returns:
        bool: True if at least one video uses that file.
    """
    # The column name can't be a "?" value in SQL, so only the two known
    # names are allowed, which keeps the query safe.
    if column not in ("video_path", "script_path"):
        raise ValueError(f"Unknown file column: {column}")
    conn = get_connection()
    try:
        row = conn.execute(f"SELECT 1 FROM videos WHERE {column} = ?", (path,)).fetchone()
    finally:
        conn.close()
    return row is not None

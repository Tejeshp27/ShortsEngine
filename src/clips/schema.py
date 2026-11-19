# =============================================================
# Persistent recording ingestion, signal scores and automation state.
# Called from database.create_tables; existing clips and videos stay intact.
# =============================================================


def create_clip_automation_tables(connection):
    """
    Add recording tables and clip fields to an open SQLite connection; return nothing.

    Args:
        connection: An open SQLite connection owned by the caller.

    Returns:
        Nothing; missing tables and columns are added in place.
    """
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS recordings (
            id INTEGER PRIMARY KEY,
            channel TEXT NOT NULL,
            platform TEXT NOT NULL,
            streamer TEXT NOT NULL,
            fingerprint TEXT NOT NULL UNIQUE,
            file_path TEXT NOT NULL,
            duration REAL NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            attempts INTEGER NOT NULL DEFAULT 0,
            retry_at REAL NOT NULL DEFAULT 0,
            error TEXT NOT NULL DEFAULT '',
            original_path TEXT NOT NULL DEFAULT '',
            original_size INTEGER NOT NULL DEFAULT 0,
            original_mtime INTEGER NOT NULL DEFAULT 0,
            engagement_json TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS stream_status (
            channel TEXT NOT NULL,
            platform TEXT NOT NULL,
            streamer TEXT NOT NULL,
            state TEXT NOT NULL,
            details TEXT NOT NULL DEFAULT '{}',
            checked_at TEXT NOT NULL,
            PRIMARY KEY(channel, platform, streamer)
        );
        CREATE TABLE IF NOT EXISTS clip_automation (
            channel TEXT PRIMARY KEY,
            enabled INTEGER NOT NULL DEFAULT 0,
            next_check REAL NOT NULL DEFAULT 0,
            last_error TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS clip_publish_limits (
            channel TEXT NOT NULL,
            day TEXT NOT NULL,
            video_id INTEGER NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
            PRIMARY KEY(channel, day, video_id)
        );
    """)
    fields = {
        "recording_id": "INTEGER REFERENCES recordings(id)",
        "highlight_score": "REAL",
        "signals_json": "TEXT NOT NULL DEFAULT '{}'",
        "quality_json": "TEXT NOT NULL DEFAULT '{}'",
        "metadata_json": "TEXT NOT NULL DEFAULT '{}'",
        "render_attempts": "INTEGER NOT NULL DEFAULT 0",
        "render_retry_at": "REAL NOT NULL DEFAULT 0",
        "external_id": "TEXT",
        "source_type": "TEXT NOT NULL DEFAULT 'clip'",
        "streamer_display_name": "TEXT NOT NULL DEFAULT ''",
        "clipper": "TEXT NOT NULL DEFAULT ''",
        "thumbnail_url": "TEXT NOT NULL DEFAULT ''",
        "game_id": "TEXT NOT NULL DEFAULT ''",
        "vod_offset": "REAL",
        "source_video_id": "TEXT NOT NULL DEFAULT ''",
        "permission_confirmed": "INTEGER NOT NULL DEFAULT 0",
    }
    existing = set()
    for row in connection.execute("PRAGMA table_info(clips)"):
        existing.add(row["name"])
    for name, definition in fields.items():
        if name not in existing:
            connection.execute(f"ALTER TABLE clips ADD COLUMN {name} {definition}")
    # Preserve the identity of discoveries saved by the earlier clip finder.
    connection.execute("UPDATE clips SET external_id=source_clip_id WHERE source='twitch' AND external_id IS NULL")
    connection.execute("""UPDATE clips SET status='discovered'
        WHERE source='twitch' AND status='found' AND COALESCE(file_path,'')=''""")
    connection.execute("""CREATE UNIQUE INDEX IF NOT EXISTS clips_external_identity
        ON clips(platform, external_id) WHERE external_id IS NOT NULL""")
    automation_fields = set()
    for row in connection.execute("PRAGMA table_info(clip_automation)"):
        automation_fields.add(row["name"])
    if "next_discovery" not in automation_fields:
        connection.execute("ALTER TABLE clip_automation ADD COLUMN next_discovery REAL NOT NULL DEFAULT 0")
    existing_recording_fields = set()
    for row in connection.execute("PRAGMA table_info(recordings)"):
        existing_recording_fields.add(row["name"])
    if "engagement_json" not in existing_recording_fields:
        connection.execute("ALTER TABLE recordings ADD COLUMN engagement_json TEXT NOT NULL DEFAULT '[]'")

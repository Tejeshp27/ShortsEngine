# =============================================================
# src/storage/control_schema.py — tables for the control center
#
# database.py calls create_control_tables() every time the app
# starts. Every step here is "additive": it only creates missing
# tables or columns and never deletes records, so it is safe to
# run again and again.
#
# Tables:
#   accounts          — one connected TikTok/Instagram/YouTube account per channel
#                       (its token is stored encrypted)
#   account_snapshots — follower count per account per day
#   platform_posts    — public post IDs you confirmed by hand
#   money_entries     — payouts and expenses you typed in
#   jobs, job_events  — work started from the browser and its progress
#   source_stories    — stories found by "Find stories"
#   oauth_states      — one-use codes that protect account logins
#   collection_locks  — stop two collectors reading one account at once
# =============================================================


CONTROL_TABLES_SQL = """
    CREATE TABLE IF NOT EXISTS accounts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        channel TEXT NOT NULL,
        platform TEXT NOT NULL CHECK (platform IN ('tiktok', 'instagram', 'youtube')),
        platform_account_id TEXT NOT NULL,
        display_name TEXT NOT NULL,
        credentials TEXT NOT NULL,
        connected_at TEXT NOT NULL,
        last_synced_at TEXT,
        last_error TEXT NOT NULL DEFAULT '',
        UNIQUE(channel, platform),
        UNIQUE(platform, platform_account_id)
    );
    CREATE TABLE IF NOT EXISTS account_snapshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
        day TEXT NOT NULL,
        followers INTEGER NOT NULL CHECK (followers >= 0),
        collected_at TEXT NOT NULL,
        UNIQUE(account_id, day)
    );
    CREATE TABLE IF NOT EXISTS platform_posts (
        video_id INTEGER NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
        platform TEXT NOT NULL CHECK (platform IN ('tiktok', 'instagram', 'youtube')),
        post_id TEXT NOT NULL,
        confirmed_at TEXT NOT NULL,
        PRIMARY KEY(video_id, platform),
        UNIQUE(platform, post_id)
    );
    CREATE TABLE IF NOT EXISTS money_entries (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        channel TEXT NOT NULL,
        platform TEXT NOT NULL,
        kind TEXT NOT NULL CHECK (kind IN ('income', 'expense')),
        amount_cents INTEGER NOT NULL CHECK (amount_cents > 0),
        entry_date TEXT NOT NULL,
        note TEXT NOT NULL DEFAULT '',
        submission_id TEXT NOT NULL UNIQUE,
        created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS jobs (
        id TEXT PRIMARY KEY,
        channel TEXT NOT NULL,
        kind TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'queued',
        stage TEXT NOT NULL DEFAULT 'Waiting to start',
        progress INTEGER NOT NULL DEFAULT 0,
        dry_run INTEGER NOT NULL DEFAULT 0,
        budget_usd REAL NOT NULL DEFAULT 0,
        payload TEXT NOT NULL,
        result TEXT NOT NULL DEFAULT '{}',
        video_id INTEGER REFERENCES videos(id),
        error TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        heartbeat_at TEXT NOT NULL,
        submission_id TEXT NOT NULL UNIQUE
    );
    CREATE TABLE IF NOT EXISTS job_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
        message TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS source_stories (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        channel TEXT NOT NULL,
        url TEXT NOT NULL,
        title TEXT NOT NULL,
        source TEXT NOT NULL,
        summary TEXT NOT NULL DEFAULT '',
        published TEXT NOT NULL DEFAULT '',
        fetched_at TEXT NOT NULL,
        rank INTEGER,
        UNIQUE(channel, url)
    );
    CREATE INDEX IF NOT EXISTS jobs_status ON jobs(status);
    CREATE TABLE IF NOT EXISTS oauth_states (
        state TEXT PRIMARY KEY,
        channel TEXT NOT NULL,
        platform TEXT NOT NULL,
        account_id TEXT NOT NULL,
        session_token TEXT NOT NULL,
        created_at REAL NOT NULL
    );
    CREATE TABLE IF NOT EXISTS collection_locks (
        account_id INTEGER PRIMARY KEY REFERENCES accounts(id) ON DELETE CASCADE,
        expires_at REAL NOT NULL
    );
    CREATE INDEX IF NOT EXISTS videos_channel_date ON videos(channel, created_at);
    CREATE INDEX IF NOT EXISTS money_channel_date ON money_entries(channel, entry_date);
"""

# Columns added after the first release. Each entry is
# (table, column name, column definition).
ADDED_COLUMNS = [
    ("cost_events", "job_id", "TEXT"),
    ("cost_events", "state", "TEXT NOT NULL DEFAULT 'recorded'"),
    ("cost_events", "units", "INTEGER NOT NULL DEFAULT 0"),
    # When an account's token stops working (seconds since 1970).
    # Empty (NULL) means unknown, e.g. a token pasted by hand.
    ("accounts", "token_expires_at", "REAL"),
]

# Date-time columns that older versions saved in the computer's local
# time without a time zone, e.g. "2026-11-09T23:44:21". New records use
# UTC with "+00:00" on the end. Mixing the two made sorting and charts
# slightly wrong, so old values are converted once (see below).
TIMESTAMP_COLUMNS = [
    ("videos", "created_at"),
    ("videos", "approved_at"),
    ("videos", "posted_at"),
    ("cost_events", "created_at"),
    ("metrics", "collected_at"),
]

# Older databases only allowed these two platforms. allow_youtube_platform()
# widens the rule in existing tables (see the function for how).
OLD_PLATFORM_RULE = "CHECK (platform IN ('tiktok', 'instagram'))"
NEW_PLATFORM_RULE = "CHECK (platform IN ('tiktok', 'instagram', 'youtube'))"
TABLES_WITH_PLATFORM_RULE = ("accounts", "platform_posts")

# A local time saved by the old code is exactly 19 characters long:
# "YYYY-MM-DDTHH:MM:SS". UTC values are longer because of "+00:00".
OLD_LOCAL_TIME_LENGTH = 19


def create_control_tables(connection):
    """
    Add the control-center tables and columns to the database.

    Safe to call every time the app starts: existing records are kept.

    Args:
        connection: An open SQLite connection (from get_connection()).

    Returns:
        Nothing.
    """
    connection.executescript(CONTROL_TABLES_SQL)
    add_missing_columns(connection)
    convert_old_local_times_to_utc(connection)
    for table in TABLES_WITH_PLATFORM_RULE:
        allow_youtube_platform(connection, table)


def table_exists(connection, table):
    """
    Check whether a table exists in the database.

    Args:
        connection: An open SQLite connection.
        table (str): The table name, e.g. "metrics".

    Returns:
        bool: True if the table exists.
    """
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
    ).fetchone()
    return row is not None


def column_names(connection, table):
    """
    List the column names of a table.

    Args:
        connection: An open SQLite connection.
        table (str): The table name.

    Returns:
        set[str]: The names of the table's columns.
    """
    names = set()
    # PRAGMA table_info is SQLite's built-in way to describe a table.
    for row in connection.execute(f"PRAGMA table_info({table})"):
        names.add(row["name"])
    return names


def add_missing_columns(connection):
    """
    Add any column from ADDED_COLUMNS that an older database lacks.

    Args:
        connection: An open SQLite connection.

    Returns:
        Nothing.
    """
    for table, name, definition in ADDED_COLUMNS:
        if not table_exists(connection, table):
            continue
        if name not in column_names(connection, table):
            # Table and column names come from the fixed list above, never
            # from a user, so building this SQL text is safe.
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")


def convert_old_local_times_to_utc(connection):
    """
    Rewrite old local-time values in TIMESTAMP_COLUMNS as UTC.

    Only 19-character values (the old format) that SQLite can read as a
    date are changed, so running this again changes nothing and a
    strange value is left alone instead of being erased.

    Args:
        connection: An open SQLite connection.

    Returns:
        Nothing.

    Example:
        "2026-11-09T23:44:21" (Chicago) → "2026-11-10T05:44:21+00:00"
    """
    for table, column in TIMESTAMP_COLUMNS:
        if not table_exists(connection, table):
            continue
        # SQLite's 'utc' modifier means "this value is local time; convert
        # it to UTC". strftime then writes it in the new format.
        connection.execute(f"""
            UPDATE {table}
            SET {column} = strftime('%Y-%m-%dT%H:%M:%S+00:00', {column}, 'utc')
            WHERE length({column}) = ?
              AND strftime('%Y-%m-%dT%H:%M:%S+00:00', {column}, 'utc') IS NOT NULL
        """, (OLD_LOCAL_TIME_LENGTH,))


def allow_youtube_platform(connection, table):
    """
    Let an older table accept "youtube" as a platform, keeping every record.

    SQLite cannot edit a CHECK rule in place, so this follows SQLite's own
    documented recipe: build a copy of the table with the new rule, copy
    all rows across (with the same IDs), delete the old table and give
    the copy the old name. Tables already allowing YouTube are skipped,
    so running this again changes nothing.

    Args:
        connection: An open SQLite connection.
        table (str): "accounts" or "platform_posts" (from a fixed list,
                     never from a user, so building SQL text is safe).

    Returns:
        Nothing.
    """
    row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
    ).fetchone()
    if row is None or OLD_PLATFORM_RULE not in row[0]:
        return

    # The saved table definition already includes columns added later
    # (e.g. token_expires_at), so the copy has exactly the same columns.
    new_definition = row[0].replace(OLD_PLATFORM_RULE, NEW_PLATFORM_RULE)
    new_definition = new_definition.replace(f"CREATE TABLE {table}", f"CREATE TABLE {table}_upgrade", 1)

    # Foreign-key checks must be off while the old table is briefly
    # missing; otherwise follower history linked to accounts would be
    # deleted. This setting only works outside a transaction.
    connection.commit()
    connection.execute("PRAGMA foreign_keys = OFF")
    try:
        connection.execute("BEGIN")
        connection.execute(new_definition)
        connection.execute(f"INSERT INTO {table}_upgrade SELECT * FROM {table}")
        connection.execute(f"DROP TABLE {table}")
        connection.execute(f"ALTER TABLE {table}_upgrade RENAME TO {table}")
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.execute("PRAGMA foreign_keys = ON")

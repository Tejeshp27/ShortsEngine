# =============================================================
# Control-center tables: accounts, daily snapshots, money and jobs.
# database.py calls this additive migration on startup. Existing
# videos and review decisions are preserved.
# =============================================================


def create_control_tables(connection):
    """Add control-center tables to connection, an open SQLite connection.

    Returns nothing. Calling this again leaves existing records intact.
    """
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            channel TEXT NOT NULL,
            platform TEXT NOT NULL CHECK (platform IN ('tiktok', 'instagram')),
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
            platform TEXT NOT NULL CHECK (platform IN ('tiktok', 'instagram')),
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
    """)
    columns = set()
    for row in connection.execute("PRAGMA table_info(cost_events)"):
        columns.add(row["name"])
    # Old installations already have cost_events; add only the missing fields.
    for name, definition in [
        ("job_id", "TEXT"),
        ("state", "TEXT NOT NULL DEFAULT 'recorded'"),
        ("units", "INTEGER NOT NULL DEFAULT 0"),
    ]:
        if name not in columns:
            connection.execute(f"ALTER TABLE cost_events ADD COLUMN {name} {definition}")

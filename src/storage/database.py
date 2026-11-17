# =============================================================
# src/storage/database.py — set up the SQLite database
#
# This file creates the database file (shortsengine.db) and
# makes sure all the tables exist. It is called once at the
# start of every run. The two tables are:
#
#   videos  — one row per video: title, script path, video path,
#              status (drafted / approved / posted / failed),
#              and the total AI cost for that video.
#
#   cost_events — one row per individual AI call, so you can see
#                 exactly what each step (ranking, writing) cost.
#
# In Phase 5 we will add a "channels" table when multiple channels
# need separate OAuth tokens. For now the channel name is just a
# text column on the video row.
# =============================================================

import os
import sqlite3

# The database file lives at the project root (next to src/) by default.
# Setting DATABASE_PATH in the environment moves it somewhere else — the
# Docker setup uses this to keep the file in a folder that is saved
# outside the container, so the data survives a rebuild.
DB_PATH = os.getenv("DATABASE_PATH", "shortsengine.db")


def get_connection():
    """
    Open (or create) the database and return a connection to it.

    SQLite creates the file automatically the first time this is
    called. The connection is configured to return rows as
    dictionaries so you can access columns by name, e.g.
    row["title"] instead of row[0].

    Returns:
        sqlite3.Connection: An open connection to the database.

    Example:
        conn = get_connection()
        conn.execute("SELECT * FROM videos")
        conn.close()
    """
    conn = sqlite3.connect(DB_PATH, timeout=30)

    # row_factory makes every row behave like a dict (column name → value).
    # Without this, rows come back as plain tuples and you have to remember
    # the column order.
    conn.row_factory = sqlite3.Row

    # foreign_keys=ON makes SQLite enforce the links between tables, e.g.
    # a cost row can't point at a video that doesn't exist. (The dashboard's
    # delete button unlinks cost rows first, so spending history is kept.)
    conn.execute("PRAGMA foreign_keys = ON")

    return conn


def create_tables():
    """
    Create the database tables if they don't already exist.

    Safe to call every time the app starts — the IF NOT EXISTS
    clause means nothing happens when the tables are already there.
    """
    conn = get_connection()

    # --- videos table ---
    # Tracks every script/video from creation to posting.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS videos (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            channel         TEXT    NOT NULL,
            story_title     TEXT    NOT NULL,
            story_url       TEXT    NOT NULL,
            script_path     TEXT,
            video_path      TEXT,
            status          TEXT    NOT NULL DEFAULT 'drafted',
            reviewer_notes  TEXT,
            total_cost_usd  REAL    NOT NULL DEFAULT 0.0,
            created_at      TEXT    NOT NULL,
            approved_at     TEXT,
            posted_at       TEXT
        )
    """)

    # A slot keeps its chosen video even when only some uploads succeed.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS publishing_slots (
            channel TEXT NOT NULL,
            slot TEXT NOT NULL,
            video_id INTEGER NOT NULL REFERENCES videos(id),
            PRIMARY KEY (channel, slot)
        )
    """)
    # Claim each upload before contacting a platform to prevent duplicate sends.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS publishing_deliveries (
            video_id INTEGER NOT NULL REFERENCES videos(id),
            platform TEXT NOT NULL,
            status TEXT NOT NULL,
            post_id TEXT,
            PRIMARY KEY (video_id, platform)
        )
    """)

    # --- cost_events table ---
    # One row per individual AI call so we can break down what each step cost.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS cost_events (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            video_id        INTEGER REFERENCES videos(id) ON DELETE CASCADE,
            channel         TEXT    NOT NULL,
            purpose         TEXT    NOT NULL,
            model           TEXT    NOT NULL,
            input_tokens    INTEGER NOT NULL DEFAULT 0,
            output_tokens   INTEGER NOT NULL DEFAULT 0,
            cost_usd        REAL    NOT NULL DEFAULT 0.0,
            created_at      TEXT    NOT NULL
        )
    """)

    # --- users table ---
    # Who may log in to the dashboard. The password itself is never
    # stored — only a one-way "hash" of it (see src/dashboard/auth.py).
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            username        TEXT    NOT NULL UNIQUE,
            password_hash   TEXT    NOT NULL,
            created_at      TEXT    NOT NULL,
            updated_at      TEXT    NOT NULL
        )
    """)

    # --- login_attempts table ---
    # One row per FAILED login, so the dashboard can lock out an address
    # that keeps guessing passwords.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS login_attempts (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            ip_address      TEXT    NOT NULL,
            attempted_at    TEXT    NOT NULL
        )
    """)

    from src.storage.control_schema import create_control_tables
    create_control_tables(conn)
    conn.commit()
    conn.close()

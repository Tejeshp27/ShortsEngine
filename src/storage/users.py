# =============================================================
# src/storage/users.py — database queries for dashboard logins
#
# Two tables are used here (both created in database.py):
#
#   users          — who may log in, and the hash of their password
#   login_attempts — one row per failed login, used to lock out
#                    an address that keeps guessing
#
# This file only reads and writes rows. The rules (what counts as
# a correct password, when to lock someone out) live in
# src/dashboard/auth.py.
# =============================================================

from src.storage.database import get_connection


def save_user(username, password_hash, now):
    """
    Create a user, or change the password of an existing user.

    Args:
        username (str): The login name, e.g. "tejesh".
        password_hash (str): The hashed password (never the real one).
        now (str): The current time as text, e.g. "2026-11-10T09:30:00".
    """
    conn = get_connection()
    existing = conn.execute(
        "SELECT id FROM users WHERE username = ?", (username,)
    ).fetchone()

    if existing:
        conn.execute(
            "UPDATE users SET password_hash = ?, updated_at = ? WHERE username = ?",
            (password_hash, now, username),
        )
    else:
        conn.execute(
            """
            INSERT INTO users (username, password_hash, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            """,
            (username, password_hash, now, now),
        )

    conn.commit()
    conn.close()


def get_user(username):
    """
    Look up one user by login name.

    Args:
        username (str): The login name to look for.

    Returns:
        sqlite3.Row or None: The user's row (id, username, password_hash,
                             created_at, updated_at), or None if there is
                             no such user.
    """
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM users WHERE username = ?", (username,)
    ).fetchone()
    conn.close()
    return row


def count_users():
    """
    Count how many users exist.

    Returns:
        int: The number of users. 0 means nobody can log in yet.
    """
    conn = get_connection()
    row = conn.execute("SELECT COUNT(*) AS total FROM users").fetchone()
    conn.close()
    return row["total"]


def record_failed_login(ip_address, now):
    """
    Save one failed login attempt.

    Args:
        ip_address (str): The network address the attempt came from.
        now (str): The current time as text.
    """
    conn = get_connection()
    conn.execute(
        "INSERT INTO login_attempts (ip_address, attempted_at) VALUES (?, ?)",
        (ip_address, now),
    )
    conn.commit()
    conn.close()


def count_failed_logins_since(ip_address, since):
    """
    Count an address's failed logins from a given time onwards.

    Args:
        ip_address (str): The network address to check.
        since (str): Only attempts at or after this time are counted.

    Returns:
        int: The number of recent failed attempts.
    """
    conn = get_connection()
    row = conn.execute(
        """
        SELECT COUNT(*) AS total
        FROM login_attempts
        WHERE ip_address = ? AND attempted_at >= ?
        """,
        (ip_address, since),
    ).fetchone()
    conn.close()
    return row["total"]


def clear_failed_logins(ip_address):
    """
    Forget an address's failed logins (called after a successful login).

    Args:
        ip_address (str): The network address to clear.
    """
    conn = get_connection()
    conn.execute("DELETE FROM login_attempts WHERE ip_address = ?", (ip_address,))
    conn.commit()
    conn.close()


def delete_failed_logins_before(cutoff):
    """
    Delete old failed-login rows so the table does not grow forever.

    Args:
        cutoff (str): Rows older than this time are deleted.
    """
    conn = get_connection()
    conn.execute("DELETE FROM login_attempts WHERE attempted_at < ?", (cutoff,))
    conn.commit()
    conn.close()

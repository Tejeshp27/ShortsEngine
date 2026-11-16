# =============================================================
# src/dashboard/auth.py — who is allowed into the dashboard
#
# This file holds the login rules. It answers four questions:
#
#   1. Is this password correct?            (verify_login)
#   2. Has this address guessed too often?  (is_locked_out)
#   3. Is this browser already logged in?   (get_logged_in_user)
#   4. Did this form really come from us?   (check_form_token)
#
# How passwords are kept safe:
#   The real password is never saved anywhere. We save a "hash" —
#   a scrambled version that cannot be turned back into the
#   password. To check a login we scramble what was typed the
#   same way and compare. We use bcrypt, the most widely used
#   password-hashing library; it is deliberately slow (about a
#   quarter of a second), which makes guessing millions of
#   passwords impractical.
#
# How it fits in:
#   app.py calls these functions from the login page and from the
#   check that runs before every other page.
# =============================================================

import os
import secrets
from datetime import datetime, timedelta, timezone

import bcrypt
import yaml

from src.storage.users import (
    clear_failed_logins,
    count_failed_logins_since,
    delete_failed_logins_before,
    get_user,
    record_failed_login,
    save_user,
)


# Where the dashboard's shared settings live.
DASHBOARD_CONFIG_PATH = os.path.join("config", "dashboard.yaml")

# Used when config/dashboard.yaml is missing or leaves a setting out.
DEFAULT_LOGIN_SETTINGS = {
    "max_failed_attempts": 5,
    "lockout_minutes": 15,
    "session_days": 7,
}

# How much work bcrypt does per password. Each step up doubles the time;
# 12 is the library's default and takes about a quarter of a second.
BCRYPT_ROUNDS = 12

# Password rules. bcrypt ignores everything after 72 bytes, so longer
# passwords are refused rather than silently cut short.
MINIMUM_PASSWORD_LENGTH = 12
MAXIMUM_PASSWORD_BYTES = 72

# A real bcrypt hash of a random password nobody knows. When someone
# tries a username that does not exist, we still check their password
# against this, so a wrong username takes just as long to reject as a
# wrong password. Otherwise the response time would reveal which
# usernames exist.
_DUMMY_HASH = bcrypt.hashpw(secrets.token_bytes(16), bcrypt.gensalt()).decode("utf-8")


def load_login_settings():
    """
    Read the login settings from config/dashboard.yaml.

    Returns:
        dict: "max_failed_attempts", "lockout_minutes" and "session_days",
              using the default for any setting the file leaves out.

    Example:
        settings = load_login_settings()
        settings["max_failed_attempts"]   # → 5
    """
    settings = dict(DEFAULT_LOGIN_SETTINGS)

    if os.path.exists(DASHBOARD_CONFIG_PATH):
        with open(DASHBOARD_CONFIG_PATH, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}
        for key in settings:
            if key in config.get("login", {}):
                settings[key] = config["login"][key]

    return settings


def current_time():
    """
    Get the current time (in UTC, so it is the same on every computer).

    This is its own small function so tests can replace it and pretend
    that time has passed.

    Returns:
        datetime: The time right now.
    """
    return datetime.now(timezone.utc)


def _time_as_text(moment):
    """
    Turn a time into text for saving in the database.

    Args:
        moment (datetime): The time to convert.

    Returns:
        str: e.g. "2026-11-10T09:30:00+00:00". Text in this format sorts
             in time order, so the database can compare times as text.
    """
    return moment.isoformat(timespec="seconds")


def check_password_rules(password):
    """
    Check that a new password is acceptable.

    Args:
        password (str): The password someone wants to use.

    Returns:
        str: "" if the password is fine, otherwise a sentence explaining
             what is wrong with it.

    Example:
        check_password_rules("short")   # → "Password must be at least 12 characters."
    """
    if len(password) < MINIMUM_PASSWORD_LENGTH:
        return f"Password must be at least {MINIMUM_PASSWORD_LENGTH} characters."
    if len(password.encode("utf-8")) > MAXIMUM_PASSWORD_BYTES:
        return f"Password must be at most {MAXIMUM_PASSWORD_BYTES} bytes long."
    return ""


def hash_password(password):
    """
    Scramble a password so it can be stored safely.

    Args:
        password (str): The real password.

    Returns:
        str: The hash, e.g. "$2b$12$...". It is different every time,
             even for the same password, because bcrypt mixes in a random
             value (a "salt") that is saved inside the hash.
    """
    hashed = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(BCRYPT_ROUNDS))
    return hashed.decode("utf-8")


def password_matches(password, password_hash):
    """
    Check a typed password against a stored hash.

    Args:
        password (str): What the person typed.
        password_hash (str): The hash saved in the database.

    Returns:
        bool: True if the password is the one the hash was made from.
    """
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        # Raised for a password longer than bcrypt allows, or a damaged
        # hash. Either way the login is not valid.
        return False


def set_password(username, password):
    """
    Create a dashboard user, or change an existing user's password.

    Changing the password also logs that user out everywhere, because
    every session remembers when the password was last changed.

    Args:
        username (str): The login name.
        password (str): The new password (must pass check_password_rules).

    Raises:
        ValueError: If the username is empty or the password breaks a rule.
    """
    username = username.strip()
    if not username:
        raise ValueError("Username cannot be empty.")

    problem = check_password_rules(password)
    if problem:
        raise ValueError(problem)

    # Microsecond precision, so two password changes in the same second
    # still get different "last changed" times.
    now = current_time().isoformat(timespec="microseconds")
    save_user(username, hash_password(password), now)


def verify_login(username, password):
    """
    Check a username and password typed into the login form.

    Args:
        username (str): The login name that was typed.
        password (str): The password that was typed.

    Returns:
        sqlite3.Row or None: The user's database row if the login is
                             correct, otherwise None.
    """
    user = get_user(username.strip())

    if user is None:
        # Unknown username: do the same slow check anyway (see _DUMMY_HASH).
        password_matches(password, _DUMMY_HASH)
        return None

    if not password_matches(password, user["password_hash"]):
        return None

    return user


def is_locked_out(ip_address):
    """
    Decide whether an address has failed to log in too many times.

    Args:
        ip_address (str): The network address of the browser.

    Returns:
        bool: True if this address must wait before trying again.
    """
    settings = load_login_settings()
    window_start = current_time() - timedelta(minutes=settings["lockout_minutes"])
    failures = count_failed_logins_since(ip_address, _time_as_text(window_start))
    return failures >= settings["max_failed_attempts"]


def note_failed_login(ip_address):
    """
    Remember that an address just entered a wrong username or password.

    Args:
        ip_address (str): The network address of the browser.
    """
    now = current_time()
    record_failed_login(ip_address, _time_as_text(now))

    # Tidy up: failures older than a day can never count towards a
    # lockout again, so delete them.
    delete_failed_logins_before(_time_as_text(now - timedelta(days=1)))


def note_successful_login(ip_address):
    """
    Wipe an address's failed attempts after it logs in correctly.

    Args:
        ip_address (str): The network address of the browser.
    """
    clear_failed_logins(ip_address)


def start_session(session, user):
    """
    Mark a browser as logged in.

    The session is a small set of values kept in a signed cookie in the
    browser. "Signed" means the browser cannot change it without us
    noticing, because it does not know SESSION_SECRET.

    Args:
        session (dict): The request's session (request.session).
        user (sqlite3.Row): The user who just logged in.
    """
    # Start from empty so nothing from before the login is carried over.
    session.clear()
    session["username"] = user["username"]
    # Remember which password this login used. If the password changes
    # later, this value no longer matches and the session stops working.
    session["password_changed_at"] = user["updated_at"]
    # A random value that every form must send back (see check_form_token).
    session["form_token"] = secrets.token_urlsafe(32)


def get_logged_in_user(session):
    """
    Find out who, if anyone, this browser is logged in as.

    Args:
        session (dict): The request's session (request.session).

    Returns:
        str or None: The username, or None if the browser is not logged
                     in, the user was deleted, or the password has
                     changed since this browser logged in.
    """
    username = session.get("username")
    if not username:
        return None

    user = get_user(username)
    if user is None:
        return None
    if session.get("password_changed_at") != user["updated_at"]:
        return None

    return username


def check_form_token(session, submitted_token):
    """
    Check that a submitted form really came from one of our own pages.

    Without this, another website could trick your browser into sending
    a request such as "approve video 7" while you are logged in (this
    attack is called cross-site request forgery, or CSRF). Our pages put
    a secret random token in every form; another site cannot know it.

    Args:
        session (dict): The request's session (request.session).
        submitted_token (str): The token that arrived with the form.

    Returns:
        bool: True if the token matches the one stored in the session.
    """
    expected_token = session.get("form_token", "")
    if not expected_token or not submitted_token:
        return False
    # compare_digest takes the same time whether the tokens differ at the
    # first character or the last, so timing reveals nothing.
    return secrets.compare_digest(expected_token, submitted_token)

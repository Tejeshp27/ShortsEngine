# =============================================================
# src/dashboard/accounts.py — connecting TikTok and Instagram accounts
#
# Each channel can connect one TikTok and one Instagram account.
# This file:
#   - runs the official login flow ("OAuth") for each platform
#   - asks the platform who the account is and how many followers it has
#   - stores the account's access token ENCRYPTED in the database
#   - refreshes TikTok tokens before they expire
#
# Tokens are never put in a page, a cookie or a log. TikTok uses its
# "Login Kit"; Instagram uses Meta's Facebook Login, which works for
# professional Instagram accounts linked to a Facebook Page.
#
# OAuth in one paragraph: we send you to the platform's login page
# with a random "state" value. After you agree, the platform sends
# your browser back to us with a one-use "code" and the same state.
# We check the state (so nobody else can finish the login for you)
# and trade the code for an access token.
# =============================================================

import base64
import hashlib
import json
import os
import re
import sqlite3
import time
from urllib.parse import urlencode

import httpx
from cryptography.fernet import Fernet, InvalidToken

from src.storage.database import get_connection
from src.storage.finance import utc_now

TIKTOK_BASE = "https://open.tiktokapis.com/v2"

# What the dashboard asks each platform for permission to do.
TIKTOK_SCOPES = "user.info.basic,user.info.stats,video.list,video.upload"
INSTAGRAM_SCOPES = "instagram_basic,instagram_manage_insights,pages_show_list,pages_read_engagement"

# Refresh a token this many seconds before it expires, so it never
# runs out in the middle of a request.
REFRESH_MARGIN_SECONDS = 300

# Words that mark an environment setting as secret. safe_error() hides
# the values of these settings if they ever appear in an error message.
SECRET_SETTING_WORDS = ("TOKEN", "SECRET", "API_KEY")


# ---------------------------------------------------------------------------
# Encrypting tokens
# ---------------------------------------------------------------------------

def token_cipher():
    """
    Get the tool that locks and unlocks saved account tokens.

    "Fernet" (from the PyCA cryptography library) encrypts data and also
    detects if the encrypted text was changed. It needs a key:
      - ACCOUNT_TOKEN_KEY from .env, if set, or
      - a key made from SESSION_SECRET. Changing SESSION_SECRET then
        means every account has to be connected again.

    Returns:
        Fernet: The cipher object.

    Raises:
        ValueError: If no usable secret is set.
    """
    key = os.getenv("ACCOUNT_TOKEN_KEY")
    if not key:
        secret = os.getenv("SESSION_SECRET", "")
        if len(secret) < 32:
            raise ValueError("Set SESSION_SECRET before connecting accounts.")
        # SHA-256 turns the secret into exactly 32 bytes, the size Fernet
        # needs. The fixed prefix makes this key different from any other
        # key made from the same secret.
        digest = hashlib.sha256(("shortsengine-account-tokens:" + secret).encode()).digest()
        key = base64.urlsafe_b64encode(digest)
    try:
        return Fernet(key)
    except (ValueError, TypeError) as error:
        raise ValueError("ACCOUNT_TOKEN_KEY must be a valid Fernet key.") from error


def encrypt_credentials(credentials):
    """
    Lock a dict of account credentials so it can be stored safely.

    Args:
        credentials (dict): For example {"access_token": "...", "expires_at": 1767225600}.

    Returns:
        str: Encrypted text, safe to save in the database.
    """
    plain_text = json.dumps(credentials).encode()
    return token_cipher().encrypt(plain_text).decode()


def decrypt_credentials(encrypted):
    """
    Unlock credentials saved by encrypt_credentials().

    Args:
        encrypted (str): The encrypted text from the database.

    Returns:
        dict: The original credentials.

    Raises:
        ValueError: If the key changed or the text was damaged.
    """
    try:
        plain_text = token_cipher().decrypt(encrypted.encode()).decode()
        return json.loads(plain_text)
    except (InvalidToken, ValueError) as error:
        raise ValueError("Account credentials could not be opened. Reconnect the account "
                         "after checking the encryption key.") from error


# ---------------------------------------------------------------------------
# Talking to the platforms
# ---------------------------------------------------------------------------

def safe_error(error):
    """
    Turn an error into a message that is safe to show on a page.

    Web addresses are removed (they can contain tokens) and the value of
    any secret setting from .env is replaced with "[hidden]".

    Args:
        error (Exception): The error that happened.

    Returns:
        str: A message of at most 600 characters.
    """
    if isinstance(error, (httpx.HTTPError, sqlite3.Error)):
        return "The account service or database request failed. Check the connection and try again."

    text = str(error)
    text = re.sub(r"https?://\S+", "[request URL hidden]", text)
    for name, value in os.environ.items():
        is_secret_setting = False
        for word in SECRET_SETTING_WORDS:
            if word in name:
                is_secret_setting = True
        # Very short values are skipped: hiding "1" would mangle the message.
        if is_secret_setting and len(value) >= 8:
            text = text.replace(value, "[hidden]")

    text = text[:600]
    if not text:
        return "The operation could not be completed."
    return text


def platform_reported_error(data):
    """
    Check whether a platform's answer says the request failed.

    TikTok always includes an "error" field; its code is "ok" on success.
    Meta only includes "error" when something went wrong.

    Args:
        data (dict): The platform's JSON answer.

    Returns:
        bool: True if the answer describes a failure.
    """
    failure = data.get("error")
    if not failure:
        return False
    if isinstance(failure, dict) and failure.get("code") == "ok":
        return False
    return True


def api_json(method, url, token=None, **options):
    """
    Send one request to an official platform API and return its answer.

    Args:
        method (str): "GET" or "POST".
        url (str): The API address.
        token (str or None): The account's access token, sent privately in
                             the Authorization header (never in the URL).
        **options: Extra request details passed to httpx, such as
                   params= (query values), data= (form) or json= (body).

    Returns:
        dict: The platform's JSON answer.

    Raises:
        ValueError: If the platform cannot be reached or refuses. The
                    platform's raw answer is not included, because it can
                    echo private values.
    """
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        response = httpx.request(method, url, headers=headers, timeout=25,
                                 follow_redirects=False, **options)
        data = response.json()
    except (httpx.HTTPError, ValueError) as error:
        raise ValueError("The platform could not be reached. Try again later.") from error

    if response.status_code >= 400 or platform_reported_error(data):
        raise ValueError("The platform rejected the request. Check the token, account "
                         "permissions and app approval, then reconnect.")
    return data


def graph_base():
    """
    Get the address of Meta's API, including its version number.

    Returns:
        str: For example "https://graph.facebook.com/v24.0".

    Raises:
        ValueError: If META_GRAPH_VERSION in .env is not like "v24.0".
    """
    version = os.getenv("META_GRAPH_VERSION", "v24.0")
    if not re.fullmatch(r"v\d+\.\d+", version):
        raise ValueError("META_GRAPH_VERSION must look like v24.0.")
    return f"https://graph.facebook.com/{version}"


def fetch_tiktok_profile(token):
    """
    Ask TikTok who an account is and how many followers it has.

    Args:
        token (str): The account's access token.

    Returns:
        dict: "id", "name" and "followers" (followers may be None).
    """
    data = api_json("GET", TIKTOK_BASE + "/user/info/", token,
                    params={"fields": "open_id,display_name,follower_count"})
    user = data.get("data", {}).get("user", {})
    return {"id": user.get("open_id"), "name": user.get("display_name"),
            "followers": user.get("follower_count")}


def fetch_instagram_profile(token, account_id):
    """
    Ask Meta who an Instagram professional account is and its followers.

    Args:
        token (str): The access token.
        account_id (str): The numeric Instagram professional account ID.

    Returns:
        dict: "id", "name" and "followers" (followers may be None).
    """
    if not account_id.isdigit():
        raise ValueError("Enter the numeric Instagram professional account ID from your Meta app.")
    user = api_json("GET", graph_base() + f"/{account_id}", token,
                    params={"fields": "id,username,followers_count"})
    return {"id": user.get("id"), "name": user.get("username"),
            "followers": user.get("followers_count")}


def fetch_profile(platform, token, account_id=""):
    """
    Get an account's ID, name and follower count from its platform.

    Args:
        platform (str): "tiktok" or "instagram".
        token (str): The account's access token.
        account_id (str): Instagram only: the numeric professional account ID.

    Returns:
        dict: {"id": "...", "name": "...", "followers": 120}

    Raises:
        ValueError: If the platform did not return the follower count.
                    A missing count is an error, never shown as 0.
    """
    if platform == "tiktok":
        result = fetch_tiktok_profile(token)
    elif platform == "instagram":
        result = fetch_instagram_profile(token, account_id)
    else:
        raise ValueError("Choose TikTok or Instagram.")

    if not result["id"] or result["followers"] is None:
        raise ValueError("The platform did not return account statistics. "
                         "Grant the follower/statistics permission and reconnect.")
    result["id"] = str(result["id"])
    if not result["name"]:
        result["name"] = result["id"]
    return result


# ---------------------------------------------------------------------------
# Saving and reading connected accounts
# ---------------------------------------------------------------------------

def save_connection(channel, platform, profile, credentials):
    """
    Save a verified account for a channel, with its token encrypted.

    Reconnecting the SAME account (for example to renew its token) keeps
    its follower history. Connecting a DIFFERENT account is refused until
    the old one is disconnected, so a chart never mixes two accounts.

    Args:
        channel (str): The channel, e.g. "news".
        platform (str): "tiktok" or "instagram".
        profile (dict): From fetch_profile(): "id", "name", "followers".
        credentials (dict): The token and, when known, "expires_at"
                            (seconds since 1970).

    Returns:
        int: The account's row ID.

    Raises:
        ValueError: If another account is connected here, or this account
                    is already connected to another channel.
    """
    encrypted = encrypt_credentials(credentials)
    expires_at = credentials.get("expires_at")

    connection = get_connection()
    try:
        connection.execute("BEGIN IMMEDIATE")
        old = connection.execute(
            "SELECT * FROM accounts WHERE channel = ? AND platform = ?", (channel, platform)
        ).fetchone()
        if old and old["platform_account_id"] != profile["id"]:
            raise ValueError("Disconnect the current account before replacing it with another account.")

        # "ON CONFLICT ... DO UPDATE" updates the existing row for this
        # channel and platform instead of failing on the unique rule.
        connection.execute("""
            INSERT INTO accounts (channel, platform, platform_account_id, display_name,
                                  credentials, connected_at, token_expires_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(channel, platform) DO UPDATE SET
                credentials = excluded.credentials,
                display_name = excluded.display_name,
                token_expires_at = excluded.token_expires_at,
                last_error = ''
        """, (channel, platform, profile["id"], profile["name"], encrypted, utc_now(), expires_at))

        account_id = connection.execute(
            "SELECT id FROM accounts WHERE channel = ? AND platform = ?", (channel, platform)
        ).fetchone()[0]
        save_snapshot(connection, account_id, profile["followers"])
        connection.commit()
        return account_id
    except sqlite3.IntegrityError as error:
        raise ValueError("This platform account is already connected to another channel.") from error
    finally:
        connection.close()


def save_snapshot(connection, account_id, followers):
    """
    Save today's follower count for an account (one row per day).

    Args:
        connection: An open SQLite connection; the caller commits.
        account_id (int): The account's row ID.
        followers (int): The follower count.

    Returns:
        Nothing.
    """
    now = utc_now()
    today = now[:10]
    connection.execute("""
        INSERT INTO account_snapshots (account_id, day, followers, collected_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(account_id, day) DO UPDATE SET
            followers = excluded.followers,
            collected_at = excluded.collected_at
    """, (account_id, today, followers, now))
    connection.execute(
        "UPDATE accounts SET last_synced_at = ?, last_error = '' WHERE id = ?", (now, account_id)
    )


def get_account(channel, platform):
    """
    Get the saved account for a channel and platform.

    Args:
        channel (str): The channel, e.g. "news".
        platform (str): "tiktok" or "instagram".

    Returns:
        dict or None: The account row (credentials still encrypted),
                      or None if nothing is connected.
    """
    connection = get_connection()
    try:
        row = connection.execute(
            "SELECT * FROM accounts WHERE channel = ? AND platform = ?", (channel, platform)
        ).fetchone()
        if row is None:
            return None
        return dict(row)
    finally:
        connection.close()


def refresh_tiktok_token(account, credentials):
    """
    Get a new TikTok access token using the saved refresh token, and save it.

    TikTok also sends a NEW refresh token each time; the old one stops
    working, so the new one must be saved.

    Args:
        account (dict): The account row.
        credentials (dict): The decrypted credentials, including "refresh_token".

    Returns:
        dict: The updated credentials.
    """
    data = api_json("POST", TIKTOK_BASE + "/oauth/token/", data={
        "client_key": required_env("TIKTOK_CLIENT_KEY"),
        "client_secret": required_env("TIKTOK_CLIENT_SECRET"),
        "grant_type": "refresh_token",
        "refresh_token": credentials["refresh_token"],
    })
    credentials.update(data)
    credentials["expires_at"] = time.time() + data["expires_in"]

    connection = get_connection()
    try:
        connection.execute(
            "UPDATE accounts SET credentials = ?, token_expires_at = ? WHERE id = ?",
            (encrypt_credentials(credentials), credentials["expires_at"], account["id"]),
        )
        connection.commit()
    finally:
        connection.close()
    return credentials


def access_token(account):
    """
    Get a working access token for an account, refreshing it if needed.

    Args:
        account (dict): The account row from get_account().

    Returns:
        str: The access token.

    Raises:
        ValueError: If the token has expired and cannot be refreshed
                    (Instagram tokens and tokens without a refresh token
                    must be reconnected by hand).

    Note:
        A token pasted by hand has no known expiry date. It is used until
        the platform refuses it; the channel page warns about this.
    """
    credentials = decrypt_credentials(account["credentials"])
    expires_at = credentials.get("expires_at")
    expires_soon = expires_at and expires_at < time.time() + REFRESH_MARGIN_SECONDS

    if expires_soon:
        can_refresh = account["platform"] == "tiktok" and credentials.get("refresh_token")
        if not can_refresh:
            raise ValueError("This account token has expired. Reconnect it on the channel page.")
        credentials = refresh_tiktok_token(account, credentials)

    return credentials["access_token"]


# ---------------------------------------------------------------------------
# The official login flow (OAuth)
# ---------------------------------------------------------------------------

def required_env(name):
    """
    Read a setting from .env that must be filled in.

    Args:
        name (str): The setting, e.g. "TIKTOK_CLIENT_KEY".

    Returns:
        str: Its value.

    Raises:
        ValueError: If it is missing or still a placeholder like "your-key".
    """
    value = os.getenv(name, "")
    if not value or value.startswith(("your-", "replace-")):
        raise ValueError(f"Add {name} to .env and restart the dashboard.")
    return value


def authorization_url(platform, state):
    """
    Build the platform login page address to send the browser to.

    Args:
        platform (str): "tiktok" or "instagram".
        state (str): A random one-use value; the platform sends it back so
                     we can check the login was started by this browser.

    Returns:
        str: The full login page address.
    """
    if platform == "tiktok":
        parameters = {
            "client_key": required_env("TIKTOK_CLIENT_KEY"),
            "response_type": "code",
            "scope": TIKTOK_SCOPES,
            "redirect_uri": required_env("TIKTOK_REDIRECT_URI"),
            "state": state,
        }
        return "https://www.tiktok.com/v2/auth/authorize/?" + urlencode(parameters)

    if platform != "instagram":
        raise ValueError("Choose TikTok or Instagram.")

    parameters = {
        "client_id": required_env("META_APP_ID"),
        "response_type": "code",
        "scope": INSTAGRAM_SCOPES,
        "redirect_uri": required_env("INSTAGRAM_REDIRECT_URI"),
        "state": state,
    }
    version = graph_base().rsplit("/", 1)[-1]   # "https://.../v24.0" → "v24.0"
    return f"https://www.facebook.com/{version}/dialog/oauth?" + urlencode(parameters)


def exchange_tiktok_code(code):
    """
    Trade TikTok's one-use login code for an access token.

    Args:
        code (str): The code TikTok sent back to the dashboard.

    Returns:
        dict: TikTok's answer, including "access_token" and "expires_in".
    """
    return api_json("POST", TIKTOK_BASE + "/oauth/token/", data={
        "client_key": required_env("TIKTOK_CLIENT_KEY"),
        "client_secret": required_env("TIKTOK_CLIENT_SECRET"),
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": required_env("TIKTOK_REDIRECT_URI"),
    })


def exchange_meta_code(code):
    """
    Trade Meta's one-use login code for a long-lived access token.

    Meta first gives a short-lived token (about an hour); a second request
    swaps it for a long-lived one (about 60 days).

    Args:
        code (str): The code Meta sent back to the dashboard.

    Returns:
        dict: Meta's answer, including "access_token" and "expires_in".
    """
    short_lived = api_json("GET", graph_base() + "/oauth/access_token", params={
        "client_id": required_env("META_APP_ID"),
        "client_secret": required_env("META_APP_SECRET"),
        "code": code,
        "redirect_uri": required_env("INSTAGRAM_REDIRECT_URI"),
    })
    return api_json("GET", graph_base() + "/oauth/access_token", params={
        "grant_type": "fb_exchange_token",
        "client_id": required_env("META_APP_ID"),
        "client_secret": required_env("META_APP_SECRET"),
        "fb_exchange_token": short_lived["access_token"],
    })


def exchange_code(platform, code):
    """
    Trade a one-use login code for the account's credentials.

    Args:
        platform (str): "tiktok" or "instagram".
        code (str): The code the platform sent back.

    Returns:
        dict: The credentials, with "expires_at" added (seconds since 1970).

    Raises:
        ValueError: If the answer has no token or no lifetime.
    """
    if platform == "tiktok":
        data = exchange_tiktok_code(code)
    else:
        data = exchange_meta_code(code)

    if not data.get("access_token") or not data.get("expires_in"):
        raise ValueError("The platform returned an incomplete token response. Reconnect the account.")
    data["expires_at"] = time.time() + int(data["expires_in"])
    return data

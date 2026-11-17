# =============================================================
# src/clips/twitch_login.py — let a streamer allow clip downloads
#
# Twitch only gives clip download links to the streamer (or their
# editors). So each approved streamer logs in ONCE and grants our
# app one narrow permission: "channel:manage:clips".
#
# We use Twitch's "device code" login because it needs no website:
#   1. We ask Twitch for a short code (e.g. "ABCD-EFGH").
#   2. You send the code to the streamer. They open
#      https://www.twitch.tv/activate, type it and press Authorize.
#   3. Meanwhile we ask Twitch every few seconds whether they did.
#      When they do, Twitch gives us their token.
# The token is saved ENCRYPTED in the twitch_logins table (same
# encryption as the TikTok/Instagram/YouTube connections).
#
# Twitch user tokens last about 4 hours; refresh_if_needed() swaps
# the saved "refresh token" for a new pair before they run out.
# =============================================================

import json
import time

import httpx

from src.dashboard.accounts import decrypt_credentials, encrypt_credentials
from src.sources.twitch import (
    REQUEST_TIMEOUT_SECONDS,
    TWITCH_TOKEN_URL,
    TwitchError,
    get_user,
    twitch_credentials,
)
from src.storage.database import get_connection
from src.storage.finance import utc_now

DEVICE_URL = "https://id.twitch.tv/oauth2/device"
DEVICE_GRANT_TYPE = "urn:ietf:params:oauth:grant-type:device_code"

# The one permission we ask streamers for: managing (and so
# downloading) the clips of their own channel. Nothing else.
DOWNLOAD_SCOPE = "channel:manage:clips"

# Refresh a token this many seconds before it expires.
REFRESH_MARGIN_SECONDS = 300


def start_device_login():
    """
    Ask Twitch for a login code a streamer can type at twitch.tv/activate.

    Returns:
        dict: Twitch's answer with "device_code" (secret, kept by us),
              "user_code" (what the streamer types), "verification_uri",
              "expires_in" and "interval" (seconds between checks).

    Raises:
        TwitchError: If Twitch refuses the request.
    """
    client_id, _ = twitch_credentials()
    response = httpx.post(DEVICE_URL, timeout=REQUEST_TIMEOUT_SECONDS,
                          data={"client_id": client_id, "scopes": DOWNLOAD_SCOPE})
    data = response.json()
    if response.status_code != 200 or "device_code" not in data:
        raise TwitchError(f"Twitch did not start the login: {data.get('message', response.status_code)}")
    return data


def check_device_login(device_code):
    """
    Ask Twitch once whether the streamer has finished logging in.

    Args:
        device_code (str): From start_device_login().

    Returns:
        dict or None: The new tokens ("access_token", "refresh_token",
                      "expires_in", "scope"), or None if the streamer
                      has not finished yet.

    Raises:
        TwitchError: If the code expired or the streamer said no.
    """
    client_id, client_secret = twitch_credentials()
    response = httpx.post(TWITCH_TOKEN_URL, timeout=REQUEST_TIMEOUT_SECONDS, data={
        "client_id": client_id,
        # Our app is a "confidential" Twitch app, so it proves who it is
        # with its secret as well as its ID.
        "client_secret": client_secret,
        "scopes": DOWNLOAD_SCOPE,
        "device_code": device_code,
        "grant_type": DEVICE_GRANT_TYPE,
    })
    data = response.json()
    if response.status_code == 200 and "access_token" in data:
        return data
    if data.get("message") == "authorization_pending":
        return None
    raise TwitchError(f"Twitch login did not finish: {data.get('message', response.status_code)}")


def credentials_from_tokens(tokens, now=None):
    """
    Turn Twitch's token answer into what we store (encrypted).

    Args:
        tokens (dict): Twitch's answer with access_token, refresh_token, expires_in.
        now (float or None): The current time in seconds (tests pass a fixed one).

    Returns:
        dict: {"access_token", "refresh_token", "expires_at"} where
              expires_at is seconds since 1970.
    """
    if now is None:
        now = time.time()
    return {
        "access_token": tokens["access_token"],
        "refresh_token": tokens.get("refresh_token", ""),
        "expires_at": now + int(tokens.get("expires_in", 0)),
    }


def save_login(user, scopes, credentials):
    """
    Save (or replace) a streamer's login, with the token encrypted.

    Args:
        user (dict): From get_user(): "id" and "login".
        scopes (list[str]): The permissions the streamer granted.
        credentials (dict): From credentials_from_tokens().
    """
    connection = get_connection()
    try:
        connection.execute("""
            INSERT INTO twitch_logins (twitch_user_id, login, scopes, credentials, connected_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(twitch_user_id) DO UPDATE SET
                login = excluded.login, scopes = excluded.scopes,
                credentials = excluded.credentials, connected_at = excluded.connected_at
        """, (user["id"], user["login"].lower(), json.dumps(scopes),
              encrypt_credentials(credentials), utc_now()))
        connection.commit()
    finally:
        connection.close()


def get_login(login):
    """
    Find a streamer's saved login.

    Args:
        login (str): Their Twitch login name.

    Returns:
        dict or None: The twitch_logins row, or None if they never logged in.
    """
    connection = get_connection()
    try:
        row = connection.execute("SELECT * FROM twitch_logins WHERE login = ?",
                                 (login.lower(),)).fetchone()
    finally:
        connection.close()
    if row is None:
        return None
    return dict(row)


def refresh_tokens(refresh_token):
    """
    Swap a refresh token for a new access token (and a new refresh token).

    Twitch refresh tokens work only once, so the new one must be saved.

    Args:
        refresh_token (str): The saved refresh token.

    Returns:
        dict: Twitch's answer with the new tokens.

    Raises:
        TwitchError: If Twitch refuses (e.g. the streamer removed our access).
    """
    client_id, client_secret = twitch_credentials()
    response = httpx.post(TWITCH_TOKEN_URL, timeout=REQUEST_TIMEOUT_SECONDS, data={
        "client_id": client_id,
        "client_secret": client_secret,
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
    })
    data = response.json()
    if response.status_code != 200 or "access_token" not in data:
        raise TwitchError("The streamer's Twitch login has expired or was removed. "
                          "Ask them to connect again.")
    return data


def user_token_for(login, channel_key="twitch_clips"):
    """
    Get a working download token for a streamer, refreshing it if needed.

    Args:
        login (str): The streamer's Twitch login.
        channel_key (str): The channel's file name, used in the message.

    Returns:
        tuple: (twitch_user_id, access_token)

    Raises:
        TwitchError: If the streamer has not logged in yet.
    """
    saved = get_login(login)
    if saved is None:
        raise TwitchError(
            f"'{login}' has not allowed clip downloads yet. Run: "
            f"python -m src.clips --channel {channel_key} connect {login}"
        )

    credentials = decrypt_credentials(saved["credentials"])
    if credentials["expires_at"] - REFRESH_MARGIN_SECONDS > time.time():
        return saved["twitch_user_id"], credentials["access_token"]

    tokens = refresh_tokens(credentials["refresh_token"])
    new_credentials = credentials_from_tokens(tokens)
    user = {"id": saved["twitch_user_id"], "login": saved["login"]}
    save_login(user, json.loads(saved["scopes"]), new_credentials)
    return saved["twitch_user_id"], new_credentials["access_token"]


def connect_streamer(login, wait_seconds=None):
    """
    Run the whole device login for one streamer, printing instructions.

    Args:
        login (str): The approved streamer's Twitch login.
        wait_seconds (int or None): Give up after this long (default:
                                    as long as Twitch keeps the code valid).

    Returns:
        str: A message saying who connected.

    Raises:
        TwitchError: If the code expires, the streamer declines, or a
                     DIFFERENT Twitch account authorized the code.
    """
    started = start_device_login()
    print()
    print(f"  Send this to {login}:")
    print(f"    1. Open {started['verification_uri']}")
    print(f"    2. Type the code {started['user_code']} and press Authorize.")
    print("  It only lets this app download clips from your own channel.")
    print(f"  (The code works for {int(started['expires_in']) // 60} minutes. Waiting...)")

    limit = wait_seconds or int(started["expires_in"])
    interval = max(int(started.get("interval", 5)), 1)
    deadline = time.time() + limit
    tokens = None
    while time.time() < deadline:
        time.sleep(interval)
        tokens = check_device_login(started["device_code"])
        if tokens is not None:
            break
    if tokens is None:
        raise TwitchError("The login code expired before the streamer finished. Run connect again.")

    # Find out WHO logged in. If someone else used the code, refuse it:
    # a token from another account could not download this streamer's clips.
    user = get_user(login, tokens["access_token"])
    validated = whoami(tokens["access_token"])
    if validated.lower() != login.lower():
        raise TwitchError(f"The code was used by '{validated}', not '{login}'. Nothing was saved.")

    scopes = tokens.get("scope", [DOWNLOAD_SCOPE])
    save_login(user, scopes, credentials_from_tokens(tokens))
    return f"{user['display_name']} connected. Their clips can now be downloaded."


def whoami(access_token):
    """
    Ask Twitch which account a user token belongs to.

    Args:
        access_token (str): A user access token.

    Returns:
        str: The account's login name.

    Raises:
        TwitchError: If the token is not valid.
    """
    response = httpx.get("https://id.twitch.tv/oauth2/validate", timeout=REQUEST_TIMEOUT_SECONDS,
                         headers={"Authorization": f"OAuth {access_token}"})
    data = response.json()
    if response.status_code != 200 or "login" not in data:
        raise TwitchError("Twitch says the new login token is not valid.")
    return data["login"]

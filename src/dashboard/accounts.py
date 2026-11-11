# =============================================================
# Per-channel official platform connections. Tokens are encrypted
# with PyCA Fernet and never put in a page, cookie or log. TikTok
# uses Login Kit; Instagram uses Meta's Facebook Login path for
# professional accounts linked to a Facebook Page.
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


def token_cipher():
    """Return the authenticated cipher derived from the private server secret.

    ACCOUNT_TOKEN_KEY may be a dedicated Fernet key. Otherwise a domain-specific
    SHA-256 derivation of SESSION_SECRET is used. Rotating that secret requires
    reconnecting accounts when no dedicated key is set.
    """
    key = os.getenv("ACCOUNT_TOKEN_KEY")
    if not key:
        secret = os.getenv("SESSION_SECRET", "")
        if len(secret) < 32:
            raise ValueError("Set SESSION_SECRET before connecting accounts.")
        key = base64.urlsafe_b64encode(hashlib.sha256(("shortsengine-account-tokens:" + secret).encode()).digest())
    try:
        return Fernet(key)
    except (ValueError, TypeError) as error:
        raise ValueError("ACCOUNT_TOKEN_KEY must be a valid Fernet key.") from error


def encrypt_credentials(credentials):
    """Encrypt the credentials dictionary and return text safe for the database."""
    return token_cipher().encrypt(json.dumps(credentials).encode()).decode()


def decrypt_credentials(encrypted):
    """Return decrypted credentials from encrypted text, or a reconnection error."""
    try:
        return json.loads(token_cipher().decrypt(encrypted.encode()).decode())
    except (InvalidToken, ValueError) as error:
        raise ValueError("Account credentials could not be opened. Reconnect the account after checking the encryption key.") from error


def safe_error(error):
    """Return a useful error message without URLs or environment secret values."""
    if isinstance(error, (httpx.HTTPError, sqlite3.Error)):
        return "The account service or database request failed. Check the connection and try again."
    text = str(error)
    text = re.sub(r"https?://\S+", "[request URL hidden]", text)
    for key, value in os.environ.items():
        if len(value) >= 8 and any(word in key for word in ("TOKEN", "SECRET", "API_KEY")):
            text = text.replace(value, "[hidden]")
    return text[:600] or "The operation could not be completed."


def api_json(method, url, token=None, **options):
    """Call an official API and return its dictionary response.

    method/url select the request; token is sent as a private Authorization
    header. options holds query, form or JSON fields. Errors omit raw bodies.
    """
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        response = httpx.request(method, url, headers=headers, timeout=25, follow_redirects=False, **options)
        data = response.json()
    except (httpx.HTTPError, ValueError) as error:
        raise ValueError("The platform could not be reached. Try again later.") from error
    failure = data.get("error")
    if response.status_code >= 400 or (failure and (not isinstance(failure, dict) or failure.get("code") != "ok")):
        raise ValueError("The platform rejected the request. Check the token, account permissions and app approval, then reconnect.")
    return data


def graph_base():
    """Return Meta's versioned official API URL using the configured version."""
    version = os.getenv("META_GRAPH_VERSION", "v24.0")
    if not re.fullmatch(r"v\d+\.\d+", version):
        raise ValueError("META_GRAPH_VERSION must look like v24.0.")
    return f"https://graph.facebook.com/{version}"


def fetch_profile(platform, token, account_id=""):
    """Return id, name and followers for an authorized platform account.

    token is the user's access token. Instagram also requires its professional
    account_id. Missing follower permission is reported instead of a false zero.
    """
    if platform == "tiktok":
        data = api_json("GET", TIKTOK_BASE + "/user/info/", token,
                        params={"fields": "open_id,display_name,follower_count"})
        user = data.get("data", {}).get("user", {})
        result = {"id": user.get("open_id"), "name": user.get("display_name"), "followers": user.get("follower_count")}
    elif platform == "instagram":
        if not account_id.isdigit():
            raise ValueError("Enter the numeric Instagram professional account ID from your Meta app.")
        user = api_json("GET", graph_base() + f"/{account_id}", token,
                        params={"fields": "id,username,followers_count"})
        result = {"id": user.get("id"), "name": user.get("username"), "followers": user.get("followers_count")}
    else:
        raise ValueError("Choose TikTok or Instagram.")
    if not result["id"] or result["followers"] is None:
        raise ValueError("The platform did not return account statistics. Grant the follower/statistics permission and reconnect.")
    result["id"] = str(result["id"])
    result["name"] = result["name"] or result["id"]
    return result


def save_connection(channel, platform, profile, credentials):
    """Save verified profile and encrypted credentials for channel/platform.

    Reconnecting the same account preserves history. Replacing it deletes that
    account's old follower history so the chart never joins two different people.
    Returns the account row ID.
    """
    encrypted = encrypt_credentials(credentials)
    connection = get_connection()
    try:
        connection.execute("BEGIN IMMEDIATE")
        old = connection.execute("SELECT * FROM accounts WHERE channel = ? AND platform = ?", (channel, platform)).fetchone()
        if old and old["platform_account_id"] != profile["id"]:
            raise ValueError("Disconnect the current account before replacing it with another account.")
        connection.execute("""
            INSERT INTO accounts (channel, platform, platform_account_id, display_name, credentials, connected_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(channel, platform) DO UPDATE SET credentials=excluded.credentials,
                display_name=excluded.display_name, last_error=''
        """, (channel, platform, profile["id"], profile["name"], encrypted, utc_now()))
        account_id = connection.execute("SELECT id FROM accounts WHERE channel=? AND platform=?", (channel, platform)).fetchone()[0]
        save_snapshot(connection, account_id, profile["followers"])
        connection.commit()
        return account_id
    except sqlite3.IntegrityError as error:
        raise ValueError("This platform account is already connected to another channel.") from error
    finally:
        connection.close()


def save_snapshot(connection, account_id, followers):
    """Save today's followers for account_id using connection; return nothing."""
    now = utc_now()
    connection.execute("""
        INSERT INTO account_snapshots (account_id, day, followers, collected_at) VALUES (?, ?, ?, ?)
        ON CONFLICT(account_id, day) DO UPDATE SET followers=excluded.followers, collected_at=excluded.collected_at
    """, (account_id, now[:10], followers, now))
    connection.execute("UPDATE accounts SET last_synced_at=?, last_error='' WHERE id=?", (now, account_id))


def get_account(channel, platform):
    """Return the private account row for channel/platform, or None."""
    connection = get_connection()
    try:
        row = connection.execute("SELECT * FROM accounts WHERE channel = ? AND platform = ?", (channel, platform)).fetchone()
        return dict(row) if row else None
    finally:
        connection.close()


def access_token(account):
    """Return a usable token for account, refreshing TikTok if near expiry.

    Token lifetimes come from the provider response. Meta Facebook Login tokens
    need reconnection when expired; manually entered token expiry may be unknown.
    """
    credentials = decrypt_credentials(account["credentials"])
    expires = credentials.get("expires_at")
    if expires and expires < time.time() + 300:
        if account["platform"] != "tiktok" or not credentials.get("refresh_token"):
            raise ValueError("This account token has expired. Reconnect it on the channel page.")
        data = api_json("POST", TIKTOK_BASE + "/oauth/token/", data={
            "client_key": required_env("TIKTOK_CLIENT_KEY"), "client_secret": required_env("TIKTOK_CLIENT_SECRET"),
            "grant_type": "refresh_token", "refresh_token": credentials["refresh_token"],
        })
        credentials.update(data)
        credentials["expires_at"] = time.time() + data["expires_in"]
        connection = get_connection()
        try:
            connection.execute("UPDATE accounts SET credentials=? WHERE id=?", (encrypt_credentials(credentials), account["id"]))
            connection.commit()
        finally:
            connection.close()
    return credentials["access_token"]


def required_env(name):
    """Return non-placeholder environment setting name or raise a setup error."""
    value = os.getenv(name, "")
    if not value or value.startswith(("your-", "replace-")):
        raise ValueError(f"Add {name} to .env and restart the dashboard.")
    return value


def authorization_url(platform, state):
    """Return the official login URL for platform with random state protection."""
    if platform == "tiktok":
        parameters = {"client_key": required_env("TIKTOK_CLIENT_KEY"), "response_type": "code",
                      "scope": "user.info.basic,user.info.stats,video.list,video.upload",
                      "redirect_uri": required_env("TIKTOK_REDIRECT_URI"), "state": state}
        return "https://www.tiktok.com/v2/auth/authorize/?" + urlencode(parameters)
    if platform != "instagram":
        raise ValueError("Choose TikTok or Instagram.")
    parameters = {"client_id": required_env("META_APP_ID"), "response_type": "code",
                  "scope": "instagram_basic,instagram_manage_insights,pages_show_list,pages_read_engagement",
                  "redirect_uri": required_env("INSTAGRAM_REDIRECT_URI"), "state": state}
    version = graph_base().rsplit("/", 1)[-1]
    return f"https://www.facebook.com/{version}/dialog/oauth?" + urlencode(parameters)


def exchange_code(platform, code):
    """Exchange the one-use authorization code for platform credentials."""
    if platform == "tiktok":
        data = api_json("POST", TIKTOK_BASE + "/oauth/token/", data={
            "client_key": required_env("TIKTOK_CLIENT_KEY"), "client_secret": required_env("TIKTOK_CLIENT_SECRET"),
            "code": code, "grant_type": "authorization_code", "redirect_uri": required_env("TIKTOK_REDIRECT_URI"),
        })
    else:
        short = api_json("GET", graph_base() + "/oauth/access_token", params={
            "client_id": required_env("META_APP_ID"), "client_secret": required_env("META_APP_SECRET"),
            "code": code, "redirect_uri": required_env("INSTAGRAM_REDIRECT_URI"),
        })
        data = api_json("GET", graph_base() + "/oauth/access_token", params={
            "grant_type": "fb_exchange_token", "client_id": required_env("META_APP_ID"),
            "client_secret": required_env("META_APP_SECRET"), "fb_exchange_token": short["access_token"],
        })
    if not data.get("access_token") or not data.get("expires_in"):
        raise ValueError("The platform returned an incomplete token response. Reconnect the account.")
    data["expires_at"] = time.time() + int(data["expires_in"])
    return data

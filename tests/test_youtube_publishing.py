# =============================================================
# tests/test_youtube_publishing.py — YouTube Shorts uploads,
# Google login, statistics and the database upgrade
#
# Every request to Google is replaced by a fake, so these tests run
# offline and never upload anything.
# =============================================================

import json
import sqlite3
import time
from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from src.analytics import accounts as analytics_accounts
from src.dashboard import accounts
from src.publishing import scheduler, youtube
from src.publishing.errors import NothingPublishedError
from src.storage import control_schema, database, queries

CONFIG = {"publishing": {"hashtags": ["#news"]}, "youtube": {"privacy": "public", "category_id": 25}}


@pytest.fixture
def video_file(tmp_path):
    """Create a small fake MP4 file and return its path as text."""
    path = tmp_path / "story.mp4"
    path.write_bytes(b"fake mp4 bytes")
    return str(path)


def fake_response(status, body=None, headers=None):
    """Make a fake httpx answer with a status code, JSON body and headers."""
    request = httpx.Request("POST", "https://example.test")
    return httpx.Response(status, json=body or {}, headers=headers or {}, request=request)


# ---------------------------------------------------------------------------
# Uploading
# ---------------------------------------------------------------------------

def test_upload_sends_details_then_file(video_file, monkeypatch):
    """Step 1 sends the details; step 2 PUTs the file to the returned address."""
    start = Mock(return_value=fake_response(200, headers={"Location": "https://upload.test/abc"}))
    put = Mock(return_value=fake_response(200, {"id": "vid12345678", "status": {"privacyStatus": "public"}}))
    monkeypatch.setattr(youtube.httpx, "post", start)
    monkeypatch.setattr(youtube.httpx, "put", put)

    result = youtube.upload_short(video_file, "Title", "Description", "token", CONFIG)

    assert result == {"id": "vid12345678", "public": True}
    details = json.loads(start.call_args.kwargs["content"])
    assert details["status"] == {"privacyStatus": "public", "selfDeclaredMadeForKids": False}
    assert details["snippet"]["categoryId"] == "25"
    assert start.call_args.kwargs["params"]["uploadType"] == "resumable"
    assert put.call_args.args[0] == "https://upload.test/abc"


def test_audit_locked_upload_is_not_public(video_file, monkeypatch):
    """When YouTube keeps the video private, it is not reported as public."""
    monkeypatch.setattr(youtube.httpx, "post", Mock(return_value=fake_response(200, headers={"Location": "https://u"})))
    monkeypatch.setattr(youtube.httpx, "put", Mock(return_value=fake_response(200, {"id": "v", "status": {"privacyStatus": "private"}})))
    assert youtube.upload_short(video_file, "T", "D", "token", CONFIG)["public"] is False


def test_failure_before_file_upload_is_safe_to_retry(video_file, monkeypatch):
    """A refused first step means no video exists yet."""
    refused = fake_response(403, {"error": {"message": "quota exceeded"}})
    monkeypatch.setattr(youtube.httpx, "post", Mock(return_value=refused))
    put = Mock()
    monkeypatch.setattr(youtube.httpx, "put", put)
    with pytest.raises(NothingPublishedError, match="quota exceeded"):
        youtube.upload_short(video_file, "T", "D", "token", CONFIG)
    assert put.call_count == 0


def test_failure_during_file_upload_is_uncertain(video_file, monkeypatch):
    """A failure while sending the file may have created the video."""
    monkeypatch.setattr(youtube.httpx, "post", Mock(return_value=fake_response(200, headers={"Location": "https://u"})))
    monkeypatch.setattr(youtube.httpx, "put", Mock(side_effect=httpx.ReadTimeout("slow")))
    with pytest.raises(httpx.ReadTimeout):
        youtube.upload_short(video_file, "T", "D", "token", CONFIG)


def test_invalid_privacy_is_refused(video_file):
    """Only public, unlisted and private are accepted."""
    with pytest.raises(NothingPublishedError):
        youtube.upload_short(video_file, "T", "D", "token", {"youtube": {"privacy": "everyone"}})


def test_title_and_description_limits():
    """Titles are cut to 100 characters; descriptions get the source and #Shorts."""
    assert len(youtube.build_title("x" * 150)) == 100
    description = youtube.build_description("Headline", "https://news.test/a", CONFIG)
    assert "Source: https://news.test/a" in description
    assert description.endswith("#news #Shorts")


# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------

@pytest.fixture
def approved_video(tmp_path, monkeypatch, video_file):
    """Create a temporary database with one approved, rendered video; return its ID."""
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "test.db"))
    database.create_tables()
    video_id = queries.save_video("news", "Test story", "https://example.com", video_file, 0.02)
    queries.approve_video(video_id)
    queries.set_video_path(video_id, video_file)
    monkeypatch.setattr(scheduler, "get_credentials", Mock(return_value={"token": "t", "account_id": "UC1"}))
    return video_id


def public_posts():
    """Return (platform, post_id) pairs saved as confirmed public posts."""
    connection = database.get_connection()
    rows = connection.execute("SELECT platform, post_id FROM platform_posts").fetchall()
    connection.close()
    pairs = []
    for row in rows:
        pairs.append((row["platform"], row["post_id"]))
    return pairs


def test_public_youtube_upload_is_recorded(approved_video, monkeypatch):
    """A public upload counts as posted and is saved for statistics."""
    monkeypatch.setattr(youtube, "upload_short", Mock(return_value={"id": "vid12345678", "public": True}))
    messages, fully_posted = scheduler.publish_video_now("news", {"platforms": ["youtube"]}, approved_video)
    assert fully_posted
    assert public_posts() == [("youtube", "vid12345678")]


def test_private_youtube_upload_succeeds_but_is_not_a_public_post(approved_video, monkeypatch):
    """A private (unaudited) upload is delivered, but not recorded as public."""
    monkeypatch.setattr(youtube, "upload_short", Mock(return_value={"id": "vid12345678", "public": False}))
    messages, fully_posted = scheduler.publish_video_now("news", {"platforms": ["youtube"]}, approved_video)
    assert fully_posted
    assert public_posts() == []
    assert "private" in messages[0] and "YouTube Studio" in messages[0]


# ---------------------------------------------------------------------------
# Google login
# ---------------------------------------------------------------------------

def test_login_address_asks_for_offline_access(monkeypatch):
    """The Google login asks for both scopes and a refresh token."""
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "client-id")
    monkeypatch.setenv("YOUTUBE_REDIRECT_URI", "http://localhost:8000/oauth/youtube/callback")
    address = accounts.authorization_url("youtube", "state-1")
    query = parse_qs(urlsplit(address).query)
    assert address.startswith("https://accounts.google.com/")
    assert query["access_type"] == ["offline"] and query["prompt"] == ["consent"]
    assert "youtube.upload" in query["scope"][0] and "youtube.readonly" in query["scope"][0]


def test_expired_youtube_token_is_refreshed_and_saved(monkeypatch):
    """An expiring YouTube token is renewed with the refresh token and saved."""
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "client-id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("SESSION_SECRET", "youtube-test-secret-at-least-32-characters")
    monkeypatch.delenv("ACCOUNT_TOKEN_KEY", raising=False)
    old = {"access_token": "old", "refresh_token": "refresh-1", "expires_at": time.time() - 10}
    account = {"id": 7, "platform": "youtube", "credentials": accounts.encrypt_credentials(old)}
    monkeypatch.setattr(accounts, "api_json", Mock(return_value={"access_token": "new", "expires_in": 3599}))
    saved = Mock()
    monkeypatch.setattr(accounts, "save_credentials", saved)

    assert accounts.access_token(account) == "new"
    saved_credentials = saved.call_args.args[1]
    assert saved_credentials["refresh_token"] == "refresh-1"     # kept
    assert saved_credentials["expires_at"] > time.time()


def test_hidden_subscriber_count_is_refused(monkeypatch):
    """A channel hiding its subscribers is refused rather than shown as 0."""
    monkeypatch.setattr(accounts, "api_json", Mock(return_value={"items": [
        {"id": "UC1", "snippet": {"title": "News"}, "statistics": {"hiddenSubscriberCount": True}}]}))
    with pytest.raises(ValueError, match="statistics"):
        accounts.fetch_profile("youtube", "token")


def test_youtube_profile_reads_channel(monkeypatch):
    """The channel's ID, title and subscriber count are read."""
    monkeypatch.setattr(accounts, "api_json", Mock(return_value={"items": [
        {"id": "UC1", "snippet": {"title": "News"}, "statistics": {"subscriberCount": "42"}}]}))
    assert accounts.fetch_profile("youtube", "token") == {"id": "UC1", "name": "News", "followers": 42}


def test_youtube_statistics_have_no_fake_shares(monkeypatch):
    """Views, likes and comments are read; shares stay empty."""
    monkeypatch.setattr(analytics_accounts, "api_json", Mock(return_value={"items": [
        {"statistics": {"viewCount": "100", "likeCount": "5", "commentCount": "2"}}]}))
    assert analytics_accounts.fetch_post_metrics("youtube", "vid12345678", "token") == {
        "views": 100, "likes": 5, "comments": 2, "shares": None}


# ---------------------------------------------------------------------------
# Upgrading an older database
# ---------------------------------------------------------------------------

def test_old_database_is_upgraded_without_losing_records(tmp_path, monkeypatch):
    """Old tables gain 'youtube'; accounts and their follower history survive."""
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "old.db"))
    old_sql = control_schema.CONTROL_TABLES_SQL.replace(
        control_schema.NEW_PLATFORM_RULE, control_schema.OLD_PLATFORM_RULE)
    monkeypatch.setattr(control_schema, "CONTROL_TABLES_SQL", old_sql)
    database.create_tables()   # builds the OLD layout, then upgrades it

    # A database from before the upgrade: the old accounts rule, with a
    # follower snapshot linked to account 5.
    connection = sqlite3.connect(str(tmp_path / "legacy.db"))
    connection.executescript(f"""
        CREATE TABLE accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT, channel TEXT NOT NULL,
            platform TEXT NOT NULL {control_schema.OLD_PLATFORM_RULE},
            platform_account_id TEXT NOT NULL, display_name TEXT NOT NULL,
            credentials TEXT NOT NULL, connected_at TEXT NOT NULL,
            UNIQUE(channel, platform));
        CREATE TABLE account_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
            day TEXT NOT NULL, followers INTEGER NOT NULL, collected_at TEXT NOT NULL);
    """)
    connection.execute("INSERT INTO accounts (id, channel, platform, platform_account_id, display_name, "
                       "credentials, connected_at) VALUES (5, 'news', 'tiktok', 'a1', 'Name', 'x', 'now')")
    connection.execute("INSERT INTO account_snapshots (account_id, day, followers, collected_at) "
                       "VALUES (5, '2026-11-16', 10, 'now')")
    connection.commit()

    control_schema.allow_youtube_platform(connection, "accounts")
    control_schema.allow_youtube_platform(connection, "accounts")   # second run: no change

    sql = connection.execute("SELECT sql FROM sqlite_master WHERE name = 'accounts'").fetchone()[0]
    assert "'youtube'" in sql
    assert connection.execute("SELECT id FROM accounts").fetchall() == [(5,)]
    connection.execute("PRAGMA foreign_keys = ON")
    assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    assert connection.execute("SELECT followers FROM account_snapshots WHERE account_id = 5").fetchone() == (10,)
    connection.execute("INSERT INTO accounts (channel, platform, platform_account_id, display_name, "
                       "credentials, connected_at) VALUES ('news', 'youtube', 'UC1', 'YT', 'x', 'now')")
    connection.close()

    # The app's own start-up upgrade also left the main database accepting YouTube.
    main = database.get_connection()
    main_sql = main.execute("SELECT sql FROM sqlite_master WHERE name = 'platform_posts'").fetchone()[0]
    main.close()
    assert "'youtube'" in main_sql

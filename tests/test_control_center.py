# =============================================================
# Control-center regression tests. Temporary databases and mocked
# providers prove privacy, budgeting, source-first generation and
# account isolation without spending money or touching real files.
# =============================================================

import json
import re
import shutil
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from src.analytics.collector import save_metrics
from src.dashboard import accounts, auth, data, jobs, media, settings, worker
from src.dashboard.app import create_app
from src.storage import database
from src.storage.database import create_tables, get_connection
from src.storage.finance import CostTracker, add_money_entry, money_summary, utc_now
from src.storage.queries import approve_video, get_video_by_id, save_video, set_video_path

PROJECT = Path(__file__).resolve().parents[1]
SCRIPT = """# Test story
## Hook
A public library will open next week.
## Story
The city announced a new library with twenty study rooms.
## Why it matters
Residents will have another place to read and study.
## What happens next
The opening ceremony takes place next week.
## Closing line
The library will publish its opening hours soon.
## Sources
- [Public source](https://example.com/article)
"""


@pytest.fixture
def portal(tmp_path, monkeypatch):
    """Return a logged-in client and temporary workspace; block real provider calls."""
    monkeypatch.chdir(tmp_path)
    channels = tmp_path / "channels"
    channels.mkdir()
    shutil.copy(PROJECT / "channels/news.yaml", channels / "news.yaml")
    shutil.copy(PROJECT / "channels/news.yaml", channels / "second.yaml")
    (tmp_path / "config").mkdir()
    shutil.copy(PROJECT / "config/pricing.yaml", tmp_path / "config/pricing.yaml")
    config_path = tmp_path / "dashboard.yaml"
    config_path.write_text("jobs:\n  daily_limit_usd: 2\n  max_in_progress: 1\n")
    monkeypatch.setattr(settings, "CHANNEL_FOLDER", channels)
    monkeypatch.setattr(settings, "SETTINGS_PATH", config_path)
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setattr(media, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(media, "OUTPUT_ROOT", tmp_path / "output")
    monkeypatch.setattr(media, "MEDIA_CACHE", tmp_path / "cache/dashboard")
    monkeypatch.setattr(auth, "BCRYPT_ROUNDS", 4)
    monkeypatch.setenv("SESSION_SECRET", "control-center-test-secret-at-least-32-characters")
    monkeypatch.setenv("DASHBOARD_AUTO_COLLECT", "false")
    monkeypatch.delenv("ACCOUNT_TOKEN_KEY", raising=False)
    monkeypatch.setattr(accounts, "api_json", Mock(side_effect=AssertionError("No live platform calls in tests")))
    monkeypatch.setattr(jobs, "launch_job", Mock())
    create_tables()
    auth.set_password("tester", "test-password-12345")
    client = TestClient(create_app())
    client.post("/login", data={"username": "tester", "password": "test-password-12345"})
    return client, tmp_path


def token(client):
    """Return the form token from the logged-in client's overview."""
    return re.search(r'name="form_token" value="([^"]+)"', client.get("/").text).group(1)


def draft(root, channel="news", title="Library opens", cost=0.02):
    """Save a test script/video record in root for channel; return its ID."""
    folder = root / "output" / channel
    folder.mkdir(parents=True, exist_ok=True)
    script = folder / "article.md"
    script.write_text(SCRIPT)
    return save_video(channel, title, "https://example.com/article", str(script), cost)


def execute(sql, values=()):
    """Execute test setup SQL with values, commit it, and return fetched rows."""
    connection = get_connection()
    try:
        cursor = connection.execute(sql, values)
        rows = cursor.fetchall()
        connection.commit()
        return rows
    finally:
        connection.close()


def connect(channel="news", platform="tiktok", account_id="person-1"):
    """Save a fake verified account for tests and return its local ID."""
    return accounts.save_connection(channel, platform, {"id": account_id, "name": "Test account", "followers": 120},
                                    {"access_token": "private-platform-token-123"})


def test_new_pages_are_private(portal):
    """Logged-out browsers cannot inspect new pages, jobs, thumbnails or videos."""
    client, _ = portal
    client.cookies.clear()
    for path in ("/money", "/library", "/new", "/jobs", "/jobs/fake/status", "/channels/news",
                 "/media/video/videos/news/fake.mp4", "/media/thumbnail/videos/news/fake.mp4", "/oauth/tiktok/callback"):
        response = client.get(path, follow_redirects=False)
        assert response.status_code == 303, path
        assert response.headers["location"] == "/login"


def test_pages_render_without_accounts_or_metrics(portal):
    """All new pages have useful empty states, and no made-up follower totals."""
    client, _ = portal
    for path in ("/", "/channels", "/channels/news", "/money", "/library", "/new", "/jobs"):
        response = client.get(path)
        assert response.status_code == 200, (path, response.text)
        assert response.headers["cache-control"] == "no-store"
    assert data.overview()["totals"]["followers"] is None
    assert "No history yet" in client.get("/").text


def test_every_new_mutation_checks_csrf(portal):
    """Invalid form tokens reject every new action before changing state."""
    client, _ = portal
    actions = [
        ("/money", {"channel": "news", "platform": "tiktok", "kind": "income", "amount": "1", "entry_date": "2026-11-10"}),
        ("/money/1/delete", {}), ("/channels/news/sync", {}),
        ("/channels/news/accounts/tiktok/token", {}),
        ("/channels/news/accounts/tiktok/connect", {}),
        ("/channels/news/accounts/tiktok/disconnect", {}),
        ("/new/discover", {"channel": "news"}),
        ("/new/generate", {"channel": "news"}),
        ("/video/1/render", {}),
        ("/video/1/published", {"platform": "tiktok", "post_id": "123"}),
    ]
    for path, fields in actions:
        assert client.post(path, data=fields).status_code == 403, path


def test_totals_use_latest_metrics_and_confirmed_posts(portal):
    """Repeated cumulative snapshots do not inflate views or public post counts."""
    _, root = portal
    video_id = draft(root)
    save_metrics(video_id, "tiktok", views=10)
    save_metrics(video_id, "tiktok", views=30)
    save_metrics(video_id, "instagram", views=7)
    execute("INSERT INTO publishing_deliveries VALUES (?, 'tiktok', 'succeeded', 'inbox-id')", (video_id,))
    totals = data.overview()["totals"]
    assert totals["views"] == 37
    assert totals["posted"] == 0
    execute("INSERT INTO platform_posts VALUES (?, 'tiktok', '123456', ?)", (video_id, utc_now()))
    assert data.overview()["totals"]["posted"] == 1


def test_income_and_spending_do_not_double_count(portal):
    """Legacy totals plus new tracked calls and manual entries give exact totals."""
    _, root = portal
    video_id = draft(root, cost=0.2)
    tracker = CostTracker("news", video_id=video_id)
    event_id = tracker.reserve("writing", "test", 0.15)
    tracker.settle(event_id, 0.03, 100, 50)
    add_money_entry("news", "tiktok", "income", "10.25", "2026-11-10", "Payout", "payout-1")
    add_money_entry("news", "other", "expense", "1.00", "2026-11-10", "Voice reconciliation", "expense-1")
    assert get_video_by_id(video_id)["total_cost_usd"] == pytest.approx(0.23)
    totals = money_summary()["totals"]
    assert totals["expense"] == pytest.approx(1.23)
    assert totals["income"] == 10.25
    assert totals["profit"] == pytest.approx(9.02)


def test_attach_costs_is_idempotent(portal):
    """Attaching pre-script costs to a saved video cannot add them twice."""
    _, root = portal
    tracker = CostTracker("news")
    event = tracker.reserve("writing", "model", 0.05)
    tracker.settle(event, 0.02)
    video_id = draft(root, cost=0)
    tracker.attach_video(video_id)
    tracker.attach_video(video_id)
    assert get_video_by_id(video_id)["total_cost_usd"] == pytest.approx(0.02)
    assert money_summary()["totals"]["expense"] == pytest.approx(0.02)


@pytest.mark.parametrize("amount", ["NaN", "Infinity", "-1", "0", "1.001", "1000001"])
def test_bad_money_values_rejected(portal, amount):
    """Money forms reject non-finite, negative, excessive and fractional-cent values."""
    with pytest.raises(ValueError):
        add_money_entry("news", "tiktok", "income", amount, "2026-11-10", "", "bad")
    assert execute("SELECT * FROM money_entries") == []


def test_duplicate_money_submission_saved_once(portal):
    """Refreshing the submitted money form cannot duplicate a payout."""
    for _ in range(2):
        add_money_entry("news", "tiktok", "income", "3.00", "2026-11-10", "", "same-form")
    assert money_summary()["totals"]["income"] == 3


def test_database_migration_preserves_old_records(portal):
    """Repeated additive startup migrations preserve existing content and costs."""
    _, root = portal
    video_id = draft(root)
    create_tables()
    create_tables()
    assert get_video_by_id(video_id)["story_title"] == "Library opens"


def test_token_encryption_and_account_isolation(portal):
    """Saved tokens are encrypted and one account cannot belong to two channels."""
    client, _ = portal
    connect()
    account = accounts.get_account("news", "tiktok")
    assert "private-platform-token" not in account["credentials"]
    assert accounts.decrypt_credentials(account["credentials"])["access_token"] == "private-platform-token-123"
    assert "private-platform-token" not in client.get("/channels/news").text
    assert accounts.get_account("second", "tiktok") is None
    with pytest.raises(ValueError, match="already connected"):
        connect("second")


def test_rotated_secret_requires_reconnection(portal, monkeypatch):
    """Changing the encryption secret never exposes old credentials."""
    connect()
    encrypted = accounts.get_account("news", "tiktok")["credentials"]
    monkeypatch.setenv("SESSION_SECRET", "a-different-secret-that-is-over-32-characters")
    with pytest.raises(ValueError, match="Reconnect"):
        accounts.decrypt_credentials(encrypted)


def test_account_reconnect_preserves_daily_history(portal):
    """Reauthorizing the same account keeps history and one snapshot per day."""
    connect()
    connect()
    assert len(execute("SELECT * FROM account_snapshots")) == 1
    assert data.overview()["totals"]["followers"] == 120
    with pytest.raises(ValueError, match="Disconnect"):
        connect(account_id="a-different-person")


def test_oauth_state_is_browser_bound_and_one_use(portal, monkeypatch):
    """Wrong state fails before token exchange; valid state cannot be replayed."""
    client, _ = portal
    form_token = token(client)
    execute("INSERT INTO oauth_states VALUES ('good', 'news', 'tiktok', '', ?, ?)", (form_token, time.time()))
    exchange = Mock(return_value={"access_token": "oauth-token"})
    monkeypatch.setattr(accounts, "exchange_code", exchange)
    monkeypatch.setattr(accounts, "fetch_profile", Mock(return_value={"id": "user", "name": "User", "followers": 0}))
    assert client.get("/oauth/tiktok/callback?state=wrong&code=x").status_code == 400
    exchange.assert_not_called()
    assert client.get("/oauth/tiktok/callback?state=good&code=x", follow_redirects=False).status_code == 303
    assert client.get("/oauth/tiktok/callback?state=good&code=x").status_code == 400
    assert exchange.call_count == 1


def test_oauth_expired_state_rejected(portal):
    """A stale OAuth callback cannot store credentials."""
    client, _ = portal
    execute("INSERT INTO oauth_states VALUES ('expired', 'news', 'tiktok', '', ?, ?)", (token(client), time.time()-601))
    assert client.get("/oauth/tiktok/callback?state=expired&code=x").status_code == 400


def test_tiktok_refresh_rotates_saved_token(portal, monkeypatch):
    """Refreshing uses the returned lifetime and replacement refresh token."""
    account_id = connect()
    encrypted = accounts.encrypt_credentials({"access_token": "old", "refresh_token": "refresh-old", "expires_at": 1})
    execute("UPDATE accounts SET credentials=? WHERE id=?", (encrypted, account_id))
    monkeypatch.setenv("TIKTOK_CLIENT_KEY", "key")
    monkeypatch.setenv("TIKTOK_CLIENT_SECRET", "secret")
    api = Mock(return_value={"access_token": "new", "refresh_token": "refresh-new", "expires_in": 7200})
    monkeypatch.setattr(accounts, "api_json", api)
    assert accounts.access_token(accounts.get_account("news", "tiktok")) == "new"
    saved = accounts.decrypt_credentials(accounts.get_account("news", "tiktok")["credentials"])
    assert saved["refresh_token"] == "refresh-new"
    assert saved["expires_at"] > time.time() + 7100


def test_collection_once_a_day_and_real_post_ids_only(portal, monkeypatch):
    """The collector skips inbox IDs and does not re-query today's public metrics."""
    from src.analytics import accounts as collector
    _, root = portal
    video_id = draft(root)
    connect()
    execute("INSERT INTO publishing_deliveries VALUES (?, 'tiktok', 'succeeded', 'inbox-id')", (video_id,))
    fetch = Mock(return_value={"views": 50, "likes": 2, "comments": 1, "shares": 0})
    monkeypatch.setattr(collector, "fetch_post_metrics", fetch)
    collector.sync_channel("news")
    fetch.assert_not_called()
    execute("INSERT INTO platform_posts VALUES (?, 'tiktok', '12345', ?)", (video_id, utc_now()))
    collector.sync_channel("news")
    collector.sync_channel("news")
    fetch.assert_called_once_with("tiktok", "12345", "private-platform-token-123")
    assert len(execute("SELECT * FROM metrics")) == 1


def test_missing_metrics_are_errors_not_zero(portal, monkeypatch):
    """Missing API data never silently becomes zero engagement."""
    from src.analytics import accounts as collector
    monkeypatch.setattr(collector, "api_json", Mock(return_value={"data": {"videos": []}}))
    with pytest.raises(ValueError):
        collector.fetch_post_metrics("tiktok", "123", "secret")


def test_video_files_and_ranges_require_login(portal):
    """Private media supports seeking while refusing local files outside output."""
    client, root = portal
    folder = root / "output/videos/news"
    folder.mkdir(parents=True)
    (folder / "example.mp4").write_bytes(b"0123456789")
    response = client.get("/media/video/videos/news/example.mp4", headers={"range": "bytes=2-5"})
    assert response.status_code == 206
    assert response.content == b"2345"
    assert response.headers["cache-control"] == "no-store"
    outside = root / "secret.mp4"
    outside.write_bytes(b"secret")
    (folder / "link.mp4").symlink_to(outside)
    assert client.get("/media/video/videos/news/link.mp4").status_code == 404
    assert media.requested_video("../secret.mp4") is None
    assert media.requested_video(str(outside)) is None


def test_library_finds_untracked_files_and_filters(portal, monkeypatch):
    """Untracked files are flagged, and search/channel/status filters combine."""
    client, root = portal
    video_id = draft(root, title="Library story")
    folder = root / "output/videos/news"
    folder.mkdir(parents=True)
    (folder / "untracked-film.mp4").write_bytes(b"video")
    monkeypatch.setattr(media, "video_duration", Mock(return_value=12.5))
    result = media.library(status="untracked", channel="news", search="film")
    assert len(result["items"]) == 1
    assert result["items"][0]["id"] is None
    assert "Not in database" in client.get("/library?status=untracked").text
    assert media.library(search="Library")["items"][0]["id"] == video_id


def test_saved_script_path_cannot_read_secrets(portal):
    """A bad database path cannot expose a file outside output to the browser."""
    client, root = portal
    secret = root / "secrets.md"
    secret.write_text("PRIVATE SECRET VALUE")
    video_id = save_video("news", "Bad path", "javascript:alert(1)", str(secret), 0)
    response = client.get(f"/video/{video_id}")
    assert "PRIVATE SECRET VALUE" not in response.text
    assert 'href="javascript:' not in response.text


def test_job_double_click_is_idempotent(portal):
    """The same submission returns the existing job even when capacity is full."""
    first, created = jobs.enqueue_job("news", "discover", {}, False, "one")
    second, new = jobs.enqueue_job("news", "discover", {}, False, "one")
    assert first == second and created and not new
    with pytest.raises(ValueError, match="progress"):
        jobs.enqueue_job("news", "discover", {}, False, "two")


def test_daily_budget_includes_legacy_costs_and_reservations(portal):
    """A new job cannot overcommit today's money allowance."""
    _, root = portal
    draft(root, cost=1.99)
    with pytest.raises(ValueError, match="today"):
        jobs.enqueue_job("news", "generate", {"url": "https://example.com/new"}, False, "paid")
    job_id, _ = jobs.enqueue_job("news", "generate", {"url": "https://example.com/new"}, True, "free")
    assert jobs.job_record(job_id)["budget_usd"] == 0


def test_per_call_budget_blocks_before_paying(portal):
    """Calls that exceed the allowance are rejected before adding a charge."""
    tracker = CostTracker("news", budget=0.02)
    with pytest.raises(ValueError, match="spending cap"):
        tracker.reserve("writing", "test", 0.03)
    assert execute("SELECT * FROM cost_events") == []


def test_uncertain_call_keeps_its_allowance_visible(portal):
    """A lost response remains counted and visible for manual cost reconciliation."""
    tracker = CostTracker("news", budget=1)
    tracker.reserve("writing", "test", 0.2)
    entry = money_summary()["entries"][0]
    assert entry["state"] == "uncertain"
    assert entry["expense"] == 0.2


def test_interrupted_job_is_not_retried(portal):
    """Missing worker heartbeats produce an explicit interrupted state."""
    job_id, _ = jobs.enqueue_job("news", "discover", {}, False, "stale")
    execute("UPDATE jobs SET heartbeat_at='2000-01-01T00:00:00+00:00' WHERE id=?", (job_id,))
    jobs.recover_stale_jobs()
    assert jobs.job_record(job_id)["status"] == "interrupted"
    worker.run_job(job_id)
    assert jobs.job_record(job_id)["status"] == "interrupted"


def test_generation_requires_source_before_paid_call(portal, monkeypatch):
    """Empty source material fails before writing or creating a video record."""
    monkeypatch.setattr("src.sources.public_web.download_public_html", Mock(return_value="<p>Too short.</p>"))
    writer = Mock()
    monkeypatch.setattr("src.generation.writer.write_script", writer)
    job_id, _ = jobs.enqueue_job("news", "generate", {"url": "https://example.com/new"}, False, "empty-source")
    worker.run_job(job_id)
    assert jobs.job_record(job_id)["status"] == "failed"
    assert "too little source" in jobs.job_record(job_id)["error"]
    writer.assert_not_called()
    assert execute("SELECT * FROM videos") == []


def test_generation_dry_run_has_no_paid_calls_or_drafts(portal, monkeypatch):
    """Dry runs inspect sources but skip script generation, voice and rendering."""
    monkeypatch.setattr(worker, "article_for_story", Mock(return_value="source text " * 50))
    writer = Mock(side_effect=AssertionError("Paid writer must not run"))
    monkeypatch.setattr("src.generation.writer.write_script", writer)
    job_id, _ = jobs.enqueue_job("news", "generate", {"url": "https://example.com/new", "render": True}, True, "dry")
    worker.run_job(job_id)
    assert jobs.job_record(job_id)["status"] == "succeeded"
    assert execute("SELECT * FROM videos") == []
    assert execute("SELECT * FROM cost_events") == []
    writer.assert_not_called()


def test_generation_success_saves_draft_and_never_publishes(portal, monkeypatch):
    """A sourced generation job ends drafted, with a link to its saved script."""
    _, root = portal
    monkeypatch.setattr(worker, "article_for_story", Mock(return_value="source " * 40))
    folder = root / "output/news"
    folder.mkdir(parents=True)
    script = folder / "generated.md"
    script.write_text(SCRIPT)
    monkeypatch.setattr("src.generation.writer.write_script", Mock(return_value=str(script)))
    job_id, _ = jobs.enqueue_job("news", "generate", {"url": "https://example.com/new", "render": False}, False, "generated")
    worker.run_job(job_id)
    job = jobs.job_record(job_id)
    assert job["status"] == "succeeded"
    assert get_video_by_id(job["video_id"])["status"] == "drafted"
    assert execute("SELECT * FROM publishing_deliveries") == []


def test_render_failure_preserves_draft_and_charges(portal, monkeypatch):
    """Rendering failure keeps its saved script and paid-call ledger entry."""
    _, root = portal
    video_id = draft(root)
    approve_video(video_id)
    def failed_render(*args, **kwargs):
        """Simulate a paid voice succeeding before the encoder fails."""
        tracker = kwargs["cost_tracker"]
        event = tracker.reserve("voiceover", "test", 0.02)
        tracker.settle(event, 0.01)
        raise RuntimeError("FFmpeg test failure")
    monkeypatch.setattr("src.video.render.render_video", failed_render)
    job_id, _ = jobs.enqueue_job("news", "render", {"video_id": video_id, "free_voice": True}, False, "render")
    assert get_video_by_id(video_id)["status"] == "drafted"
    approve_video(video_id)
    assert get_video_by_id(video_id)["status"] == "drafted"
    worker.run_job(job_id)
    assert jobs.job_record(job_id)["status"] == "failed"
    assert get_video_by_id(video_id)["total_cost_usd"] == pytest.approx(0.03)
    assert get_video_by_id(video_id)["script_path"]


def test_render_success_returns_to_review(portal, monkeypatch):
    """A newly rendered approved script requires approval of its finished video."""
    _, root = portal
    video_id = draft(root)
    approve_video(video_id)
    folder = root / "output/videos/news"
    folder.mkdir(parents=True)
    path = folder / "render.mp4"
    path.write_bytes(b"test video")
    monkeypatch.setattr("src.video.render.render_video", Mock(return_value=str(path)))
    job_id, _ = jobs.enqueue_job("news", "render", {"video_id": video_id, "free_voice": True}, False, "render-success")
    worker.run_job(job_id)
    assert jobs.job_record(job_id)["status"] == "succeeded"
    assert get_video_by_id(video_id)["status"] == "drafted"
    assert get_video_by_id(video_id)["video_path"] == str(path)


def test_previously_published_video_cannot_be_rerendered(portal):
    """A confirmed public post cannot have its local video replaced in a job."""
    _, root = portal
    video_id = draft(root)
    execute("INSERT INTO platform_posts VALUES (?, 'tiktok', '123', ?)", (video_id, utc_now()))
    with pytest.raises(ValueError, match="publishing history"):
        jobs.enqueue_job("news", "render", {"video_id": video_id, "free_voice": True}, False, "published")
    assert execute("SELECT * FROM jobs") == []


def test_channel_path_traversal_rejected(portal):
    """Channel names cannot escape the YAML directory."""
    with pytest.raises(ValueError):
        settings.channel_config("../secrets")


@pytest.mark.parametrize("url", ["file:///etc/passwd", "http://user:password@example.com", "http://127.0.0.1", "http://[::1]", "http://10.0.0.1", "http://169.254.169.254", "http://example.com:8000"])
def test_article_fetch_rejects_local_targets(portal, url):
    """User-supplied article URLs cannot read files, credentials or local services."""
    from src.sources.public_web import public_address
    with pytest.raises(ValueError):
        public_address(url)


def test_article_redirect_is_rechecked(portal, monkeypatch):
    """A public page redirecting to a private address is refused before connecting."""
    from src.sources import public_web
    from urllib.parse import urlsplit
    validator = Mock(side_effect=[(urlsplit("http://example.com"), "93.184.216.34", 80), ValueError("blocked redirect")])
    monkeypatch.setattr(public_web, "public_address", validator)
    response = Mock(status=302)
    response.getheader.return_value = "http://127.0.0.1/admin"
    connection = Mock()
    connection.getresponse.return_value = response
    factory = Mock(return_value=connection)
    monkeypatch.setattr(public_web.http.client, "HTTPConnection", factory)
    with pytest.raises(ValueError, match="blocked redirect"):
        public_web.download_public_html("http://example.com")
    assert factory.call_count == 1
    assert validator.call_args.args[0] == "http://127.0.0.1/admin"


def test_free_jobs_work_after_daily_spending_limit(portal):
    """Spending limits never block source browsing or no-charge dry runs."""
    _, root = portal
    draft(root, cost=5)
    job_id, _ = jobs.enqueue_job("news", "discover", {}, False, "free-after-limit")
    assert jobs.job_record(job_id)["budget_usd"] == 0


def test_concurrent_requests_reserve_only_one_worker_slot(portal):
    """Two simultaneous heavy jobs cannot bypass the capacity guard."""
    from concurrent.futures import ThreadPoolExecutor
    def submit(number):
        """Attempt a uniquely named request and return whether it was accepted."""
        try:
            jobs.enqueue_job("news", "generate", {"url": f"https://example.com/story-{number}"},
                             True, f"parallel-{number}")
            return True
        except ValueError:
            return False
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(submit, [1, 2]))
    assert sum(results) == 1
    assert len(execute("SELECT * FROM jobs")) == 1


def test_job_form_and_status_endpoints(portal):
    """Submitting a form creates one job, redirects to progress, and supports polling."""
    client, _ = portal
    response = client.post("/new/discover", data={"form_token": token(client), "channel": "news",
                            "topic": "library", "submission_id": "browser-submit"}, follow_redirects=False)
    assert response.status_code == 303
    location = response.headers["location"]
    assert client.get(location).status_code == 200
    assert client.get(location + "/status").json()["status"] == "queued"
    jobs.launch_job.assert_called_once()


def test_duplicate_story_stops_before_paid_writing(portal, monkeypatch):
    """A source with an existing draft cannot be generated and charged again."""
    _, root = portal
    draft(root)
    article = Mock(side_effect=AssertionError("Should skip this already-processed source"))
    monkeypatch.setattr(worker, "article_for_story", article)
    job_id, _ = jobs.enqueue_job("news", "generate", {"url": "https://example.com/article"}, False, "duplicate")
    worker.run_job(job_id)
    assert "already has draft" in jobs.job_record(job_id)["error"]
    article.assert_not_called()
    assert len(execute("SELECT * FROM videos")) == 1


def test_actual_subprocess_worker_completes_a_free_job(portal):
    """A real child process can load the database and finish a dry run."""
    import os
    import subprocess
    import sys
    _, root = portal
    video_id = draft(root)
    job_id, _ = jobs.enqueue_job("news", "render", {"video_id": video_id}, True, "process")
    environment = os.environ.copy()
    environment["DATABASE_PATH"] = database.DB_PATH
    environment["PYTHONPATH"] = str(PROJECT)
    result = subprocess.run([sys.executable, "-m", "src.dashboard.worker", job_id], cwd=root,
                            env=environment, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert jobs.job_record(job_id)["status"] == "succeeded"
    assert execute("SELECT * FROM cost_events") == []
    assert get_video_by_id(video_id)["video_path"] is None


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="FFmpeg is optional in CI")
def test_real_video_thumbnail_and_duration(portal):
    """A tiny local MP4 exercises real FFmpeg thumbnails, duration and cache reuse."""
    import subprocess
    _, root = portal
    folder = root / "output/videos/news"
    folder.mkdir(parents=True)
    path = folder / "preview.mp4"
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i",
                    "color=c=gray:s=90x160:d=1:r=2", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)],
                   capture_output=True, check=True, timeout=15)
    assert media.video_duration(path) == pytest.approx(1.0)
    preview = media.thumbnail(path)
    assert preview and preview.read_bytes().startswith(b"\xff\xd8")
    previous_time = preview.stat().st_mtime_ns
    assert media.thumbnail(path).stat().st_mtime_ns == previous_time


def test_unknown_model_does_not_make_an_unpriced_paid_call(portal, monkeypatch):
    """A model missing from pricing is refused before any API request."""
    from src.generation.llm import LLMClient, LLMError
    api = SimpleNamespace(messages=SimpleNamespace(create=Mock()))
    llm = LLMClient(api_client=api, cost_tracker=CostTracker("news"))
    with pytest.raises(LLMError, match="Add a price"):
        llm.ask("unknown-model", "system", "user", 100, "writing")
    api.messages.create.assert_not_called()


def test_voice_cost_is_recorded_before_saving_audio(portal, monkeypatch):
    """Paid voice usage remains recorded if decoding its returned audio fails."""
    from src.video import tts_elevenlabs
    _, root = portal
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-only-key")
    monkeypatch.setattr(tts_elevenlabs, "_get_voice_id", Mock(return_value="voice"))
    response = Mock()
    response.read.return_value = b'{}'
    context = Mock()
    context.__enter__ = Mock(return_value=response)
    context.__exit__ = Mock(return_value=False)
    monkeypatch.setattr(tts_elevenlabs.urllib.request, "urlopen", Mock(return_value=context))
    tracker = CostTracker("news")
    with pytest.raises(KeyError):
        tts_elevenlabs.synthesize_elevenlabs("A hundred characters are not needed.", "voice", str(root / "audio.mp3"),
                                          str(root / "timing.json"), tracker,
                                          {"elevenlabs_usd_per_1000_chars": 0.3})
    assert tracker.total > 0
    assert execute("SELECT * FROM cost_events")[0]["state"] == "recorded"


def test_record_public_post_updates_status_without_upload(portal):
    """Recording a manually published video updates its state without sending content."""
    client, root = portal
    video_id = draft(root)
    approve_video(video_id)
    response = client.post(f"/video/{video_id}/published", data={"form_token": token(client),
        "platform": "tiktok", "post_id": "123456789", "confirmed": "true"}, follow_redirects=False)
    assert response.status_code == 303
    assert get_video_by_id(video_id)["status"] == "posted"
    assert data.overview()["totals"]["posted"] == 1
    assert execute("SELECT * FROM publishing_deliveries") == []


def test_same_source_id_and_url_cannot_run_in_parallel(portal):
    """Different selection methods still reserve the same source only once."""
    _, root = portal
    settings.SETTINGS_PATH.write_text("jobs:\n  max_in_progress: 2\n")
    execute("INSERT INTO source_stories (channel,url,title,source,fetched_at) VALUES ('news', 'https://example.com/article', 'Story', 'Source', ?)", (utc_now(),))
    story_id = execute("SELECT id FROM source_stories")[0]["id"]
    jobs.enqueue_job("news", "generate", {"story_id": story_id}, True, "source-id")
    with pytest.raises(ValueError, match="story already has a job"):
        jobs.enqueue_job("news", "generate", {"url": "https://example.com/article#section"}, True, "source-url")


def test_history_carries_latest_snapshot_forward(portal):
    """A missing collection day carries forward the last count without adding it."""
    from datetime import datetime, timedelta, timezone
    _, root = portal
    video_id = draft(root)
    previous_day = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat(timespec="seconds")
    save_metrics(video_id, "tiktok", views=10)
    save_metrics(video_id, "tiktok", views=15)
    execute("UPDATE metrics SET collected_at=?", (previous_day,))
    chart = data.history_chart("views", "news", days=5)
    assert chart["points"][0]["value"] is None
    assert chart["points"][-1]["value"] == 15
    assert chart["points"][-2]["value"] == 15


# ---------------------------------------------------------------------------
# Review fixes (November 2026)
# ---------------------------------------------------------------------------

def test_free_jobs_run_beside_a_heavy_job(portal):
    """Finding stories and collecting statistics still work while a video renders."""
    _, root = portal
    video_id = draft(root)
    jobs.enqueue_job("news", "render", {"video_id": video_id, "free_voice": True}, True, "busy-render")
    jobs.enqueue_job("news", "discover", {}, False, "find-while-rendering")
    jobs.enqueue_job("news", "sync", {}, False, "sync-while-rendering")
    with pytest.raises(ValueError, match="already being written or rendered"):
        jobs.enqueue_job("news", "generate", {"url": "https://example.com/other"}, True, "second-heavy")
    with pytest.raises(ValueError, match="already in progress"):
        jobs.enqueue_job("news", "discover", {}, False, "second-find")


def test_rejected_video_can_be_approved_again(portal):
    """Rejecting is not final: a rejected draft can be approved later."""
    from src.storage.queries import reject_video
    _, root = portal
    video_id = draft(root)
    assert reject_video(video_id) is True
    assert get_video_by_id(video_id)["status"] == "failed"
    assert approve_video(video_id) is True
    assert get_video_by_id(video_id)["status"] == "approved"


def test_approve_reports_when_nothing_changed(portal):
    """approve_video returns False instead of silently ignoring a posted video."""
    _, root = portal
    video_id = draft(root)
    execute("UPDATE videos SET status = 'posted' WHERE id = ?", (video_id,))
    assert approve_video(video_id) is False


def test_unapproved_video_cannot_be_recorded_as_posted(portal):
    """A draft that was never reviewed cannot be marked as publicly posted."""
    client, root = portal
    video_id = draft(root)
    response = client.post(f"/video/{video_id}/published", data={"form_token": token(client),
        "platform": "tiktok", "post_id": "123456789", "confirmed": "true"})
    assert response.status_code == 400
    assert "Approve this video" in response.text
    assert get_video_by_id(video_id)["status"] == "drafted"
    assert execute("SELECT * FROM platform_posts") == []


def test_old_local_times_are_converted_to_utc(portal):
    """Startup converts old timezone-less timestamps so sorting and charts agree."""
    _, root = portal
    video_id = draft(root)
    execute("UPDATE videos SET created_at = '2026-11-09T23:44:21' WHERE id = ?", (video_id,))
    create_tables()
    saved = get_video_by_id(video_id)["created_at"]
    assert saved.endswith("+00:00")
    assert len(saved) == 25
    create_tables()
    assert get_video_by_id(video_id)["created_at"] == saved


def test_pasted_token_shows_unknown_expiry(portal):
    """A token pasted by hand is flagged on the channel page; a login token is not."""
    client, _ = portal
    connect()
    account = data.account_rows("news")[0]
    assert account["token_expires_at"] is None
    assert "expiry unknown" in account["token_note"]
    assert "expiry unknown" in client.get("/channels/news").text
    accounts.save_connection("news", "tiktok", {"id": "person-1", "name": "Test", "followers": 5},
                             {"access_token": "token", "expires_at": time.time() + 3600})
    assert "renews automatically" in data.account_rows("news")[0]["token_note"]


def test_topic_search_adds_web_news_to_feed_matches(portal, monkeypatch):
    """A topic the feeds don't mention is still found through web news search."""
    feed = [{"title": "Election update", "url": "https://feed.example/1", "source": "NPR",
             "summary": "Votes counted", "published_ts": 0.0}]
    found = [{"title": "Nvidia shares rise", "url": "https://web.example/2", "source": "example.com",
              "summary": "", "published_ts": time.time()}]
    monkeypatch.setattr("src.main.get_candidate_stories", Mock(return_value=feed))
    search = Mock(return_value=(found, []))
    monkeypatch.setattr("src.sources.news_search.search_news", search)
    job_id, _ = jobs.enqueue_job("news", "discover", {"topic": "NVIDIA"}, False, "topic-search")
    worker.run_job(job_id)
    job = jobs.job_record(job_id)
    assert job["status"] == "succeeded", job["error"]
    assert "0 from this channel's feeds and 1 from web news search" in job["result"]["reason"]
    story = worker.saved_story("news", job["result"]["story_ids"][0])
    assert story["url"] == "https://web.example/2"
    assert story["published"].endswith("+00:00")

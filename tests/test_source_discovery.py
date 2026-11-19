# =============================================================
# Offline public-source discovery and manual attachment regression tests.
# HTTP answers, credentials, clocks and media probes are local fixtures.
# =============================================================

from unittest.mock import Mock

import httpx
import pytest
import yaml

from src.clips import acquisition, automation, discovery, finder, monitor, recordings, store
from src.clips.demo import demo_config
from src.clips.settings import load_clip_settings
from src.dashboard import jobs, worker
from src.sources import kick, twitch
from src.storage import database
from tests.test_control_center import execute, portal, token


@pytest.fixture(autouse=True)
def offline_twitch(monkeypatch):
    """Install dummy app credentials and block unmocked HTTP for each test; return nothing."""
    monkeypatch.setenv("TWITCH_CLIENT_ID", "offline-client")
    monkeypatch.setenv("TWITCH_CLIENT_SECRET", "offline-secret")
    monkeypatch.setattr(twitch, "_app_token_cache", {})
    monkeypatch.setattr(httpx, "get", Mock(side_effect=AssertionError("Unexpected HTTP GET")))
    monkeypatch.setattr(httpx, "post", Mock(side_effect=AssertionError("Unexpected HTTP POST")))
    monkeypatch.setattr(twitch, "get_download_links", Mock(side_effect=AssertionError("No download API")))
    monkeypatch.setattr(twitch, "download_file", Mock(side_effect=AssertionError("No media downloads")))


def configure_discovery(root):
    """Write a Twitch discovery channel under the temporary root and return its YAML dictionary."""
    config = demo_config()
    config["streamers"][0]["platform"] = "twitch"
    config["automation"]["enabled"] = True
    config["acquisition"] = {"only": True, "max_upload_mb": 1}
    (root / "channels/clips_demo.yaml").write_text(yaml.safe_dump(config))
    return config


def clip_metadata(views=15):
    """Return a complete synthetic Helix clip dictionary with the requested view count."""
    return {"id": "DemoClip", "url": "https://clips.twitch.tv/DemoClip", "title": "Demo reaction",
            "creator_name": "TheClipper", "view_count": views, "created_at": "2026-11-17T12:00:00Z",
            "duration": 30.5, "thumbnail_url": "https://example.com/thumbnail.jpg", "vod_offset": 500,
            "video_id": "123", "game_id": "456"}


def mock_metadata(monkeypatch, views=15):
    """Mock public Twitch discovery for one clip and VOD; return the configured clip response mock."""
    monkeypatch.setattr(twitch, "resolve_user", Mock(return_value={
        "id": "999", "login": "shortsengine_demo", "display_name": "ShortsEngineDemo"}))
    clips = Mock(return_value={"items": [clip_metadata(views)], "truncated": False})
    monkeypatch.setattr(twitch, "discover_clips", clips)
    monkeypatch.setattr(twitch, "discover_videos", Mock(return_value={"items": [{
        "id": "123", "url": "https://www.twitch.tv/videos/123", "title": "Demo archive",
        "created_at": "2026-11-17T10:00:00Z", "duration": "2h3m4s", "view_count": 7,
        "thumbnail_url": "https://example.com/vod.jpg"}], "truncated": False}))
    return clips


def test_app_token_fetch_cache_and_refresh_once(monkeypatch):
    """Mock a rejected app token followed by success; verify client credentials, cache replacement and one refresh."""
    post = Mock(side_effect=[httpx.Response(200, json={"access_token": "old", "expires_in": 3600}),
                             httpx.Response(200, json={"access_token": "new", "expires_in": 3600})])
    get = Mock(side_effect=[httpx.Response(401, json={}), httpx.Response(200, json={"data": [{"id": "999"}]})])
    monkeypatch.setattr(httpx, "post", post)
    monkeypatch.setattr(httpx, "get", get)
    assert twitch.resolve_user("ShortsEngine_Demo")["id"] == "999"
    assert post.call_count == 2
    assert post.call_args.kwargs["data"] == {"client_id": "offline-client", "client_secret": "offline-secret",
                                             "grant_type": "client_credentials"}
    assert get.call_args_list[0].kwargs["headers"]["Authorization"] == "Bearer old"
    assert get.call_args_list[1].kwargs["headers"]["Authorization"] == "Bearer new"
    assert get.call_args.kwargs["params"] == {"login": "shortsengine_demo"}
    assert twitch.get_app_token() == "new"
    assert post.call_count == 2


def test_second_401_stops_without_loop_or_secret_logging(monkeypatch):
    """Return repeated unauthorized answers; assert one refresh only and safe error text."""
    post = Mock(return_value=httpx.Response(200, json={"access_token": "private-token", "expires_in": 3600}))
    get = Mock(return_value=httpx.Response(401, json={"message": "private-token offline-secret"}))
    monkeypatch.setattr(httpx, "post", post)
    monkeypatch.setattr(httpx, "get", get)
    with pytest.raises(twitch.TwitchError, match="HTTP 401") as error:
        twitch.resolve_user("demo")
    assert post.call_count == get.call_count == 2
    assert "private-token" not in str(error.value) and "offline-secret" not in str(error.value)


def test_login_not_found_has_clear_error(monkeypatch):
    """Mock an empty public-user lookup and assert the configured login is identified in the error."""
    monkeypatch.setattr(twitch, "app_get", Mock(return_value={"data": []}))
    with pytest.raises(twitch.TwitchError, match="not found"):
        twitch.resolve_user("missing")


def test_clip_pagination_keeps_fixed_window_and_page_size(monkeypatch):
    """Read three mocked clip pages; assert cursors, first=100 and unchanged date filters on every request."""
    responses = [{"data": [{"id": "a"}], "pagination": {"cursor": "one"}},
                 {"data": [{"id": "b"}], "pagination": {"cursor": "two"}},
                 {"data": [{"id": "c"}], "pagination": {}}]
    calls = []

    def page(endpoint, params):
        """Record this metadata endpoint and a copy of its filters; return the next synthetic page."""
        calls.append((endpoint, dict(params)))
        return responses.pop(0)

    monkeypatch.setattr(twitch, "app_get", page)
    result = twitch.discover_clips("999", "start", "end")
    assert result == {"items": [{"id": "a"}, {"id": "b"}, {"id": "c"}], "truncated": False}
    for endpoint, params in calls:
        assert endpoint == "/clips"
        assert params["first"] == 100 and params["broadcaster_id"] == "999"
        assert params["started_at"] == "start" and params["ended_at"] == "end"
    assert "after" not in calls[0][1]
    assert calls[1][1]["after"] == "one" and calls[2][1]["after"] == "two"


def test_vod_pagination_and_bounded_partial_results(monkeypatch):
    """Mock an archive listing with more pages; assert archive-only filters and a visible truncation result."""
    getter = Mock(return_value={"data": [{"id": "123"}], "pagination": {"cursor": "next"}})
    monkeypatch.setattr(twitch, "app_get", getter)
    assert twitch.discover_videos("999", max_pages=1)["truncated"]
    getter.assert_called_once_with("/videos", {"user_id": "999", "type": "archive", "sort": "time", "first": 100})


def test_vod_cursor_reads_second_page(monkeypatch):
    """Mock two archive pages; assert both videos are returned and the second request uses the cursor."""
    getter = Mock(side_effect=[{"data": [{"id": "123"}], "pagination": {"cursor": "second"}},
                               {"data": [{"id": "124"}], "pagination": {}}])
    monkeypatch.setattr(twitch, "app_get", getter)
    result = twitch.discover_videos("999")
    assert result == {"items": [{"id": "123"}, {"id": "124"}], "truncated": False}
    assert getter.call_args.kwargs == {}
    assert getter.call_args.args[1]["after"] == "second"


def test_repeated_cursor_stops_pagination(monkeypatch):
    """Return a repeated cursor from Twitch; assert discovery fails safely rather than looping."""
    getter = Mock(return_value={"data": [], "pagination": {"cursor": "same"}})
    monkeypatch.setattr(twitch, "app_get", getter)
    with pytest.raises(twitch.TwitchError, match="repeated"):
        twitch.discover_videos("999")
    assert getter.call_count == 2


def test_429_respects_reset_then_retries(monkeypatch):
    """Mock a rate limit resetting in ten seconds; assert the next request follows the wait."""
    events = []
    responses = [httpx.Response(429, headers={"Ratelimit-Reset": "1010"}), httpx.Response(200, json={"data": []})]

    def get(url, **options):
        """Record the request order for this URL/options and return the next mocked HTTP response."""
        events.append("request")
        return responses.pop(0)

    monkeypatch.setattr(twitch, "get_app_token", Mock(return_value="token"))
    monkeypatch.setattr(twitch.time, "time", Mock(return_value=1000))
    monkeypatch.setattr(twitch.time, "sleep", events.append)
    monkeypatch.setattr(httpx, "get", get)
    assert twitch.app_get("/clips", {}) == {"data": []}
    assert events == ["request", 11, "request"]


def test_429_retries_are_bounded(monkeypatch):
    """Mock a persistent rate limit; assert at most three requests and two waits."""
    monkeypatch.setattr(twitch, "get_app_token", Mock(return_value="token"))
    monkeypatch.setattr(twitch.time, "time", Mock(return_value=1000))
    sleep = Mock()
    get = Mock(return_value=httpx.Response(429, headers={"Ratelimit-Reset": "1001"}))
    monkeypatch.setattr(twitch.time, "sleep", sleep)
    monkeypatch.setattr(httpx, "get", get)
    with pytest.raises(twitch.TwitchError, match="HTTP 429"):
        twitch.app_get("/clips", {})
    assert get.call_count == 3 and sleep.call_count == 2


def test_long_rate_limit_defers_instead_of_retrying_early(monkeypatch):
    """Return a distant reset time; assert no sleeping or premature HTTP retry occurs."""
    monkeypatch.setattr(twitch, "get_app_token", Mock(return_value="token"))
    monkeypatch.setattr(twitch.time, "time", Mock(return_value=1000))
    sleep = Mock()
    get = Mock(return_value=httpx.Response(429, headers={"Ratelimit-Reset": "2000"}))
    monkeypatch.setattr(twitch.time, "sleep", sleep)
    monkeypatch.setattr(httpx, "get", get)
    with pytest.raises(twitch.TwitchError, match="later"):
        twitch.app_get("/clips", {})
    sleep.assert_not_called()
    assert get.call_count == 1


def test_discovery_never_allows_download_endpoint():
    """Pass a media-download endpoint to the metadata client; assert rejection before any HTTP request."""
    with pytest.raises(twitch.TwitchError, match="Discovery only"):
        twitch.app_get("/clips/downloads", {})


def test_discovery_saves_credit_without_media_and_updates_views(portal, monkeypatch):
    """Run discovery twice in temporary SQLite; assert complete provenance, no bytes and stable identities."""
    client, root = portal
    config = configure_discovery(root)
    clips = mock_metadata(monkeypatch)
    assert discovery.discover_sources("clips_demo", config)["new"] == 2
    rows = execute("SELECT * FROM clips ORDER BY id")
    clip, vod = rows
    assert clip["source_type"] == "clip" and vod["source_type"] == "vod"
    assert clip["external_id"] == "DemoClip" and vod["external_id"] == "123"
    assert clip["platform"] == "twitch" and clip["streamer"] == "shortsengine_demo"
    assert clip["streamer_display_name"] == "ShortsEngineDemo" and clip["clipper"] == "TheClipper"
    assert clip["credit_text"] == "twitch.tv/shortsengine_demo"
    assert clip["clip_url"] == "https://clips.twitch.tv/DemoClip"
    assert clip["clip_created_at"] == "2026-11-17T12:00:00Z"
    assert clip["thumbnail_url"] == "https://example.com/thumbnail.jpg" and clip["game_id"] == "456"
    assert clip["vod_offset"] == 500 and clip["source_video_id"] == "123"
    assert clip["duration_seconds"] == 30.5 and vod["duration_seconds"] == 7384
    for row in rows:
        assert row["status"] == "discovered" and not row["file_path"] and not row["permission_confirmed"]
    clips.return_value["items"][0]["view_count"] = 90
    result = discovery.discover_sources("clips_demo", config)
    assert result["new"] == 0 and result["updated"] == 2
    assert len(execute("SELECT * FROM clips")) == 2
    assert store.get_clip(clip["id"])["view_count"] == 90
    assert acquisition.source_library("clips_demo", config)["stored_count"] == 0
    assert "<video" not in client.get("/clips/clips_demo/sources").text
    assert not list(root.rglob("*.mp4"))


@pytest.mark.parametrize("source_type", ["clip", "vod"])
def test_upload_attaches_to_same_discovery_and_repeated_run_preserves_it(portal, monkeypatch, source_type):
    """Upload mocked valid MP4 bytes to an existing clip/VOD; assert one library entry and persistent acquired state."""
    client, root = portal
    config = configure_discovery(root)
    mock_metadata(monkeypatch)
    discovery.discover_sources("clips_demo", config)
    source = execute("SELECT * FROM clips WHERE source_type=?", (source_type,))[0]
    monkeypatch.setattr(recordings, "probe_video", Mock(return_value={"duration": 30, "has_audio": True}))
    data = {"form_token": token(client), "username": "shortsengine_demo", "platform": "twitch",
            "confirmed": "true", "source_id": str(source["id"])}
    for name in ("original.mp4", "copy.mp4"):
        result = client.post("/clips/clips_demo/sources/upload", data=data,
                             files={"file": (name, b"synthetic media bytes", "video/mp4")}, follow_redirects=False)
        assert result.status_code == 303
    assert len(execute("SELECT * FROM clips")) == 2
    assert len(execute("SELECT * FROM recordings")) == 1
    acquired = store.get_clip(source["id"])
    assert acquired["status"] == "acquired" and acquired["permission_confirmed"] == 1
    assert acquired["recording_id"] and acquired["external_id"] == source["external_id"]
    library = acquisition.source_library("clips_demo", config)
    assert library["listed_count"] == 2 and library["stored_count"] == 1
    assert client.get(f"/clips/clips_demo/sources/clip/{source['id']}/file").content == b"synthetic media bytes"
    discovery.discover_sources("clips_demo", config)
    assert store.get_clip(source["id"])["status"] == "acquired"
    assert store.get_clip(source["id"])["file_path"] == acquired["file_path"]
    assert store.get_clip(source["id"])["permission_confirmed"] == 1
    assert jobs.recent_jobs() == []


def test_upload_rejects_wrong_channel_or_creator_before_import(portal, monkeypatch):
    """Select a discovery from another channel; assert attribution is rejected without storing video bytes."""
    client, root = portal
    config = configure_discovery(root)
    mock_metadata(monkeypatch)
    discovery.discover_sources("clips_demo", config)
    execute("UPDATE clips SET channel='different'")
    importer = Mock(side_effect=AssertionError("must not import"))
    monkeypatch.setattr(recordings, "import_recording", importer)
    response = client.post("/clips/clips_demo/sources/upload", data={
        "form_token": token(client), "username": "shortsengine_demo", "platform": "twitch",
        "confirmed": "true", "source_id": "1"}, files={"file": ("source.mp4", b"bytes", "video/mp4")})
    assert response.status_code == 400
    importer.assert_not_called()
    assert not execute("SELECT * FROM recordings")


def test_existing_discoveries_migrate_in_place(portal, monkeypatch):
    """Save an old finder row, run additive migration twice and discover it again; assert the original ID survives."""
    _, root = portal
    config = configure_discovery(root)
    old_id = store.save_found_clip("clips_demo", {"source": "twitch", "source_clip_id": "DemoClip",
        "streamer": "shortsengine_demo", "credit_text": "twitch.tv/shortsengine_demo"})
    database.create_tables()
    database.create_tables()
    assert store.get_clip(old_id)["status"] == "discovered"
    mock_metadata(monkeypatch)
    result = discovery.discover_sources("clips_demo", config)
    assert result["new"] == 1 and result["updated"] == 1
    assert store.get_clip(old_id)["external_id"] == "DemoClip"


def test_schedule_uses_existing_queue_and_six_hour_interval(portal, monkeypatch):
    """Call the supervisor's existing scheduler twice; assert a single free discovery job and a six-hour next check."""
    _, root = portal
    configure_discovery(root)
    before = twitch.time.time()
    automation.queue_due_channels()
    automation.queue_due_channels()
    discoveries = execute("SELECT * FROM jobs WHERE kind='clip_discovery'")
    assert len(discoveries) == 1 and discoveries[0]["budget_usd"] == 0
    next_check = execute("SELECT next_discovery FROM clip_automation")[0][0]
    assert before + 21600 <= next_check <= twitch.time.time() + 21600
    assert jobs.launch_job.call_count == 2  # One discovery and one existing inbox pipeline.


def test_discovery_custom_interval_and_stop_choice(portal):
    """Use a custom discovery interval and pause automation; assert scheduling honors both settings."""
    _, root = portal
    config = configure_discovery(root)
    config["discovery"] = {"interval_seconds": 3600}
    settings = load_clip_settings(config)
    automation.set_enabled("clips_demo", False)
    assert automation.queue_due_discovery("clips_demo", settings) == []
    automation.set_enabled("clips_demo", True)
    before = twitch.time.time()
    assert len(automation.queue_due_discovery("clips_demo", settings)) == 1
    assert before + 3600 <= execute("SELECT next_discovery FROM clip_automation")[0][0] <= twitch.time.time() + 3600


def test_disabled_discovery_does_not_queue_checks(portal):
    """Disable only scheduled metadata checks; assert the discovery scheduler creates no job."""
    _, root = portal
    config = configure_discovery(root)
    config["discovery"] = {"enabled": False}
    assert automation.queue_due_discovery("clips_demo", load_clip_settings(config)) == []
    assert jobs.recent_jobs() == []


def test_discovery_retry_after_queue_failure_does_not_wait_six_hours(portal, monkeypatch):
    """Reject job enqueue once; assert discovery is retried soon and the scheduling error is saved."""
    _, root = portal
    config = configure_discovery(root)
    monkeypatch.setattr(jobs, "enqueue_job", Mock(side_effect=ValueError("Already running")))
    before = twitch.time.time()
    assert automation.queue_due_discovery("clips_demo", load_clip_settings(config)) == []
    row = execute("SELECT * FROM clip_automation")[0]
    assert before + 300 <= row["next_discovery"] <= twitch.time.time() + 300
    assert "Already running" in row["last_error"]


def test_manual_button_queues_only_discovery_and_checks_csrf(portal):
    """Submit the source-page check button twice; assert a single free metadata job and CSRF protection."""
    client, root = portal
    configure_discovery(root)
    url = "/clips/clips_demo/sources/discover"
    assert client.post(url, data={"submission_id": "same"}).status_code == 403
    data = {"submission_id": "same", "form_token": token(client)}
    for attempt in range(2):
        assert client.post(url, data=data, follow_redirects=False).status_code == 303
    saved = jobs.recent_jobs()
    assert len(saved) == 1 and saved[0]["kind"] == "clip_discovery"
    assert jobs.job_record(saved[0]["id"])["budget_usd"] == 0


def test_discovery_worker_dry_run_makes_no_requests(portal):
    """Execute a real queued discovery dry run with forbidden HTTP; assert no network, media or source rows."""
    _, root = portal
    configure_discovery(root)
    job_id, _ = jobs.enqueue_job("clips_demo", "clip_discovery", {}, True, "offline-dry-run")
    result = worker.do_job_work(jobs.job_record(job_id), None, None)
    assert "No network" in result["message"]
    assert not execute("SELECT * FROM clips")


def test_pipeline_no_longer_calls_download_finder(portal, monkeypatch):
    """Run an acquisition-only inbox cycle; assert the legacy downloader is never called."""
    _, root = portal
    config = configure_discovery(root)
    downloader = Mock(side_effect=AssertionError("must not download"))
    monkeypatch.setattr(finder, "find_and_download", downloader)
    monkeypatch.setattr(monitor, "check_live_sources", Mock(return_value=[]))
    monkeypatch.setattr(recordings, "scan_inboxes", Mock(return_value=[]))
    assert automation.run_pipeline("clips_demo", config, None)["video_ids"] == []
    downloader.assert_not_called()


def test_creator_failure_does_not_block_other_sources(portal, monkeypatch):
    """Fail clips for one creator while VODs succeed; assert both saved metadata and source-specific errors."""
    _, root = portal
    config = configure_discovery(root)
    mock_metadata(monkeypatch)
    monkeypatch.setattr(twitch, "discover_clips", Mock(side_effect=twitch.TwitchError("HTTP 403")))
    result = discovery.discover_sources("clips_demo", config)
    assert result["new"] == 1 and "HTTP 403" in result["errors"][0]


def test_partial_results_are_visible_on_existing_job_page(portal, monkeypatch):
    """Reach a discovery page limit; assert its warning and source-library link appear on the existing job page."""
    client, root = portal
    config = configure_discovery(root)
    clips = mock_metadata(monkeypatch)
    clips.return_value["truncated"] = True
    job_id, _ = jobs.enqueue_job("clips_demo", "clip_discovery", {}, False, "partial-results")
    result = discovery.discover_sources("clips_demo", config)
    jobs.update_job(job_id, "Complete", 100, status="succeeded", result=result)
    page = client.get(f"/jobs/{job_id}")
    assert "results are partial" in page.text and "Open source library" in page.text


def test_library_pagination_keeps_older_discoveries_accessible(portal, monkeypatch):
    """Save more than one page of discoveries; assert older metadata and its upload choice remain reachable."""
    client, root = portal
    config = configure_discovery(root)
    streamer = load_clip_settings(config).streamers[0]
    for index in range(102):
        item = clip_metadata()
        item["id"] = f"clip-{index}"
        item["title"] = f"Discovery number {index}"
        store.save_discovered_source("clips_demo", discovery.source_details(item, "clip", streamer, {}))
    first = acquisition.source_library("clips_demo", config)
    assert first["listed_count"] == 100 and first["total_count"] == 102 and first["has_next"]
    second = acquisition.source_library("clips_demo", config, page=2)
    assert second["listed_count"] == 2 and not second["has_next"]
    page = client.get("/clips/clips_demo/sources?page=2&streamer=shortsengine_demo")
    assert page.status_code == 200 and "Discovery number 0" in page.text and "Previous sources" in page.text
    assert "Discovery number 101" not in page.text
    assert acquisition.source_library("clips_demo", config, streamer="another_creator")["listed_count"] == 0


@pytest.mark.parametrize("value,expected", [("2h3m4s", 7384), ("45m", 2700), ("8s", 8), ("1h", 3600)])
def test_vod_duration_units(value, expected):
    """Convert official hour/minute/second duration examples and assert exact totals."""
    assert discovery.vod_duration_seconds(value) == expected


def test_kick_discovery_stub_makes_no_requests():
    """Call the Kick metadata interface with HTTP blocked; assert explicit unsupported status and no invented entries."""
    result = kick.discover_sources("demo")
    assert result["items"] == [] and "unavailable" in result["message"]

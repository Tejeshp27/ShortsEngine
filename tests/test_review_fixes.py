# =============================================================
# Regression tests for publishing slots, cumulative analytics,
# and render spending. All uploads and renders are replaced by
# local fakes; no paid calls or public posts are made.
# =============================================================

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.storage import database, queries
from src.publishing import scheduler
from src.analytics import collector, feedback
from src import main


@pytest.fixture
def video_setup(tmp_path, monkeypatch):
    """Create a temporary database and approved video; return its ID and path."""
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "test.db"))
    database.create_tables()
    path = tmp_path / "story.md"
    path.write_text("## Hook\nA story.")
    video_id = queries.save_video("news", "Test story", "https://example.com", str(path), 0.02)
    queries.approve_video(video_id)
    queries.set_video_path(video_id, str(path))
    return video_id, path


def configure_scheduler(monkeypatch, platforms):
    """Set a fixed posting slot and fake credentials for the supplied platforms."""
    config = {"platforms": platforms, "schedule": {"post_times": ["09:00"]},
              "publishing": {"use_legacy_env_tokens": True}}
    monkeypatch.setattr(scheduler, "_load_all_channels", Mock(return_value=[("news", config)]))
    monkeypatch.setattr(scheduler, "find_posting_slot", Mock(return_value="2026-11-10T09:00:00+00:00"))
    monkeypatch.setenv("TIKTOK_ACCESS_TOKEN", "fake-test-token")


def test_slot_uses_named_timezone_and_midnight():
    """Named zones follow daylight saving time and include adjacent-day windows."""
    config = {"timezone": "America/New_York", "post_times": ["09:00"]}
    for month, hour in [(1, 14), (7, 13)]:
        now = datetime(2026, month, 10, hour, tzinfo=timezone.utc)
        assert scheduler.find_posting_slot(config, now) == now.isoformat()
    config = {"timezone": "UTC", "post_times": ["00:10"]}
    now = datetime(2026, 11, 9, 23, 50, tzinfo=timezone.utc)
    assert scheduler.find_posting_slot(config, now) == "2026-11-10T00:10:00+00:00"


def test_repeated_slot_uploads_only_one_video(video_setup, monkeypatch):
    """Repeated runs do not consume another approved video in the same slot."""
    from src.publishing import tiktok
    video_id, path = video_setup
    second = queries.save_video("news", "Second", "https://example.com/2", str(path), 0)
    queries.approve_video(second)
    queries.set_video_path(second, str(path))
    configure_scheduler(monkeypatch, ["tiktok"])
    upload = Mock(return_value="publish-123")
    monkeypatch.setattr(tiktok, "upload_to_drafts", upload)
    scheduler.run_scheduler()
    scheduler.run_scheduler()
    assert upload.call_count == 1
    statuses = [queries.get_video_by_id(video_id)["status"], queries.get_video_by_id(second)["status"]]
    assert sorted(statuses) == ["approved", "posted"]
    conn = database.get_connection()
    row = conn.execute("SELECT post_id FROM publishing_deliveries").fetchone()
    conn.close()
    assert row["post_id"] == "publish-123"


def test_failed_upload_stays_approved(video_setup, monkeypatch):
    """An uncertain upload stays approved and is not blindly sent again."""
    from src.publishing import tiktok
    configure_scheduler(monkeypatch, ["tiktok"])
    upload = Mock(side_effect=TimeoutError("lost response"))
    monkeypatch.setattr(tiktok, "upload_to_drafts", upload)
    scheduler.run_scheduler()
    scheduler.run_scheduler()
    assert upload.call_count == 1
    assert queries.get_video_by_id(video_setup[0])["status"] == "approved"


def test_partial_success_skips_completed_platform(video_setup, monkeypatch):
    """A platform without credentials (Instagram here) keeps a TikTok-only success approved."""
    from src.publishing import tiktok
    configure_scheduler(monkeypatch, ["tiktok", "instagram"])
    upload = Mock(return_value="publish-123")
    monkeypatch.setattr(tiktok, "upload_to_drafts", upload)
    scheduler.run_scheduler()
    scheduler.run_scheduler()
    assert upload.call_count == 1
    assert queries.get_video_by_id(video_setup[0])["status"] == "approved"


def test_scheduler_dry_run_does_not_reserve_slots(video_setup, monkeypatch):
    """Dry runs do not write slot or upload records."""
    configure_scheduler(monkeypatch, ["tiktok"])
    scheduler.run_scheduler(dry_run=True)
    conn = database.get_connection()
    assert conn.execute("SELECT COUNT(*) FROM publishing_slots").fetchone()[0] == 0
    conn.close()


def test_latest_snapshot_used_in_dashboard_and_feedback(video_setup):
    """Both readers use the newest snapshot, including same-second ties and decreases."""
    video_id, _ = video_setup
    queries.mark_posted(video_id)
    collector.save_metrics(video_id, "tiktok", views=100, likes=10)
    collector.save_metrics(video_id, "tiktok", views=90, likes=9)
    collector.save_metrics(video_id, "instagram", views=50, likes=5)
    summary = collector.get_metrics_summary("news")[0]
    assert summary["total_views"] == 140
    assert summary["total_likes"] == 14
    top, _ = feedback._load_top_and_bottom_stories("news")
    assert top[0]["total_views"] == 140


@pytest.mark.parametrize("fails", [False, True])
def test_render_cost_saved_even_after_failure(video_setup, monkeypatch, fails):
    """Render charges add to existing costs and are logged when FFmpeg fails."""
    from src.video import render
    video_id, _ = video_setup
    client = SimpleNamespace(total_cost=0.03, write_cost_log=Mock())
    monkeypatch.setattr("src.generation.llm.LLMClient", Mock(return_value=client))
    monkeypatch.setattr(main, "print_cost_summary", Mock())
    renderer = Mock(return_value="result.mp4")
    if fails:
        renderer.side_effect = RuntimeError("FFmpeg failed")
    monkeypatch.setattr(render, "render_video", renderer)
    args = SimpleNamespace(channel="news", dry_run=False, animated=True)
    config = {"video": {"voice": "test"}, "llm": {"max_calls_per_run": 5}}
    if fails:
        with pytest.raises(RuntimeError, match="FFmpeg failed"):
            main.run_render_pipeline(args, config)
    else:
        main.run_render_pipeline(args, config)
    assert queries.get_video_by_id(video_id)["total_cost_usd"] == pytest.approx(0.05)
    client.write_cost_log.assert_called_once()

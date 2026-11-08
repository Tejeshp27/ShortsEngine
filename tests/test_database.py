# =============================================================
# tests/test_database.py — tests for the database layer
#
# These tests run against a real (temporary) SQLite database so
# the SQL is actually executed. Each test creates a fresh DB file
# in a temp folder so tests never interfere with each other.
# =============================================================

import os
import sys
import tempfile

import pytest

# Point the database module at a temp file for all tests in this file.
# We do this by overriding the DB_PATH before importing the module.
import src.storage.database as db_module
import src.storage.queries as q_module


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    """
    Replace the real database with a temporary one for each test.

    'autouse=True' means this fixture runs automatically for every
    test in this file — no need to request it explicitly.

    'monkeypatch' is a pytest helper that lets us temporarily change
    a module-level variable and automatically restores it after the test.
    """
    temp_db_path = str(tmp_path / "test.db")
    monkeypatch.setattr(db_module, "DB_PATH", temp_db_path)
    monkeypatch.setattr(q_module, "get_connection", db_module.get_connection)
    db_module.create_tables()


def test_create_tables_is_idempotent():
    """Calling create_tables() twice should not raise an error."""
    db_module.create_tables()  # second call
    db_module.create_tables()  # third call


def test_save_video_returns_id():
    """Saving a new video should return a positive integer ID."""
    video_id = q_module.save_video(
        channel="news",
        story_title="Test story",
        story_url="https://example.com/story",
        script_path="output/news/2026-11-07-test.md",
        cost_usd=0.013,
    )
    assert isinstance(video_id, int)
    assert video_id > 0


def test_new_video_has_drafted_status():
    """A newly saved video should have status 'drafted'."""
    video_id = q_module.save_video(
        channel="news",
        story_title="Test story",
        story_url="https://example.com/story",
        script_path="output/news/2026-11-07-test.md",
        cost_usd=0.0,
    )
    video = q_module.get_video_by_id(video_id)
    assert video["status"] == "drafted"


def test_approve_video_changes_status():
    """Approving a video should change its status to 'approved'."""
    video_id = q_module.save_video(
        channel="news",
        story_title="Approvable story",
        story_url="https://example.com/approve",
        script_path="output/news/2026-11-07-approve.md",
        cost_usd=0.01,
    )
    q_module.approve_video(video_id, notes="Looks good")
    video = q_module.get_video_by_id(video_id)
    assert video["status"] == "approved"
    assert video["reviewer_notes"] == "Looks good"
    assert video["approved_at"] is not None


def test_reject_video_changes_status():
    """Rejecting a video should change its status to 'failed'."""
    video_id = q_module.save_video(
        channel="news",
        story_title="Bad story",
        story_url="https://example.com/bad",
        script_path="output/news/2026-11-07-bad.md",
        cost_usd=0.0,
    )
    q_module.reject_video(video_id, notes="Inaccurate")
    video = q_module.get_video_by_id(video_id)
    assert video["status"] == "failed"
    assert video["reviewer_notes"] == "Inaccurate"


def test_get_videos_by_status_filters_correctly():
    """get_videos_by_status should only return videos with that status."""
    id1 = q_module.save_video("news", "Story A", "https://a.com", "a.md", 0.01)
    id2 = q_module.save_video("news", "Story B", "https://b.com", "b.md", 0.02)
    q_module.approve_video(id1)

    drafted = q_module.get_videos_by_status("drafted")
    approved = q_module.get_videos_by_status("approved")

    drafted_ids = [v["id"] for v in drafted]
    approved_ids = [v["id"] for v in approved]

    assert id2 in drafted_ids
    assert id1 not in drafted_ids
    assert id1 in approved_ids
    assert id2 not in approved_ids


def test_get_videos_by_status_filters_by_channel():
    """Passing a channel should only return videos for that channel."""
    id_news = q_module.save_video("news", "News story", "https://n.com", "n.md", 0.0)
    id_tech = q_module.save_video("tech", "Tech story", "https://t.com", "t.md", 0.0)

    news_drafted = q_module.get_videos_by_status("drafted", channel="news")
    ids = [v["id"] for v in news_drafted]

    assert id_news in ids
    assert id_tech not in ids


def test_set_video_path_updates_row():
    """set_video_path should write the video file path to the database."""
    video_id = q_module.save_video("news", "Video story", "https://v.com", "v.md", 0.0)
    q_module.set_video_path(video_id, "output/videos/news/2026-11-07-video.mp4")
    video = q_module.get_video_by_id(video_id)
    assert video["video_path"] == "output/videos/news/2026-11-07-video.mp4"


def test_save_cost_event():
    """Saving a cost event should not raise an error."""
    video_id = q_module.save_video("news", "Cost story", "https://c.com", "c.md", 0.0)
    q_module.save_cost_event(
        video_id=video_id,
        channel="news",
        purpose="ranking",
        model="claude-haiku-4-5",
        input_tokens=2000,
        output_tokens=50,
        cost_usd=0.0022,
    )


def test_mark_posted_changes_status():
    """mark_posted should set status to 'posted' and record posted_at."""
    video_id = q_module.save_video("news", "Posted", "https://p.com", "p.md", 0.0)
    q_module.approve_video(video_id)
    q_module.mark_posted(video_id)
    video = q_module.get_video_by_id(video_id)
    assert video["status"] == "posted"
    assert video["posted_at"] is not None

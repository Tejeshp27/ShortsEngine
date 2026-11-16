# =============================================================
# tests/test_meta_publishing.py — Instagram + Facebook Reels publishing
#
# Every request to Meta is replaced by a fake (unittest.mock), so these
# tests run offline and never post anything. They check:
#   - the publishers call Meta's steps in the right order
#   - a failure before the final step is "safe to retry"
#   - the scheduler records results, never sends twice, and only marks
#     a video posted when every platform succeeded
# =============================================================

from unittest.mock import Mock

import pytest

from src.publishing import facebook, instagram, scheduler
from src.publishing.errors import NothingPublishedError
from src.storage import database, queries


@pytest.fixture
def video_file(tmp_path):
    """Create a small fake MP4 file and return its path as text."""
    path = tmp_path / "story.mp4"
    path.write_bytes(b"fake mp4 bytes")
    return str(path)


# ---------------------------------------------------------------------------
# Instagram publisher
# ---------------------------------------------------------------------------

def fake_instagram_graph(statuses):
    """Return a fake graph_call that answers like Meta, with the given processing statuses."""
    remaining = list(statuses)

    def fake_call(method, path, token, fields=None):
        if path.endswith("/media"):
            return {"id": "container-1"}
        if path == "/container-1":
            return {"status_code": remaining.pop(0)}
        if path.endswith("/media_publish"):
            return {"id": "reel-99"}
        raise AssertionError(f"Unexpected call {path}")
    return Mock(side_effect=fake_call)


def test_instagram_waits_for_processing_then_publishes(video_file, monkeypatch):
    """Create → upload → wait until FINISHED → publish, returning the Reel ID."""
    graph = fake_instagram_graph(["IN_PROGRESS", "FINISHED"])
    upload = Mock(return_value={"success": True})
    monkeypatch.setattr(instagram, "graph_call", graph)
    monkeypatch.setattr(instagram, "upload_video_file", upload)
    monkeypatch.setattr(instagram.time, "sleep", Mock())

    assert instagram.post_reel(video_file, "Caption", "token", "123") == "reel-99"

    paths = [call.args[1] for call in graph.call_args_list]
    assert paths == ["/123/media", "/container-1", "/container-1", "/123/media_publish"]
    assert graph.call_args_list[0].args[3]["upload_type"] == "resumable"
    assert "rupload.facebook.com/ig-api-upload" in upload.call_args.args[0]


def test_instagram_processing_error_is_safe_to_retry(video_file, monkeypatch):
    """If Instagram can't process the video, nothing is published."""
    graph = fake_instagram_graph(["ERROR"])
    monkeypatch.setattr(instagram, "graph_call", graph)
    monkeypatch.setattr(instagram, "upload_video_file", Mock())

    with pytest.raises(NothingPublishedError):
        instagram.post_reel(video_file, "Caption", "token", "123")
    paths = [call.args[1] for call in graph.call_args_list]
    assert "/123/media_publish" not in paths


def test_instagram_publish_step_failure_is_not_safe(video_file, monkeypatch):
    """A failure in the final publish step stays a normal (uncertain) error."""
    def fake_call(method, path, token, fields=None):
        if path.endswith("/media_publish"):
            raise RuntimeError("connection lost")
        if path.endswith("/media"):
            return {"id": "container-1"}
        return {"status_code": "FINISHED"}
    monkeypatch.setattr(instagram, "graph_call", Mock(side_effect=fake_call))
    monkeypatch.setattr(instagram, "upload_video_file", Mock())

    with pytest.raises(RuntimeError) as raised:
        instagram.post_reel(video_file, "Caption", "token", "123")
    assert not isinstance(raised.value, NothingPublishedError)


# ---------------------------------------------------------------------------
# Facebook publisher
# ---------------------------------------------------------------------------

PAGES = {"data": [
    {"id": "page-other", "access_token": "other-token", "instagram_business_account": {"id": "555"}},
    {"id": "page-1", "access_token": "page-token"},
    {"id": "page-2", "access_token": "right-token", "instagram_business_account": {"id": "123"}},
]}


def test_facebook_finds_page_linked_to_instagram(monkeypatch):
    """The Page whose linked Instagram ID matches is chosen, with its own token."""
    monkeypatch.setattr(facebook, "graph_call", Mock(return_value=PAGES))
    assert facebook.find_linked_page("user-token", "123") == {"id": "page-2", "token": "right-token"}
    with pytest.raises(RuntimeError, match="No Facebook Page"):
        facebook.find_linked_page("user-token", "999")


def test_facebook_reel_start_upload_finish(video_file, monkeypatch):
    """Start → upload → finish (PUBLISHED), all with the Page's token."""
    def fake_call(method, path, token, fields=None):
        if path == "/me/accounts":
            return PAGES
        if fields["upload_phase"] == "start":
            return {"video_id": "fb-7", "upload_url": "https://rupload.facebook.com/video-upload/v24.0/fb-7"}
        return {"success": True}
    graph = Mock(side_effect=fake_call)
    upload = Mock(return_value={"success": True})
    monkeypatch.setattr(facebook, "graph_call", graph)
    monkeypatch.setattr(facebook, "upload_video_file", upload)

    assert facebook.post_page_reel(video_file, "Caption", "user-token", "123") == "fb-7"
    finish = graph.call_args_list[-1]
    assert finish.args[1] == "/page-2/video_reels" and finish.args[2] == "right-token"
    assert finish.args[3]["video_state"] == "PUBLISHED"
    assert upload.call_args.args[2] == "right-token"


def test_facebook_without_linked_page_is_safe_to_retry(video_file, monkeypatch):
    """No linked Page means nothing was uploaded, so it may be retried."""
    monkeypatch.setattr(facebook, "graph_call", Mock(return_value={"data": []}))
    with pytest.raises(NothingPublishedError):
        facebook.post_page_reel(video_file, "Caption", "user-token", "123")


# ---------------------------------------------------------------------------
# Scheduler (shared by "Post now" and scheduled posting)
# ---------------------------------------------------------------------------

@pytest.fixture
def approved_video(tmp_path, monkeypatch, video_file):
    """Create a temporary database with one approved, rendered video; return its ID."""
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "test.db"))
    database.create_tables()
    video_id = queries.save_video("news", "Test story", "https://example.com", video_file, 0.02)
    queries.approve_video(video_id)
    queries.set_video_path(video_id, video_file)
    # Pretend the channel's accounts are connected.
    monkeypatch.setattr(scheduler, "get_credentials",
                        Mock(return_value={"token": "token", "account_id": "123"}))
    return video_id


CONFIG = {"platforms": ["instagram", "facebook"], "publishing": {"hashtags": ["#news"]}}


def delivery_statuses(video_id):
    """Return {platform: status} for a video's upload records."""
    connection = database.get_connection()
    rows = connection.execute(
        "SELECT platform, status FROM publishing_deliveries WHERE video_id = ?", (video_id,)).fetchall()
    connection.close()
    statuses = {}
    for row in rows:
        statuses[row["platform"]] = row["status"]
    return statuses


def test_post_now_publishes_everywhere_and_records_instagram_post(approved_video, monkeypatch):
    """Both platforms succeed → video posted, Instagram ID saved for statistics."""
    monkeypatch.setattr(instagram, "post_reel", Mock(return_value="reel-99"))
    monkeypatch.setattr(facebook, "post_page_reel", Mock(return_value="fb-7"))

    messages, fully_posted = scheduler.publish_video_now("news", CONFIG, approved_video)

    assert fully_posted
    assert queries.get_video_by_id(approved_video)["status"] == "posted"
    connection = database.get_connection()
    post = connection.execute("SELECT platform, post_id FROM platform_posts").fetchone()
    connection.close()
    assert (post["platform"], post["post_id"]) == ("instagram", "reel-99")


def test_failure_before_publishing_can_be_retried(approved_video, monkeypatch):
    """A safe failure releases the reservation; the next click retries only that platform."""
    instagram_upload = Mock(return_value="reel-99")
    facebook_upload = Mock(side_effect=[NothingPublishedError("no Page"), "fb-7"])
    monkeypatch.setattr(instagram, "post_reel", instagram_upload)
    monkeypatch.setattr(facebook, "post_page_reel", facebook_upload)

    messages, fully_posted = scheduler.publish_video_now("news", CONFIG, approved_video)
    assert not fully_posted
    assert "safe to try again" in messages[1]
    assert queries.get_video_by_id(approved_video)["status"] == "approved"

    messages, fully_posted = scheduler.publish_video_now("news", CONFIG, approved_video)
    assert fully_posted
    assert instagram_upload.call_count == 1      # Instagram was not sent twice
    assert facebook_upload.call_count == 2


def test_uncertain_failure_is_never_resent(approved_video, monkeypatch):
    """A failure at the final step stays 'uncertain' and is not sent again."""
    upload = Mock(side_effect=RuntimeError("connection lost"))
    monkeypatch.setattr(instagram, "post_reel", upload)
    config = {"platforms": ["instagram"]}

    scheduler.publish_video_now("news", config, approved_video)
    scheduler.publish_video_now("news", config, approved_video)

    assert upload.call_count == 1
    assert delivery_statuses(approved_video) == {"instagram": "uncertain"}
    assert queries.get_video_by_id(approved_video)["status"] == "approved"


def test_post_now_refuses_unapproved_video(approved_video):
    """Only approved videos can be posted."""
    connection = database.get_connection()
    connection.execute("UPDATE videos SET status = 'drafted' WHERE id = ?", (approved_video,))
    connection.commit()
    connection.close()
    with pytest.raises(ValueError, match="approved"):
        scheduler.publish_video_now("news", CONFIG, approved_video)


def test_missing_connection_leaves_video_approved(approved_video, monkeypatch):
    """Without a connected account nothing is reserved or sent."""
    monkeypatch.setattr(scheduler, "get_credentials", Mock(side_effect=ValueError("Connect Instagram first.")))
    upload = Mock()
    monkeypatch.setattr(facebook, "post_page_reel", upload)

    messages, fully_posted = scheduler.publish_video_now("news", {"platforms": ["facebook"]}, approved_video)

    assert not fully_posted and upload.call_count == 0
    assert delivery_statuses(approved_video) == {}


def test_facebook_uses_the_instagram_connection(monkeypatch):
    """Facebook has no connection of its own; it borrows Instagram's."""
    from src.dashboard import accounts
    asked = []
    monkeypatch.setattr(accounts, "get_account", lambda channel, platform: asked.append(platform) or None)
    with pytest.raises(ValueError, match="Instagram"):
        scheduler.get_credentials("news", {}, "facebook")
    assert asked == ["instagram"]


def test_channel_platforms_and_support_check():
    """Platforms come from the schedule first, without repeats; unknown ones are flagged."""
    config = {"platforms": ["tiktok", "tiktok", "facebook"], "schedule": {}}
    assert scheduler.channel_platforms(config) == ["tiktok", "facebook"]
    assert scheduler.channel_platforms({"platforms": ["x"], "schedule": {"platforms": ["instagram"]}}) == ["instagram"]
    assert scheduler.unsupported_platforms(["instagram", "myspace"]) == ["myspace"]

# =============================================================
# Official upload status contracts, without live credentials/uploads.
# Processing and inbox states must never become public-post counts.
# =============================================================

from unittest.mock import Mock

import httpx
import pytest

from src.publishing import verification
from tests.test_clip_runtime import runtime_space


@pytest.mark.parametrize("upload,privacy,expected", [("uploaded","public",False),
    ("processed","private",False), ("processed","public",True), ("failed","public",False)])
def test_youtube_requires_processed_public_video(monkeypatch, upload, privacy, expected):
    """Supply mocked YouTube processing/privacy states; assert only a processed public video receives a public URL."""
    reply = httpx.Response(200, json={"items":[{"status":{"uploadStatus":upload,"privacyStatus":privacy}}]},
                           request=httpx.Request("GET","https://www.googleapis.com/youtube/v3/videos"))
    monkeypatch.setattr(verification.httpx, "get", Mock(return_value=reply))
    result = verification.fetch_status("youtube","video-id","never-print-this-token")
    assert bool(result["public_url"]) == expected


@pytest.mark.parametrize("state", ["PROCESSING_UPLOAD", "SEND_TO_USER_INBOX", "FAILED", "PUBLISH_COMPLETE"])
def test_tiktok_without_public_id_is_not_public(monkeypatch, state):
    """Supply a TikTok upload/inbox status without public IDs; assert a public URL is never invented."""
    reply = httpx.Response(200,json={"data":{"status":state},"error":{"code":"ok"}},
                           request=httpx.Request("POST","https://open.tiktokapis.com"))
    request = Mock(return_value=reply)
    monkeypatch.setattr(verification.httpx,"post",request)
    result = verification.fetch_status("tiktok","publish-id","never-print-this-token")
    assert result["public_url"] == ""
    assert request.call_count == 1


def test_tiktok_uses_platform_share_url_after_public_confirmation(monkeypatch):
    """Mock TikTok's confirmed post and Display API URL; assert the exact returned share URL is retained."""
    request=httpx.Request("POST","https://open.tiktokapis.com")
    status=httpx.Response(200,json={"data":{"status":"PUBLISH_COMPLETE","publicaly_available_post_id":["123"]}},request=request)
    video=httpx.Response(200,json={"data":{"videos":[{"id":"123","share_url":"https://www.tiktok.com/@creator/video/123"}]}},request=request)
    monkeypatch.setattr(verification.httpx,"post",Mock(side_effect=[status,video]))
    result=verification.fetch_status("tiktok","publish-id","never-print-this-token")
    assert result["status"] == "public"
    assert result["public_url"] == "https://www.tiktok.com/@creator/video/123"


def add_delivery():
    """Save one simulated successful YouTube clip transfer in the isolated database; return its video ID."""
    from src.clips import store
    from src.storage.database import get_connection
    from src.storage.queries import save_video
    video_id=save_video("clips_demo","Synthetic test","example.com",None,0)
    clip_id=store.save_found_clip("clips_demo",{"source":"upload","source_clip_id":"test",
        "streamer":"demo","credit_text":"example.com/synthetic"})
    store.update_clip(clip_id,video_id=video_id)
    connection=get_connection()
    connection.execute("INSERT INTO publishing_deliveries VALUES (?, 'youtube','succeeded','test-id')",(video_id,))
    connection.commit()
    connection.close()
    return video_id


def test_verified_public_result_is_persisted_and_not_repolled(runtime_space, monkeypatch):
    """Verify a simulated accepted upload twice; assert public history is recorded once and the file is never resent."""
    from src.dashboard import accounts
    from src.storage.database import get_connection
    video_id=add_delivery()
    monkeypatch.setattr(accounts,"get_account",Mock(return_value={"id":1}))
    monkeypatch.setattr(accounts,"access_token",Mock(return_value="test-token"))
    check=Mock(return_value={"status":"public","public_url":"https://www.youtube.com/shorts/test-id","details":{}})
    monkeypatch.setattr(verification,"fetch_status",check)
    assert verification.verify_deliveries()[0]["status"] == "public"
    assert verification.verify_deliveries() == []
    check.assert_called_once()
    connection=get_connection()
    assert connection.execute("SELECT post_id FROM platform_posts WHERE video_id=?",(video_id,)).fetchone()[0] == "test-id"
    assert connection.execute("SELECT status FROM publishing_deliveries").fetchone()[0] == "succeeded"
    connection.close()


def test_verification_failure_keeps_upload_reservation(runtime_space, monkeypatch):
    """Simulate a disconnected account after transfer; assert pending verification never clears duplicate protection."""
    from src.dashboard import accounts
    from src.storage.database import get_connection
    add_delivery()
    monkeypatch.setattr(accounts,"get_account",Mock(return_value=None))
    assert verification.verify_deliveries()[0]["status"] == "verification_pending"
    connection=get_connection()
    assert connection.execute("SELECT status FROM publishing_deliveries").fetchone()[0] == "succeeded"
    assert connection.execute("SELECT COUNT(*) FROM platform_posts").fetchone()[0] == 0
    connection.close()


@pytest.mark.parametrize("status,waited,expected", [
    (None, 0, True),                       # never checked before
    ("uploaded", 60, False),               # still processing, checked a minute ago
    ("uploaded", 5 * 60, True),            # still processing, five minutes passed
    ("processed", 5 * 60, False),          # private YouTube video: waits for a person
    ("processed", 6 * 60 * 60, True),
    ("SEND_TO_USER_INBOX", 60 * 60, False),
    ("failed", 10 ** 7, False),            # finished answers are never asked again
    ("not_found", 10 ** 7, False),
    ("FAILED", 10 ** 7, False),
    ("public", 10 ** 7, False),
])
def test_check_interval_depends_on_last_answer(status, waited, expected):
    """Feed saved statuses and waiting times; assert stable or finished uploads are not polled every five minutes."""
    checked_at = None if status is None else 1000.0
    assert verification.is_due_for_check(status, checked_at, 1000.0 + waited) == expected


def test_private_youtube_upload_is_not_repolled_every_run(runtime_space, monkeypatch):
    """Verify a processed private upload twice in a row; assert the platform is asked only once (protects API quota)."""
    from src.dashboard import accounts
    add_delivery()
    monkeypatch.setattr(accounts,"get_account",Mock(return_value={"id":1}))
    monkeypatch.setattr(accounts,"access_token",Mock(return_value="test-token"))
    check=Mock(return_value={"status":"processed","public_url":"","details":{}})
    monkeypatch.setattr(verification,"fetch_status",check)
    assert verification.verify_deliveries()[0]["status"] == "processed"
    monkeypatch.setattr(verification.time,"time",Mock(return_value=verification.time.time() + 10 * 60))
    assert verification.verify_deliveries() == []
    check.assert_called_once()

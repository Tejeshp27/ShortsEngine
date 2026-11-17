# =============================================================
# Tests for deleting videos from the video library.
#
# Deleting must remove the record and its files, keep every dollar
# on the Money page, and refuse videos that were sent to a platform
# or that a job is still working on. Uses the same temporary
# workspace as the other control-center tests (no real files or APIs).
# =============================================================

from src.storage.finance import ledger, utc_now
from src.storage.queries import get_video_by_id, set_video_path
from tests.test_control_center import draft, execute, portal, token  # noqa: F401 (portal is a fixture)


def make_video_files(root, video_id):
    """
    Give a test video an MP4 and a storyboard next to its script.

    Args:
        root (Path): The temporary project folder.
        video_id (int): The video to attach the files to.

    Returns:
        tuple: (mp4 path, storyboard path, script path)
    """
    folder = root / "output/videos/news"
    folder.mkdir(parents=True, exist_ok=True)
    mp4 = folder / f"story-{video_id}.mp4"
    mp4.write_bytes(b"video")
    set_video_path(video_id, str(mp4))
    script = root / "output/news/article.md"
    storyboard = root / "output/news/article_storyboard.json"
    storyboard.write_text("[]")
    return mp4, storyboard, script


def total_spent():
    """Add up every expense on the Money page, in dollars (rounded to cents)."""
    total = 0.0
    for row in ledger():
        total += row["expense"]
    return round(total, 2)


def delete(client, video_id, confirmed=True):
    """Press the Delete button on a video's page and return the response."""
    form = {"form_token": token(client)}
    if confirmed:
        form["confirmed"] = "true"
    return client.post(f"/video/{video_id}/delete", data=form, follow_redirects=False)


def test_delete_removes_record_and_files_but_keeps_costs(portal):
    """The video, MP4, script and storyboard go; the money spent stays."""
    client, root = portal
    video_id = draft(root, cost=0.05)
    mp4, storyboard, script = make_video_files(root, video_id)
    # 3 cents of the 5-cent total were recorded per call; 2 cents are an older total.
    execute("INSERT INTO cost_events (video_id, channel, purpose, model, cost_usd, created_at) "
            "VALUES (?, 'news', 'script', 'test-model', 0.03, ?)", (video_id, utc_now()))
    assert total_spent() == 0.05

    response = delete(client, video_id)

    assert response.status_code == 303 and response.headers["location"] == "/library"
    assert get_video_by_id(video_id) is None
    assert not mp4.exists() and not script.exists() and not storyboard.exists()
    assert total_spent() == 0.05


def test_delete_needs_the_confirmation_box(portal):
    """Without the ticked box, nothing is deleted."""
    client, root = portal
    video_id = draft(root)
    mp4, _, _ = make_video_files(root, video_id)
    delete(client, video_id, confirmed=False)
    assert get_video_by_id(video_id) is not None and mp4.exists()


def test_video_sent_to_a_platform_cannot_be_deleted(portal):
    """Upload history is kept, so a video with any delivery is refused."""
    client, root = portal
    video_id = draft(root)
    mp4, _, _ = make_video_files(root, video_id)
    execute("INSERT INTO publishing_deliveries (video_id, platform, status) VALUES (?, 'tiktok', 'succeeded')",
            (video_id,))
    response = delete(client, video_id)
    assert "sent to a platform" in response.text
    assert get_video_by_id(video_id) is not None and mp4.exists()
    assert "Delete this video" not in client.get(f"/video/{video_id}").text


def test_video_with_running_job_cannot_be_deleted(portal):
    """A video that is being rendered is left alone."""
    client, root = portal
    video_id = draft(root)
    now = utc_now()
    execute("INSERT INTO jobs (id, channel, kind, status, payload, video_id, created_at, updated_at, "
            "heartbeat_at, submission_id) VALUES ('job-1', 'news', 'render', 'running', '{}', ?, ?, ?, ?, 'sub-1')",
            (video_id, now, now, now))
    response = delete(client, video_id)
    assert "job is still working" in response.text
    assert get_video_by_id(video_id) is not None


def test_shared_script_is_kept_for_the_other_video(portal):
    """Two videos from one script: deleting one keeps the script for the other."""
    client, root = portal
    first = draft(root, title="First render")
    second = draft(root, title="Second render")
    _, _, script = make_video_files(root, first)
    delete(client, first)
    assert get_video_by_id(first) is None
    assert get_video_by_id(second) is not None and script.exists()


def test_untracked_file_can_be_deleted(portal):
    """An MP4 with no database record can be deleted from its own page."""
    client, root = portal
    folder = root / "output/videos/news"
    folder.mkdir(parents=True)
    loose = folder / "loose-file.mp4"
    loose.write_bytes(b"video")
    page = client.get("/library/untracked", params={"path": "videos/news/loose-file.mp4"})
    assert "Delete this file" in page.text

    response = client.post("/library/untracked/delete", follow_redirects=False, data={
        "form_token": token(client), "path": "videos/news/loose-file.mp4", "confirmed": "true"})
    assert response.status_code == 303 and not loose.exists()


def test_untracked_delete_refuses_tracked_and_outside_files(portal):
    """The file-only delete can't remove a recorded video or anything outside output/."""
    client, root = portal
    video_id = draft(root)
    mp4, _, _ = make_video_files(root, video_id)
    secret = root / "keep.mp4"
    secret.write_bytes(b"not in output")

    tracked = client.post("/library/untracked/delete", data={
        "form_token": token(client), "path": f"videos/news/{mp4.name}", "confirmed": "true"})
    outside = client.post("/library/untracked/delete", data={
        "form_token": token(client), "path": "../keep.mp4", "confirmed": "true"})

    assert "belongs to a video record" in tracked.text and mp4.exists()
    assert outside.status_code == 404 and secret.exists()

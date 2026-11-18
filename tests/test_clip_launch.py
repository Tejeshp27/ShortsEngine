# =============================================================
# Launch fallback regression tests, using isolated media and SQLite.
# Check permission, duplicate prevention, review state and manual bundles.
# Unit tests fake media checks; the separate launch smoke uses real Whisper/FFmpeg.
# =============================================================

import json
from unittest.mock import Mock

import pytest

from src.clips import launch, maker, recordings, store
from src.clips.demo import demo_config
from src.clips.settings import StreamerNotApproved
from src.storage import database
from src.storage.queries import get_video_by_id, save_video, set_video_path


@pytest.fixture
def launch_space(tmp_path, monkeypatch):
    """
    Create a temporary database and free channel configuration for each launch test.

    Args:
        tmp_path: Pytest's temporary folder.
        monkeypatch: Pytest's helper for replacing paths and media calls.

    Returns:
        The temporary folder and configuration; no actual accounts or media are used.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "test.db"))
    database.create_tables()
    config = demo_config()
    config["clips"] = {"moment_min_seconds": 15, "moment_max_seconds": 60}
    monkeypatch.setattr(launch.quality, "check_export", Mock(return_value={
        "passed": True, "duration": 20, "errors": [], "warnings": [],
    }))
    return tmp_path, config


def add_draft(root, identity="one", content=b"fake checked video", start=0):
    """
    Save a fake rendered clip and its draft for export/permission tests.

    Args:
        root: The isolated temporary folder.
        identity: The fake source identity and filename suffix.
        content: Bytes used to test identical-file detection without actual rendering.
        start: The selected start time; the interval lasts 20 seconds.

    Returns:
        The draft video ID and linked clip ID.
    """
    folder = root / "output/videos"
    folder.mkdir(parents=True, exist_ok=True)
    video_path = folder / (identity + ".mp4")
    video_path.write_bytes(content)
    notes = root / "output" / (identity + ".md")
    notes.write_text("Original source transcript for review.")
    video_id = save_video("clips_demo", "Why is the timer running?", "example.com", str(notes), 0)
    set_video_path(video_id, str(video_path))
    clip_id = store.save_found_clip("clips_demo", {
        "source": "upload", "source_clip_id": identity, "streamer": "shortsengine_demo",
        "platform": "kick", "credit_text": "example.com/synthetic-demo", "duration_seconds": 100,
    })
    store.update_clip(clip_id, status="rendered", video_id=video_id, start_seconds=start,
                      end_seconds=start + 20, quality_json=json.dumps({"duration": 20}))
    return video_id, clip_id


@pytest.mark.parametrize("start,end", [(-1, 20), (0, 14), (0, 61), (20, 0),
                                      (float("nan"), 20), (0, float("inf"))])
def test_bad_timestamps_fail_before_import(launch_space, monkeypatch, start, end):
    """
    Reject invalid timestamp inputs before importing or transcribing any source.

    Args:
        launch_space: The isolated folder and free channel settings.
        monkeypatch: The test helper replacing source ingestion.
        start: An invalid proposed start in seconds.
        end: An invalid proposed end in seconds.

    Returns:
        Nothing; assertions confirm ingestion never starts.
    """
    _, config = launch_space
    importer = Mock(side_effect=AssertionError("must not import"))
    monkeypatch.setattr(recordings, "import_recording", importer)
    with pytest.raises(ValueError):
        launch.cut_recording("clips_demo", config, "missing.mp4", "shortsengine_demo", "kick",
                             start, end, "Truthful hook", True)
    importer.assert_not_called()


def test_revoked_source_blocks_cut_and_export(launch_space, monkeypatch):
    """
    Enforce current source permission before reading a recording or exporting its old draft.

    Args:
        launch_space: The isolated folder and free channel configuration.
        monkeypatch: The test helper replacing source ingestion.

    Returns:
        Nothing; neither file import nor export is allowed after revocation.
    """
    root, config = launch_space
    video_id, _ = add_draft(root)
    config["streamers"][0]["enabled"] = False
    importer = Mock(side_effect=AssertionError("must not import"))
    monkeypatch.setattr(recordings, "import_recording", importer)
    with pytest.raises(StreamerNotApproved):
        launch.cut_recording("clips_demo", config, "missing.mp4", "shortsengine_demo", "kick",
                             0, 20, "Truthful hook", True)
    with pytest.raises(StreamerNotApproved):
        launch.export_bundle("clips_demo", config, [video_id], "output/bundle")
    importer.assert_not_called()
    assert not (root / "output/bundle").exists()


def test_bundle_preserves_review_and_never_claims_uploads(launch_space):
    """
    Export usable metadata and exact media bytes while leaving the video drafted and uploads at zero.

    Args:
        launch_space: The isolated folder and free channel configuration.

    Returns:
        Nothing; assertions check bundle files, review status and truthful counts.
    """
    root, config = launch_space
    video_id, _ = add_draft(root)
    bundle = root / "output/bundle"
    result = launch.export_bundle("clips_demo", config, [video_id], bundle)
    saved = json.loads((bundle / "manifest.json").read_text())
    assert saved == result
    assert (bundle / f"clip-{video_id}.mp4").read_bytes() == b"fake checked video"
    text = (bundle / f"clip-{video_id}.txt").read_text()
    assert "#Shorts" in text and "example.com/synthetic-demo" in text
    assert (bundle / f"clip-{video_id}-review.md").is_file()
    assert "studio.youtube.com" in (bundle / "UPLOAD_INSTRUCTIONS.md").read_text()
    assert result["youtube_uploads"] == result["tiktok_uploads"] == 0
    assert result["public_urls"] == []
    assert get_video_by_id(video_id)["status"] == "drafted"


def test_warning_or_failed_media_blocks_entire_bundle(launch_space, monkeypatch):
    """
    Refuse silent or invalid exports before creating a folder that could look ready to publish.

    Args:
        launch_space: The isolated folder and free channel configuration.
        monkeypatch: The test helper replacing measured media results.

    Returns:
        Nothing; assertions verify an audio warning prevents all export writes.
    """
    root, config = launch_space
    video_id, _ = add_draft(root)
    monkeypatch.setattr(launch.quality, "check_export", Mock(return_value={
        "passed": True, "duration": 20, "errors": [], "warnings": ["Audio is silent"],
    }))
    with pytest.raises(ValueError, match="Audio is silent"):
        launch.export_bundle("clips_demo", config, [video_id], "output/bundle")
    assert not (root / "output/bundle").exists()


def test_duplicate_bytes_and_ids_are_refused(launch_space):
    """
    Keep identical media and repeated IDs out of a bundle meant to contain distinct clips.

    Args:
        launch_space: The isolated folder and free channel configuration.

    Returns:
        Nothing; both duplicate forms fail before bundle creation.
    """
    root, config = launch_space
    first, _ = add_draft(root)
    second, _ = add_draft(root, "two")
    with pytest.raises(ValueError, match="distinct video IDs"):
        launch.export_bundle("clips_demo", config, [first, first], "output/bundle")
    with pytest.raises(ValueError, match="identical bytes"):
        launch.export_bundle("clips_demo", config, [first, second], "output/bundle")
    assert not (root / "output/bundle").exists()


def test_export_never_overwrites_or_writes_outside_output(launch_space):
    """
    Keep source files and existing bundles intact by restricting export destinations.

    Args:
        launch_space: The isolated folder and free channel configuration.

    Returns:
        Nothing; invalid destinations are refused.
    """
    root, config = launch_space
    video_id, _ = add_draft(root)
    for path in (root / "outside", root / "output", root / "output/videos"):
        with pytest.raises(ValueError, match="new export folder"):
            launch.export_bundle("clips_demo", config, [video_id], path)


def test_dry_export_skips_media_and_writes(launch_space, monkeypatch):
    """
    Report an export plan without opening media, creating folders or pretending clips already exist.

    Args:
        launch_space: The isolated folder and free channel configuration.
        monkeypatch: The test helper replacing media inspection.

    Returns:
        Nothing; dry run produces only an explicit plan.
    """
    root, config = launch_space
    checker = Mock(side_effect=AssertionError("must not check"))
    monkeypatch.setattr(launch.quality, "check_export", checker)
    result = launch.export_bundle("clips_demo", config, [123], "output/bundle", True)
    assert result["dry_run"]
    assert not (root / "output/bundle").exists()
    checker.assert_not_called()


def test_cut_reuses_identical_draft_and_refuses_overlapping_moment(launch_space, monkeypatch):
    """
    Recover an identical selection without re-rendering and reject an overlapping second highlight.

    Args:
        launch_space: The isolated folder and free channel configuration.
        monkeypatch: The test helper replacing import, transcription and rendering.

    Returns:
        Nothing; assertions confirm the original ID is reused and repeated work is avoided.
    """
    root, config = launch_space
    identity = "recording:hash:0.00:20.00"
    video_id, clip_id = add_draft(root, identity)
    connection = database.get_connection()
    connection.execute("""INSERT INTO recordings
        (id,channel,platform,streamer,fingerprint,file_path,duration,created_at)
        VALUES (1,'clips_demo','kick','shortsengine_demo','hash','fake.mp4',100,'today')""")
    connection.commit()
    connection.close()
    store.update_clip(clip_id, recording_id=1)
    monkeypatch.setattr(recordings, "import_recording", Mock(return_value=1))
    transcriber = Mock(side_effect=AssertionError("must not transcribe"))
    monkeypatch.setattr(recordings, "recording_words", transcriber)
    renderer = Mock(side_effect=AssertionError("must not render"))
    monkeypatch.setattr(maker, "make_clip_video", renderer)
    assert launch.cut_recording("clips_demo", config, "fake.mp4", "shortsengine_demo", "kick",
                                 0, 20, "Truthful hook", True) == video_id
    with pytest.raises(ValueError, match="overlaps"):
        launch.cut_recording("clips_demo", config, "fake.mp4", "shortsengine_demo", "kick",
                             10, 30, "Another hook", True)
    transcriber.assert_not_called()
    renderer.assert_not_called()


def test_overlapping_source_intervals_cannot_be_exported_together(launch_space):
    """
    Detect duplicate source moments even when their encoded video bytes differ.

    Args:
        launch_space: The isolated folder and free channel configuration.

    Returns:
        Nothing; the overlapping bundle is refused before creating files.
    """
    root, config = launch_space
    first, first_clip = add_draft(root)
    second, second_clip = add_draft(root, "two", b"different encode", 10)
    connection = database.get_connection()
    connection.execute("""INSERT INTO recordings
        (id,channel,platform,streamer,fingerprint,file_path,duration,created_at)
        VALUES (1,'clips_demo','kick','shortsengine_demo','hash','fake.mp4',100,'today')""")
    connection.commit()
    connection.close()
    store.update_clip(first_clip, recording_id=1)
    store.update_clip(second_clip, recording_id=1)
    with pytest.raises(ValueError, match="overlap"):
        launch.export_bundle("clips_demo", config, [first, second], "output/bundle")
    assert not (root / "output/bundle").exists()


def test_prior_uncertain_upload_blocks_manual_duplicate(launch_space):
    """
    Keep an uncertain official upload from being accidentally duplicated through a manual bundle.

    Args:
        launch_space: The isolated folder and free channel configuration.

    Returns:
        Nothing; reconciliation is required before another upload path is prepared.
    """
    root, config = launch_space
    video_id, _ = add_draft(root)
    connection = database.get_connection()
    connection.execute("INSERT INTO publishing_deliveries VALUES (?, 'youtube', 'uncertain', NULL)", (video_id,))
    connection.commit()
    connection.close()
    with pytest.raises(ValueError, match="reconcile"):
        launch.export_bundle("clips_demo", config, [video_id], "output/bundle")
    assert not (root / "output/bundle").exists()


def test_one_completed_destination_does_not_block_the_other(launch_space):
    """
    Allow TikTok-only export after a YouTube upload while still blocking duplicate YouTube exports.

    Args:
        launch_space: The isolated folder and free channel configuration.

    Returns:
        Nothing; assertions confirm partial publishing can finish without duplicating a successful upload.
    """
    root, config = launch_space
    video_id, _ = add_draft(root)
    connection = database.get_connection()
    connection.execute("INSERT INTO publishing_deliveries VALUES (?, 'youtube', 'succeeded', 'confirmed-id')", (video_id,))
    connection.commit()
    connection.close()
    result = launch.export_bundle("clips_demo", config, [video_id], "output/tiktok", platforms=["tiktok"])
    assert result["platforms"] == ["tiktok"]
    with pytest.raises(ValueError, match="reconcile"):
        launch.export_bundle("clips_demo", config, [video_id], "output/youtube", platforms=["youtube"])

# =============================================================
# Regression tests for long recordings, scoring and automation safety.
# Platform calls are mocked. The media integration test uses real
# FFmpeg and a clearly labelled transcript fixture, without API keys.
# =============================================================

import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import yaml

from src.clips import automation, demo, maker, monitor, recordings, signals, store
from src.clips.demo import demo_config
from src.clips.settings import StreamerNotApproved, load_clip_settings
from src.dashboard import jobs, settings
from src.generation.llm import LLMClient
from src.publishing import scheduler
from src.sources import kick
from src.storage import database
from src.storage.queries import approve_video, get_video_by_id, save_video
from tests.test_control_center import execute, portal, token


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """
    Create isolated config, database and caches; return the temporary root and free demo config.

    Args:
        tmp_path: The temporary directory supplied by pytest.
        monkeypatch: The pytest helper that temporarily replaces functions and settings.

    Returns:
        The temporary workspace path and channel configuration.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setattr(settings, "CHANNEL_FOLDER", tmp_path / "channels")
    (tmp_path / "channels").mkdir()
    config = demo_config()
    (tmp_path / "channels/clips_demo.yaml").write_text(yaml.safe_dump(config))
    database.create_tables()
    return tmp_path, config


def test_scores_favor_reactions_and_explain_missing_chat():
    """
    Speech reactions and volume contrast outrank quiet filler, with audience data explicitly unavailable.

    Returns:
        Nothing; assertions verify the expected behavior.
    """
    settings = load_clip_settings(demo_config())
    words = [{"word": "ordinary.", "start": 1, "end": 1.5},
             {"word": "Wow!", "start": 11, "end": 11.7},
             {"word": "Amazing!", "start": 12, "end": 12.7},
             {"word": "Why?", "start": 13, "end": 13.7}]
    media = {"volume_db": [-38] * 10 + [-16] * 5 + [-38] * 15, "motion": [0.02] * 30}
    quiet = signals.score_candidate(0, 8, words, media, settings)
    reaction = signals.score_candidate(10, 18, words, media, settings)
    assert reaction["score"] > quiet["score"] + 20
    assert reaction["components"]["audience"] is None
    assert 0 <= reaction["score"] <= 100


def test_candidates_preserve_words_and_do_not_overlap():
    """
    Ranked candidates respect duration limits and substantial overlap is suppressed.

    Returns:
        Nothing; assertions verify the expected behavior.
    """
    settings = load_clip_settings(demo_config())
    words = []
    for index in range(100):
        words.append({"word": f"Word{index}.", "start": index * 0.7, "end": index * 0.7 + 0.5})
    media = {"volume_db": [-20] * 70, "motion": [0.1] * 70}
    chosen = signals.select_highlights(70, words, media, settings)
    assert len(chosen) == 2
    for item in chosen:
        assert 0 <= item["start"] < item["end"] <= 70
        assert 10 <= item["end"] - item["start"] <= 22
        for word in words:
            assert not word["start"] < item["start"] < word["end"]
            assert not word["start"] < item["end"] < word["end"]
    assert not signals.similar_candidate(chosen[0], [chosen[1]])


@pytest.mark.parametrize("weights", [{"hook": 1}, {"entertainment": float("nan"), "hook": 1,
                         "audience": 1, "clarity": 1, "audio": 1, "visual": 1}])
def test_invalid_weights_are_refused(weights):
    """
    Missing score dimensions and non-finite weights fail configuration validation.

    Args:
        weights: The configured relative weight of each scoring dimension.

    Returns:
        Nothing; assertions verify the expected behavior.
    """
    config = demo_config()
    config["analysis"]["weights"] = weights
    with pytest.raises(ValueError):
        load_clip_settings(config)


def test_disabled_streamer_is_refused_before_transcription(workspace, monkeypatch):
    """
    Revoking a source prevents file reading, transcription and rendering of its old clips.

    Args:
        workspace: The isolated test directory and free channel configuration.
        monkeypatch: The pytest helper that temporarily replaces functions and settings.

    Returns:
        Nothing; assertions verify the expected behavior.
    """
    _, config = workspace
    config["streamers"][0]["enabled"] = False
    clip = {"streamer": "shortsengine_demo", "platform": "kick"}
    transcriber = Mock(side_effect=AssertionError("must not transcribe"))
    monkeypatch.setattr(maker, "words_for", transcriber)
    with pytest.raises(StreamerNotApproved):
        maker.make_clip_video("clips_demo", config, load_clip_settings(config), clip, None)
    transcriber.assert_not_called()


def test_dry_cycle_has_no_side_effects(workspace, monkeypatch):
    """
    A pipeline dry run never contacts platforms, reads models or imports a requested file.

    Args:
        workspace: The isolated test directory and free channel configuration.
        monkeypatch: The pytest helper that temporarily replaces functions and settings.

    Returns:
        Nothing; assertions verify the expected behavior.
    """
    _, config = workspace
    monkeypatch.setattr(monitor, "check_live_sources", Mock(side_effect=AssertionError("network")))
    monkeypatch.setattr(recordings, "import_recording", Mock(side_effect=AssertionError("import")))
    result = automation.run_pipeline("clips_demo", config, None, True, {"path": "missing.mp4"})
    assert "Dry run" in result["message"]
    assert execute("SELECT COUNT(*) FROM recordings")[0][0] == 0


def test_empty_macos_speech_stops_demo_before_video_creation(tmp_path, monkeypatch):
    """
    Stop the demo with a useful message when macOS reports speech success but writes no audio samples.

    Args:
        tmp_path: The temporary directory supplied by pytest.
        monkeypatch: The pytest helper that temporarily replaces functions and settings.

    Returns:
        Nothing; assertions verify that FFmpeg never starts for empty speech.
    """
    monkeypatch.setattr(demo.shutil, "which", Mock(return_value="/usr/bin/say"))
    runner = Mock(side_effect=[SimpleNamespace(returncode=0),
                               SimpleNamespace(stdout=json.dumps({"streams": [{"duration": "0.000000"}]}))])
    monkeypatch.setattr(demo.subprocess, "run", runner)

    with pytest.raises(RuntimeError, match="speech.*--fixture"):
        demo.make_sample(tmp_path, False)

    for call in runner.call_args_list:
        assert call.args[0][0] != "ffmpeg"
    assert not (tmp_path / "sample.mp4").exists()


def test_kick_official_status_contract(monkeypatch):
    """
    Kick channel status uses the documented public endpoint and app token, without playback downloads.

    Args:
        monkeypatch: The pytest helper that temporarily replaces functions and settings.

    Returns:
        Nothing; assertions verify the expected behavior.
    """
    monkeypatch.setattr(kick, "get_app_token", Mock(return_value="token"))
    response = SimpleNamespace(status_code=200, json=Mock(return_value={"data": [{
        "stream_title": "Demo", "stream": {"is_live": True, "viewer_count": 42}}]}))
    getter = Mock(return_value=response)
    monkeypatch.setattr(kick.httpx, "get", getter)
    assert kick.live_status("creator") == {"live": True, "title": "Demo", "viewers": 42, "started_at": ""}
    assert getter.call_args.args[0] == "https://api.kick.com/public/v1/channels"
    assert getter.call_args.kwargs["params"] == {"slug": "creator"}


def test_monitoring_errors_do_not_mean_offline(workspace, monkeypatch):
    """
    A credential failure is persisted as unknown and does not prevent other pipeline work.

    Args:
        workspace: The isolated test directory and free channel configuration.
        monkeypatch: The pytest helper that temporarily replaces functions and settings.

    Returns:
        Nothing; assertions verify the expected behavior.
    """
    _, config = workspace
    monkeypatch.setattr(kick, "live_status", Mock(side_effect=ValueError("Missing Kick credentials")))
    result = monitor.check_live_sources("clips_demo", config)
    assert result[0]["state"] == "unknown"
    assert execute("SELECT state FROM stream_status")[0][0] == "unknown"


def test_free_cycle_uses_persistent_capacity_and_is_idempotent(workspace, monkeypatch):
    """
    Repeated form submissions reuse one free job and parallel heavy work is refused.

    Args:
        workspace: The isolated test directory and free channel configuration.
        monkeypatch: The pytest helper that temporarily replaces functions and settings.

    Returns:
        Nothing; assertions verify the expected behavior.
    """
    monkeypatch.setattr(jobs, "recover_stale_jobs", Mock())
    first, created = jobs.enqueue_job("clips_demo", "clip_pipeline", {}, False, "same-form")
    assert created
    assert jobs.enqueue_job("clips_demo", "clip_pipeline", {}, False, "same-form") == (first, False)
    assert jobs.job_record(first)["budget_usd"] == 0
    with pytest.raises(ValueError, match="already"):
        jobs.enqueue_job("clips_demo", "clip_pipeline", {}, False, "different-form")


def test_recording_failures_have_bounded_retries(workspace, monkeypatch):
    """
    A failed analysis saves attempts/error/backoff and is not retried immediately.

    Args:
        workspace: The isolated test directory and free channel configuration.
        monkeypatch: The pytest helper that temporarily replaces functions and settings.

    Returns:
        Nothing; assertions verify the expected behavior.
    """
    _, config = workspace
    execute("""INSERT INTO recordings (channel,platform,streamer,fingerprint,file_path,duration,created_at)
               VALUES ('clips_demo','kick','shortsengine_demo','hash','missing.mp4',60,'today')""")
    analyzer = Mock(side_effect=ValueError("model unavailable"))
    monkeypatch.setattr(recordings, "analyze_recording", analyzer)
    assert recordings.process_recordings("clips_demo", config) == []
    row = recordings.recording_row(1)
    assert row["status"] == "failed" and row["attempts"] == 1 and row["retry_at"] > time.time()
    assert "model unavailable" in row["error"]
    recordings.process_recordings("clips_demo", config)
    assert analyzer.call_count == 1


def test_corrupt_inbox_file_does_not_block_completed_recordings(workspace, monkeypatch):
    """
    Keep importing usable recordings after a corrupt file and report the skipped file in the cycle result.

    Args:
        workspace: The isolated test directory and free channel configuration.
        monkeypatch: The pytest helper that temporarily replaces functions and settings.

    Returns:
        Nothing; assertions check saved recordings and the visible error message.
    """
    root, config = workspace
    folder = root / "completed"
    folder.mkdir()
    broken = folder / "00-broken.mp4"
    broken.write_bytes(b"not a video")
    usable = folder / "01-completed.mp4"
    usable.write_bytes(b"authorized recording")
    for path in (broken, usable):
        os.utime(path, (time.time() - 120, time.time() - 120))
    config["streamers"][0]["recording_directory"] = str(folder)
    probe = Mock(side_effect=[ValueError("The recording has no video picture."),
                             {"duration": 30, "has_audio": True}])
    monkeypatch.setattr(recordings, "probe_video", probe)
    monkeypatch.setattr(monitor, "check_live_sources", Mock(return_value=[]))
    monkeypatch.setattr(recordings, "process_recordings", Mock(return_value=[]))

    result = automation.run_pipeline("clips_demo", config, LLMClient(dry_run=True))

    saved = execute("SELECT original_path FROM recordings")
    assert len(saved) == 1 and saved[0][0] == str(usable)
    assert "00-broken.mp4" in "\n".join(result["errors"])


def test_unreadable_inbox_does_not_block_other_creators(workspace, monkeypatch):
    """
    Continue with the next creator when an inbox cannot be listed and report which source failed.

    Args:
        workspace: The isolated test directory and free channel configuration.
        monkeypatch: The pytest helper that temporarily replaces functions and settings.

    Returns:
        Nothing; assertions check that the other creator's recording is imported correctly.
    """
    root, config = workspace
    inaccessible = root / "unreadable"
    inaccessible.mkdir()
    config["streamers"][0]["recording_directory"] = str(inaccessible)
    folder = root / "another_creator"
    folder.mkdir()
    usable = folder / "completed.mp4"
    usable.write_bytes(b"another authorized recording")
    os.utime(usable, (time.time() - 120, time.time() - 120))
    creator = dict(config["streamers"][0])
    creator.update({"username": "another_creator", "recording_directory": str(folder)})
    config["streamers"].append(creator)
    original_iterdir = Path.iterdir

    def list_readable_folder(path):
        """
        List a test folder unless it is the inaccessible inbox.

        Args:
            path: The directory whose entries the scanner wants to read.

        Returns:
            The directory entries, or raises PermissionError for the blocked inbox.
        """
        if path == inaccessible:
            raise PermissionError("Recording folder cannot be read.")
        return original_iterdir(path)

    monkeypatch.setattr(Path, "iterdir", list_readable_folder)
    monkeypatch.setattr(recordings, "probe_video", Mock(return_value={"duration": 30, "has_audio": True}))
    monkeypatch.setattr(monitor, "check_live_sources", Mock(return_value=[]))
    monkeypatch.setattr(recordings, "process_recordings", Mock(return_value=[]))

    result = automation.run_pipeline("clips_demo", config, LLMClient(dry_run=True))

    saved = execute("SELECT streamer FROM recordings")
    assert len(saved) == 1 and saved[0][0] == "another_creator"
    assert "shortsengine_demo" in "\n".join(result["errors"])


def test_renamed_duplicate_does_not_consume_inbox_import_limit(workspace, monkeypatch):
    """
    Skip a renamed copy of saved footage so a one-recording cycle still reaches new footage.

    Args:
        workspace: The isolated test directory and free channel configuration.
        monkeypatch: The pytest helper that temporarily replaces functions and settings.

    Returns:
        Nothing; assertions check the new recording ID and prevent duplicate storage.
    """
    root, config = workspace
    monkeypatch.setattr(recordings, "probe_video", Mock(return_value={"duration": 30, "has_audio": True}))
    original = root / "original.mp4"
    original.write_bytes(b"previously imported recording")
    existing_id = recordings.import_recording("clips_demo", config, original, "shortsengine_demo", "kick", True)
    folder = root / "completed"
    folder.mkdir()
    duplicate = folder / "00-renamed.mp4"
    shutil.copyfile(original, duplicate)
    fresh = folder / "01-new.mp4"
    fresh.write_bytes(b"new authorized footage")
    for path in (duplicate, fresh):
        os.utime(path, (time.time() - 120, time.time() - 120))
    config["streamers"][0]["recording_directory"] = str(folder)
    config["automation"]["max_recordings_per_run"] = 1

    imported = recordings.scan_inboxes("clips_demo", config)

    assert len(imported) == 1 and imported[0] != existing_id
    assert recordings.recording_row(imported[0])["original_path"] == str(fresh)
    assert execute("SELECT COUNT(*) FROM recordings")[0][0] == 2


def test_reassigned_inbox_reports_existing_footage_attribution(workspace, monkeypatch):
    """
    Refuse saved footage assigned to a different creator even when its path, size and modification time are unchanged.

    Args:
        workspace: The isolated test directory and free channel configuration.
        monkeypatch: The pytest helper that temporarily replaces functions and settings.

    Returns:
        Nothing; assertions check that attribution stays with the original creator and the conflict is reported.
    """
    root, config = workspace
    folder = root / "completed"
    folder.mkdir()
    source = folder / "recording.mp4"
    source.write_bytes(b"authorized footage from the first creator")
    os.utime(source, (time.time() - 120, time.time() - 120))
    monkeypatch.setattr(recordings, "probe_video", Mock(return_value={"duration": 30, "has_audio": True}))
    recordings.import_recording("clips_demo", config, source, "shortsengine_demo", "kick", True)
    config["streamers"][0].update({"username": "another_creator", "recording_directory": str(folder)})
    errors = []

    assert recordings.scan_inboxes("clips_demo", config, errors) == []

    assert len(errors) == 1 and "attribution" in errors[0]
    assert execute("SELECT streamer FROM recordings")[0][0] == "shortsengine_demo"


def test_daily_cap_and_revocation_block_new_uploads(workspace):
    """
    Daily reservations are atomic per video and revoked streamers cannot reach platform uploads.

    Args:
        workspace: The isolated test directory and free channel configuration.

    Returns:
        Nothing; assertions verify the expected behavior.
    """
    root, config = workspace
    config["automation"]["max_uploads_per_day"] = 1
    (root / "channels/clips_demo.yaml").write_text(yaml.safe_dump(config))
    videos = []
    for number in range(2):
        video_id = save_video("clips_demo", "Demo", "source", "notes.md", 0)
        clip_id = store.save_found_clip("clips_demo", {"source": "upload", "source_clip_id": str(number),
                "streamer": "shortsengine_demo", "platform": "kick", "credit_text": "kick.com/shortsengine_demo"})
        store.update_clip(clip_id, video_id=video_id)
        approve_video(video_id)
        videos.append(video_id)
    assert scheduler.claim_delivery(videos[0], "youtube")
    assert scheduler.claim_delivery(videos[0], "tiktok")
    assert not scheduler.claim_delivery(videos[1], "youtube")
    config["streamers"][0]["enabled"] = False
    (root / "channels/clips_demo.yaml").write_text(yaml.safe_dump(config))
    with pytest.raises(StreamerNotApproved):
        scheduler.claim_delivery(videos[1], "tiktok")


def test_schedule_defaults_to_dry_run_for_clips(workspace, monkeypatch):
    """
    Even approved videos are not sent by a clips schedule until publishing dry run is disabled.

    Args:
        workspace: The isolated test directory and free channel configuration.
        monkeypatch: The pytest helper that temporarily replaces functions and settings.

    Returns:
        Nothing; assertions verify the expected behavior.
    """
    _, config = workspace
    config["schedule"] = {"post_times": ["12:00"]}
    monkeypatch.setattr(scheduler, "_load_all_channels", Mock(return_value=[("clips_demo", config)]))
    monkeypatch.setattr(scheduler, "find_posting_slot", Mock(return_value="slot"))
    sender = Mock(side_effect=AssertionError("must not send"))
    monkeypatch.setattr(scheduler, "select_slot_video", sender)
    scheduler.run_scheduler()
    sender.assert_not_called()


@pytest.mark.parametrize("size", [1, 4_000_000, 33_000_000, 65_000_000, 140_000_001, 4 * 1024 ** 3])
def test_tiktok_chunk_limits(size):
    """
    Small and large videos use valid sequential chunk counts without dropping trailing bytes.

    Args:
        size: A font size in pixels, or a video size in bytes in transfer tests.

    Returns:
        Nothing; assertions verify the expected behavior.
    """
    from src.publishing.tiktok import chunk_plan
    chunk_size, count = chunk_plan(size)
    assert count == max(1, size // chunk_size)
    assert 1 <= count <= 1000
    assert chunk_size <= 64 * 1024 ** 2
    assert size - (count - 1) * chunk_size <= 128 * 1024 ** 2
    if size > 64 * 1024 ** 2:
        assert count > 1


def test_engagement_sidecar_is_real_and_validated(tmp_path):
    """
    Creator-supplied chat counts are accepted, while impossible and non-finite values are refused.

    Args:
        tmp_path: The temporary directory supplied by pytest.

    Returns:
        Nothing; assertions verify the expected behavior.
    """
    sidecar = tmp_path / "recording.engagement.json"
    sidecar.write_text('[{"time": 12, "count": 80}, {"time": 15, "count": 2}]')
    assert recordings.read_engagement(sidecar, 20)[0]["count"] == 80
    sidecar.write_text('[{"time": 25, "count": 80}]')
    with pytest.raises(ValueError, match="inside"):
        recordings.read_engagement(sidecar, 20)


def test_paid_speech_assessment_is_bounded_and_explainable(workspace):
    """
    Optional cached LLM output updates only speech dimensions and keeps attribution in the publishing text.

    Args:
        workspace: The isolated test directory and free channel configuration.

    Returns:
        Nothing; assertions verify the expected behavior.
    """
    from src.clips.metadata import create_metadata, publishing_caption
    _, config = workspace
    config["analysis"]["use_llm"] = True
    config["models"]["clip_pick"] = "configured-model"
    clip_id = store.save_found_clip("clips_demo", {"source": "upload", "source_clip_id": "metadata",
        "streamer": "shortsengine_demo", "platform": "kick", "credit_text": "kick.com/shortsengine_demo"})
    evidence = {"components": {"entertainment": 0.5, "hook": 0.5, "clarity": 0.5,
                              "audio": 1, "visual": 0.5, "audience": None}}
    store.update_clip(clip_id, start_seconds=0, end_seconds=10, hook_title="Original",
                      signals_json=json.dumps(evidence))
    llm = SimpleNamespace(ask=Mock(return_value=json.dumps({"hook_title": "A surprising finish",
        "description": "The race ends in a surprise.", "scores": {"entertainment": 80, "hook": 90, "clarity": 85},
        "reason": "Setup and reaction are understandable."})))
    words = [{"word": "Wow!", "start": 1, "end": 2}]
    create_metadata(store.get_clip(clip_id), words, config, load_clip_settings(config), llm)
    saved = store.get_clip(clip_id)
    assert saved["highlight_score"] > 70
    assert "LLM" in json.loads(saved["signals_json"])["method"]
    assert llm.ask.call_args.kwargs["max_tokens"] == 400
    assert "https://kick.com/shortsengine_demo" in publishing_caption(saved, saved["hook_title"], [])


def test_destination_overrides_do_not_send_elsewhere(workspace):
    """
    A clip can target only YouTube even when its channel also has TikTok configured.

    Args:
        workspace: The isolated test directory and free channel configuration.

    Returns:
        Nothing; assertions verify the expected behavior.
    """
    _, config = workspace
    video_id = save_video("clips_demo", "Demo", "source", "notes.md", 0)
    clip_id = store.save_found_clip("clips_demo", {"source": "upload", "source_clip_id": "dest",
        "streamer": "shortsengine_demo", "platform": "kick", "credit_text": "kick.com/shortsengine_demo"})
    store.update_clip(clip_id, video_id=video_id, metadata_json='{"platforms":["youtube"]}')
    assert scheduler.video_platforms(get_video_by_id(video_id), config) == ["youtube"]


def test_stop_automation_also_stops_scheduled_uploads(workspace, monkeypatch):
    """
    The Stop control prevents scheduled sends even when publishing dry run was explicitly disabled.

    Args:
        workspace: The isolated test directory and free channel configuration.
        monkeypatch: The pytest helper that temporarily replaces functions and settings.

    Returns:
        Nothing; assertions verify the expected behavior.
    """
    _, config = workspace
    config["automation"]["publish_dry_run"] = False
    config["automation"]["enabled"] = True
    config["schedule"] = {"post_times": ["12:00"]}
    automation.set_enabled("clips_demo", False)
    monkeypatch.setattr(scheduler, "_load_all_channels", Mock(return_value=[("clips_demo", config)]))
    sender = Mock(side_effect=AssertionError("scheduled send"))
    monkeypatch.setattr(scheduler, "select_slot_video", sender)
    scheduler.run_scheduler()
    sender.assert_not_called()


def test_metadata_form_updates_text_destinations_and_review(portal):
    """
    Metadata edits save destination choices and return an approved clip to draft while keeping attribution.

    Args:
        portal: A logged-in test client and its isolated temporary workspace.

    Returns:
        Nothing; assertions verify the expected behavior.
    """
    client, root = portal
    config = demo_config()
    (root / "channels/clips_demo.yaml").write_text(yaml.safe_dump(config))
    video_id = save_video("clips_demo", "Original", "source", "notes.md", 0)
    clip_id = store.save_found_clip("clips_demo", {"source": "upload", "source_clip_id": "edit",
        "streamer": "shortsengine_demo", "platform": "kick", "credit_text": "kick.com/shortsengine_demo"})
    store.update_clip(clip_id, video_id=video_id)
    approve_video(video_id)
    form = {"form_token": token(client), "title": "A grounded title", "description": "Actual speech.", "youtube": "true"}
    assert client.post(f"/video/{video_id}/metadata", data=form, follow_redirects=False).status_code == 303
    assert get_video_by_id(video_id)["status"] == "drafted"
    assert scheduler.video_platforms(get_video_by_id(video_id), config) == ["youtube"]
    assert "https://kick.com/shortsengine_demo" in scheduler.clip_caption(get_video_by_id(video_id), config)
    assert client.get(f"/video/{video_id}").status_code == 200


def test_manual_retry_requires_reconciliation_and_no_active_work(portal):
    """
    A failed recording can be reset only after confirmation and while its channel is idle.

    Args:
        portal: A logged-in test client and its isolated temporary workspace.

    Returns:
        Nothing; assertions verify the expected behavior.
    """
    client, root = portal
    (root / "channels/clips_demo.yaml").write_text(yaml.safe_dump(demo_config()))
    execute("""INSERT INTO recordings (channel, platform, streamer, fingerprint, file_path, duration,
               status, attempts, created_at) VALUES ('clips_demo','kick','shortsengine_demo','retry','f.mp4',60,'failed',3,'today')""")
    form = {"form_token": token(client), "item_id": 1, "kind": "recording"}
    assert client.post("/clips/clips_demo/retry", data=form).status_code == 400
    form["confirmed"] = "true"
    assert client.post("/clips/clips_demo/retry", data=form, follow_redirects=False).status_code == 303
    assert recordings.recording_row(1)["status"] == "pending"
    assert recordings.recording_row(1)["attempts"] == 0


def test_uncertain_paid_request_is_not_automatically_repeated(workspace, monkeypatch):
    """
    Losing an AI response exhausts that clip's automatic retries so the next cycle cannot pay again blindly.

    Args:
        workspace: The isolated test directory and free channel configuration.
        monkeypatch: The pytest helper that temporarily replaces functions and settings.

    Returns:
        Nothing; assertions verify the expected behavior.
    """
    from src.generation.llm import LLMError
    _, config = workspace
    config["analysis"]["use_llm"] = True
    config["models"]["clip_pick"] = "configured-model"
    clip_id = store.save_found_clip("clips_demo", {"source": "upload", "source_clip_id": "uncertain",
        "streamer": "shortsengine_demo", "platform": "kick", "credit_text": "kick.com/shortsengine_demo"})
    store.update_clip(clip_id, status="picked", start_seconds=0, end_seconds=10, hook_title="Demo", highlight_score=80)
    llm = SimpleNamespace(cost_tracker=None, pending_event=1, ask=Mock(side_effect=LLMError("Lost response")))
    monkeypatch.setattr(monitor, "check_live_sources", Mock(return_value=[]))
    monkeypatch.setattr(maker, "words_for", Mock(return_value=[{"word": "Wow!", "start": 1, "end": 2}]))
    automation.run_pipeline("clips_demo", config, llm)
    assert store.get_clip(clip_id)["render_attempts"] == 3
    assert "uncertain" in store.get_clip(clip_id)["last_error"]
    automation.run_pipeline("clips_demo", config, llm)
    assert llm.ask.call_count == 1


def test_clip_pages_forms_and_yaml_preserve_other_settings(portal):
    """
    Clip UI loads, validates CSRF, saves streamers/settings and keeps unrelated YAML comments.

    Args:
        portal: A logged-in test client and its isolated temporary workspace.

    Returns:
        Nothing; assertions verify the expected behavior.
    """
    client, root = portal
    path = root / "channels/clips_demo.yaml"
    config = demo_config()
    path.write_text("# Keep this project header\n" + yaml.safe_dump(config, sort_keys=False) + "\n# Keep footer\n")
    assert client.get("/clips").status_code == 200
    page = client.get("/clips/clips_demo")
    assert page.status_code == 200 and "Start automation" in page.text
    assert client.post("/clips/clips_demo/automation", data={"enabled": "true"}).status_code == 403
    form = {"form_token": token(client), "username": "another_creator", "platform": "kick",
            "permission_note": "Written agreement received", "permission_date": "2026-01-01", "confirmed": "true"}
    assert client.post("/clips/clips_demo/streamers", data=form, follow_redirects=False).status_code == 303
    saved = yaml.safe_load(path.read_text())
    assert len(saved["streamers"]) == 2 and "Keep this project header" in path.read_text()
    response = client.post("/clips/clips_demo/settings", data={"form_token": token(client),
        "minimum_score": 65, "max_clips": 2, "poll_seconds": 60, "max_uploads": 2,
        "require_approval": "true", "publish_dry_run": "true", "template": "conversation", "post_times": "09:00, 17:00"},
        follow_redirects=False)
    assert response.status_code == 303
    saved = yaml.safe_load(path.read_text())
    assert saved["analysis"]["minimum_score"] == 65 and saved["schedule"]["post_times"] == ["09:00", "17:00"]
    assert "Keep footer" in path.read_text()
    assert client.post("/clips/clips_demo/automation", data={"form_token": token(client), "enabled": "true"},
                       follow_redirects=False).status_code == 303
    assert execute("SELECT enabled FROM clip_automation")[0][0] == 1


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg is required")
def test_recording_to_checked_draft_and_duplicate_recovery(workspace, monkeypatch):
    """
    Real FFmpeg video imports once, reuses a transcript fixture and renders one valid captioned review draft.

    Args:
        workspace: The isolated test directory and free channel configuration.
        monkeypatch: The pytest helper that temporarily replaces functions and settings.

    Returns:
        Nothing; assertions verify the expected behavior.
    """
    root, config = workspace
    config["clips"] = {"moment_min_seconds": 3, "moment_max_seconds": 6}
    config["analysis"].update({"candidate_seconds": 5, "max_clips_per_recording": 1})
    source = root / "test.mp4"
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=320x180:r=10:d=5",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=5", "-c:v", "libx264", "-c:a", "aac",
                    "-shortest", str(source)], check=True)
    with pytest.raises(ValueError, match="Confirm"):
        recordings.import_recording("clips_demo", config, source, "shortsengine_demo", "kick", False)
    recording_id = recordings.import_recording("clips_demo", config, source, "shortsengine_demo", "kick", True)
    assert recordings.import_recording("clips_demo", config, source, "shortsengine_demo", "kick", True) == recording_id
    words = [{"word": "Wow!", "start": 0.2, "end": 0.8}, {"word": "Amazing!", "start": 1, "end": 1.5}]
    monkeypatch.setattr(recordings, "transcribe_clip", Mock(return_value=words))
    monkeypatch.setattr("src.video.clip_render.credit_icon", Mock(return_value=None))
    monkeypatch.setattr(monitor, "check_live_sources", Mock(return_value=[]))
    result = automation.run_pipeline("clips_demo", config, LLMClient(dry_run=True))
    assert result["errors"] == []
    assert len(result["video_ids"]) == 1
    video_id = result["video_ids"][0]
    clip = store.get_clip_for_video(video_id)
    draft = get_video_by_id(video_id)
    assert draft["status"] == "drafted" and Path(draft["video_path"]).is_file()
    assert json.loads(clip["quality_json"])["passed"]
    assert recordings.analyze_recording(recording_id, config) == []
    assert automation.run_pipeline("clips_demo", config, LLMClient(dry_run=True))["video_ids"] == []
    assert execute("SELECT COUNT(*) FROM videos")[0][0] == 1


def test_silence_edits_remap_caption_times():
    """
    Removing the middle of a true-silence span moves later captions by exactly the removed duration.

    Returns:
        Nothing; assertions verify the expected behavior.
    """
    from src.clips.editing import keep_ranges, remap_words
    ranges = keep_ranges(10, [(2, 7)], padding=0.5)
    assert ranges == [(0, 2.5), (6.5, 10)]
    words = [{"word": "first", "start": 11, "end": 11.5},
             {"word": "last", "start": 18, "end": 18.5}]
    mapped = remap_words(words, 10, ranges)
    assert mapped[0]["start"] == 1
    assert mapped[1]["start"] == 4


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg is required")
def test_real_silence_removal_keeps_audio_video_and_captions(workspace):
    """
    Actual FFmpeg silence detection and concat shorten a recording without losing the final spoken caption.

    Args:
        workspace: The isolated test directory and free channel configuration.

    Returns:
        Nothing; assertions verify the expected behavior.
    """
    from src.clips.editing import remove_long_silence
    from src.video.clip_render import probe_video
    root, _ = workspace
    source = root / "silence.mp4"
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=320x180:r=10:d=6",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=6", "-af", "volume=0:enable='between(t,1,4)'",
                    "-c:v", "libx264", "-c:a", "aac", "-shortest", str(source)], check=True)
    words = [{"word": "first", "start": 0.2, "end": 0.5}, {"word": "last", "start": 5, "end": 5.5}]
    path, start, end, captions, cleanup = remove_long_silence(str(source), 0, 6, words, str(root / "edited.mp4"),
                                                            {"remove_long_silence": True})
    assert cleanup and start == 0 and 3 < end < 5
    assert probe_video(path)["has_audio"]
    assert abs(probe_video(path)["duration"] - end) < 0.2
    assert captions[-1]["start"] < 4

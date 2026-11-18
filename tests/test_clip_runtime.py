# =============================================================
# Tests for independent supervision without real platform calls.
# Protect original media, isolate news publishing, and exercise recovery.
# =============================================================

import json
import os
import plistlib
import time
from unittest.mock import Mock

import pytest

from src.clips import runtime, service
from src.dashboard import jobs, settings
from src.storage import database


@pytest.fixture
def runtime_space(tmp_path, monkeypatch):
    """Use pytest's temporary directory and patch helper; return an isolated runtime root with an empty database."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(runtime, "ROOT", tmp_path)
    monkeypatch.setattr(runtime, "STATE", tmp_path / "output/runtime")
    monkeypatch.setattr(settings, "CHANNEL_FOLDER", tmp_path / "channels")
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "test.db"))
    (tmp_path / "channels").mkdir()
    database.create_tables()
    return tmp_path


def test_launchd_definitions_preserve_venv_and_avoid_duplicate_schedulers(tmp_path):
    """Build agents for a temporary root; assert restart controls, private binding and single scheduling owner."""
    agents = service.definitions(tmp_path)
    assert len(agents) == 2
    for agent in agents:
        assert plistlib.loads(plistlib.dumps(agent)) == agent
        assert agent["KeepAlive"] and agent["RunAtLoad"]
        assert agent["ProgramArguments"][0] == str(tmp_path / ".venv/bin/python")
        assert "SECRET" not in json.dumps(agent) and "TOKEN" not in json.dumps(agent)
    dashboard = agents[1]["EnvironmentVariables"]
    assert dashboard["DASHBOARD_AUTO_CLIPS"] == dashboard["DASHBOARD_AUTO_PUBLISH"] == "false"
    assert dashboard["DASHBOARD_HOST"] == "127.0.0.1"


def test_cleanup_only_deletes_old_fragments_when_idle(runtime_space):
    """Create old/fresh files in an isolated root; assert originals, final media and busy workers remain protected."""
    root = runtime_space
    folder = root / "output/videos/demo"
    folder.mkdir(parents=True)
    for name in ["old.part", "draft.rendering.mp4", "draft.edited.mp4", "finished.mp4", "recent.part"]:
        path = folder / name
        path.write_bytes(b"test")
        if name != "recent.part":
            os.utime(path, (time.time() - 3 * 86400,) * 2)
    outside = root / "recording.mp4"
    outside.write_bytes(b"original")
    (folder / "shortcut.part").symlink_to(outside)
    assert runtime.clean_temporary_files(24, busy=True) == []
    removed = runtime.clean_temporary_files(24)
    assert len(removed) == 3
    assert (folder / "finished.mp4").exists() and (folder / "recent.part").exists()
    assert outside.read_bytes() == b"original"


def test_low_disk_pauses_new_jobs_but_updates_health(runtime_space, monkeypatch):
    """Simulate low disk with pytest patches; assert processing pauses and a fresh health report remains available."""
    from src.clips import automation
    from types import SimpleNamespace
    enqueue = Mock()
    monkeypatch.setattr(automation, "queue_due_channels", enqueue)
    monkeypatch.setattr(runtime.shutil, "disk_usage", Mock(return_value=SimpleNamespace(free=1024)))
    result = runtime.tick()
    assert result["low_disk"] and result["alive"]
    enqueue.assert_not_called()
    assert json.loads((runtime.STATE / "health.json").read_text())["low_disk"]


def test_periodic_recovery_releases_crashed_job_without_replaying_paid_work(runtime_space, monkeypatch):
    """Save an interrupted worker heartbeat in temporary SQLite; assert a tick frees capacity without direct paid retries."""
    from src.clips import automation
    connection = database.get_connection()
    connection.execute("""INSERT INTO jobs
        (id,channel,kind,status,payload,created_at,updated_at,heartbeat_at,submission_id)
        VALUES ('dead','demo','clip_pipeline','running','{}','2000','2000','2000','submission')""")
    connection.commit()
    connection.close()
    queue = Mock(return_value=[])
    monkeypatch.setattr(automation, "queue_due_channels", queue)
    runtime.tick()
    assert jobs.job_record("dead")["status"] == "interrupted"
    queue.assert_called_once()


def test_runtime_never_schedules_news(runtime_space, monkeypatch):
    """Supply news and clips channel settings through patches; assert only the clips schedule is invoked."""
    from src.publishing import scheduler
    monkeypatch.setattr(settings, "list_channels", Mock(return_value=[{"key":"news"}, {"key":"clips"}]))
    configs = {"news":{"type":"news"}, "clips":{"type":"clips"}}
    monkeypatch.setattr(settings, "channel_config", configs.__getitem__)
    publish = Mock()
    monkeypatch.setattr(scheduler, "run_scheduler", publish)
    runtime.publish_clips()
    publish.assert_called_once_with(only_channel="clips")


def test_unconnected_accounts_are_not_reported_ready(runtime_space):
    """Write a minimal clip config to temporary YAML; assert the health report discloses source and account blockers."""
    (runtime_space / "channels/demo.yaml").write_text("name: Demo\ntype: clips\nplatforms: [youtube, tiktok]\n")
    status = runtime.snapshot()["channels"][0]
    assert status["rendered_clips"] == 0
    assert len(status["blockers"]) >= 4
    assert any("manual posting" in text for text in status["blockers"])


def test_live_report_preserves_audit_and_replaces_only_its_section(runtime_space, monkeypatch):
    """Run two fake queue ticks in an isolated root; assert the audit survives and the live section is not duplicated."""
    from src.clips import automation
    monkeypatch.setattr(automation,"queue_due_channels",Mock(return_value=[]))
    report=runtime_space / "AUTOMATION_STATUS.md"
    report.write_text("# Audit\nKeep these verified findings.\n")
    runtime.tick()
    runtime.tick()
    text=report.read_text()
    assert text.startswith("# Audit\nKeep these verified findings.")
    assert text.count("<!-- RUNTIME_STATUS_START -->") == 1
    assert "Verified public URLs:" in text and "- None." in text

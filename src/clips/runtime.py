# =============================================================
# Independent clips supervisor, reusing the existing queue and scheduler.
# launchd keeps this process alive; SQLite keeps media stages and uploads.
# Health files and rotating JSON logs contain status, never credentials.
# =============================================================

import argparse
import fcntl
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import shutil
import signal
import threading
import time

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
STATE = ROOT / "output/runtime"
logger = logging.getLogger("shortsengine.runtime")


def event(name, **details):
    """Log a named event and safe status details; return nothing. Never pass credentials as details."""
    logger.info(json.dumps({"time": time.time(), "event": name, **details}))


def settings():
    """Read bounded runtime settings from project YAML; return intervals and disk limits."""
    path = ROOT / "config/runtime.yaml"
    values = yaml.safe_load(path.read_text()) if path.exists() else {}
    values = values or {}
    return {"tick_seconds": max(5, min(60, int(values.get("tick_seconds", 15)))),
            "minimum_free_gb": max(1, float(values.get("minimum_free_gb", 5))),
            "temporary_retention_hours": max(24, float(values.get("temporary_retention_hours", 48)))}


def snapshot():
    """Read local pipeline/account/source state; return safe counts and blockers without remote requests."""
    from src.dashboard.settings import channel_config, list_channels
    from src.storage.database import get_connection

    connection = get_connection()
    channels = []
    try:
        for item in list_channels():
            config = channel_config(item["key"])
            if config.get("type") != "clips":
                continue
            key = item["key"]
            accounts = []
            for row in connection.execute("SELECT platform FROM accounts WHERE channel=?", (key,)):
                accounts.append(row[0])
            videos = connection.execute("SELECT COUNT(*) FROM clips WHERE channel=? AND status='rendered'", (key,)).fetchone()[0]
            recordings = connection.execute("SELECT COUNT(*) FROM recordings WHERE channel=?", (key,)).fetchone()[0]
            state = connection.execute("SELECT enabled FROM clip_automation WHERE channel=?", (key,)).fetchone()
            enabled = bool(state[0]) if state else config.get("automation", {}).get("enabled", False)
            blockers = []
            if not recordings:
                blockers.append("No authorized recording imported; remote media access still needs verification.")
            for platform in config.get("platforms", []):
                if platform not in accounts:
                    blockers.append(f"Connect this channel's {platform} account.")
            if "tiktok" in config.get("platforms", []):
                blockers.append("TikTok inbox delivery requires manual posting; Direct Post is not enabled.")
            if config.get("automation", {}).get("publish_dry_run", True):
                blockers.append("Scheduled publishing is a dry run.")
            channels.append({"channel": key, "enabled": enabled, "recordings": recordings,
                             "rendered_clips": videos, "connected_platforms": accounts, "blockers": blockers})
        active = connection.execute("SELECT COUNT(*) FROM jobs WHERE status IN ('queued','running')").fetchone()[0]
        accepted = []
        for row in connection.execute("""SELECT d.platform, COUNT(*) AS count FROM publishing_deliveries d
            JOIN clips c ON c.video_id=d.video_id WHERE d.status='succeeded' GROUP BY d.platform"""):
            accepted.append(dict(row))
        public = []
        if connection.execute("SELECT 1 FROM sqlite_master WHERE name='publishing_checks'").fetchone():
            for row in connection.execute("SELECT platform,video_id,public_url FROM publishing_checks WHERE status='public'"):
                public.append(dict(row))
    finally:
        connection.close()
    return {"channels": channels, "active_jobs": active, "accepted_uploads":accepted, "verified_public_posts":public}


def clean_temporary_files(retention_hours, busy=False):
    """
    Remove old disposable render fragments only when idle; return deleted relative paths.

    Args:
        retention_hours: Minimum age, at least 24 hours, before a fragment is eligible.
        busy: True when jobs or an upload are active; all cleanup is skipped.

    Returns:
        Deleted fragment paths. Originals, finished MP4s, models and transcripts are always retained.
    """
    if busy:
        return []
    cutoff = time.time() - max(24, retention_hours) * 3600
    removed = []
    for base in (ROOT / "cache", ROOT / "output/videos"):
        if not base.is_dir() or base.is_symlink() or not base.resolve().is_relative_to(ROOT.resolve()):
            continue
        for folder, directories, files in os.walk(base, followlinks=False):
            for name in files:
                path = Path(folder) / name
                if not name.endswith((".part", ".rendering.mp4", ".edited.mp4")):
                    continue
                if path.is_symlink() or not path.resolve().is_relative_to(base.resolve()):
                    continue
                try:
                    if path.stat().st_mtime < cutoff:
                        path.unlink()
                        removed.append(str(path.relative_to(ROOT)))
                except FileNotFoundError:
                    continue
    return removed


def write_status(status):
    """
    Refresh only the marked machine section of the automation report from current safe health values.

    Args:
        status: Current heartbeat, channel blockers, accepted-upload counts and verified public URLs.

    Returns:
        Nothing; the audit and human-written instructions outside the markers are preserved.
    """
    path = ROOT / "AUTOMATION_STATUS.md"
    if not path.is_file():
        return
    start = "<!-- RUNTIME_STATUS_START -->"
    end = "<!-- RUNTIME_STATUS_END -->"
    lines = [start, "## Live supervisor status", "",
             f"Updated: {time.strftime('%Y-%m-%d %H:%M:%S %Z')} · PID {status['pid']}",
             f"Free disk: {status['free_gb']} GB · Low-disk pause: {status['low_disk']}", ""]
    for channel in status["channels"]:
        lines.append(f"- {channel['channel']}: queue enabled={channel['enabled']}; "
                     f"recordings={channel['recordings']}; finished clips={channel['rendered_clips']}.")
        for blocker in channel["blockers"]:
            lines.append(f"  - {blocker}")
    lines.extend(["", "Accepted file uploads (these are not public-post counts):"])
    if not status["accepted_uploads"]:
        lines.append("- YouTube: 0; TikTok: 0.")
    for item in status["accepted_uploads"]:
        lines.append(f"- {item['platform']}: {item['count']}")
    lines.extend(["", "Verified public URLs:"])
    if not status["verified_public_posts"]:
        lines.append("- None.")
    for item in status["verified_public_posts"]:
        lines.append(f"- {item['platform']}, video {item['video_id']}: {item['public_url']}")
    lines.append(end)
    source = path.read_text()
    if start in source and end in source:
        before, section = source.split(start, 1)
        old, after = section.split(end, 1)
        content = before + "\n".join(lines) + after
    else:
        content = source.rstrip() + "\n\n" + "\n".join(lines) + "\n"
    partial = path.with_suffix(".md.part")
    partial.write_text(content)
    partial.replace(path)


def publish_clips():
    """Run existing schedules for clips channels only; return nothing and isolate each channel's errors."""
    from src.dashboard.accounts import safe_error
    from src.dashboard.settings import channel_config, list_channels
    from src.publishing.scheduler import run_scheduler
    from src.publishing.verification import verify_deliveries

    verify_deliveries()
    for item in list_channels():
        try:
            if channel_config(item["key"]).get("type") == "clips":
                run_scheduler(only_channel=item["key"])
        except Exception as error:
            event("publishing_check_failed", channel=item["key"], error=safe_error(error))


def clip_jobs_running(channel):
    """
    Check whether a clip pipeline or re-cut job is working on this channel right now.

    Args:
        channel: The clips channel's configuration key.

    Returns:
        True while such a job is queued or running (its files must not be deleted).
    """
    from src.storage.database import get_connection

    connection = get_connection()
    try:
        row = connection.execute("""SELECT 1 FROM jobs WHERE channel=? AND status IN ('queued','running')
            AND kind IN ('clip_pipeline','clip_render') LIMIT 1""", (channel,)).fetchone()
    finally:
        connection.close()
    return row is not None


def manage_live_channels():
    """
    Run the rolling live recorder and footage clean-up for every clips channel.

    Returns:
        A dictionary of channel → recorder summary. One channel's error never stops another.
    """
    from src.clips.live_recorder import manage_live_recording
    from src.clips.retention import release_processed_recordings, release_published_videos
    from src.dashboard.accounts import safe_error
    from src.dashboard.settings import channel_config, list_channels

    results = {}
    for item in list_channels():
        channel = item["key"]
        try:
            config = channel_config(channel)
            if config.get("type") != "clips":
                continue
            summary = manage_live_recording(channel, config)
            summary["released"] = []
            # Never delete footage while a job may be reading it.
            if not clip_jobs_running(channel):
                summary["released"] = release_processed_recordings(channel, config)
                release_published_videos(channel, config)
            results[channel] = summary
        except Exception as error:
            event("live_recording_failed", channel=channel, error=safe_error(error))
            results[channel] = {"errors": [safe_error(error)]}
    return results


def tick(publishing=False):
    """
    Recover stale jobs, queue due work, clean safe fragments and write health; return a status dictionary.

    Args:
        publishing: Whether a schedule thread is currently sending a video.

    Returns:
        Health measurements. Low disk stops new heavy work and publishing, without deleting originals.
    """
    from src.clips.automation import queue_due_channels
    from src.dashboard.jobs import recover_stale_jobs

    options = settings()
    recover_stale_jobs()
    status = snapshot()
    removed = clean_temporary_files(options["temporary_retention_hours"], status["active_jobs"] > 0 or publishing)
    free = shutil.disk_usage(ROOT).free / 1024 ** 3
    low_disk = free < options["minimum_free_gb"]
    jobs = []
    if not low_disk:
        jobs = queue_due_channels()
    live = manage_live_channels()
    status.update({"pid": os.getpid(), "updated_at": time.time(), "alive": True,
                   "low_disk": low_disk, "free_gb": round(free, 2), "new_jobs": jobs,
                   "publishing_check_running": publishing, "live_recording": live})
    STATE.mkdir(parents=True, exist_ok=True)
    temporary = STATE / "health.json.part"
    temporary.write_text(json.dumps(status, indent=2))
    os.replace(temporary, STATE / "health.json")
    write_status(status)
    event("tick", new_jobs=jobs, low_disk=low_disk, removed_temporary_files=len(removed))
    for channel, summary in live.items():
        if summary.get("started") or summary.get("stopped") or summary.get("imported") or summary.get("released"):
            event("live_recording", channel=channel, **summary)
    return status


def run(once=False):
    """
    Supervise existing clip jobs until a termination signal; return after graceful shutdown.

    Args:
        once: True performs one maintenance/queue tick without starting a publishing thread.

    Returns:
        Nothing. A file lock prevents two runtime processes from supervising the same installation.
    """
    from src.dashboard.accounts import safe_error
    from src.storage.database import create_tables

    STATE.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(STATE / "events.jsonl", maxBytes=2_000_000, backupCount=4)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    stopped = threading.Event()

    def request_stop(number, frame):
        """Receive a termination signal and its frame; return nothing after requesting a graceful stop."""
        stopped.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    with (STATE / "runtime.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit("The clips runtime is already running.")
        create_tables()
        publisher = None
        next_publish = 0
        failures = 0
        delay = 15
        event("started", pid=os.getpid())
        while not stopped.is_set():
            try:
                delay = settings()["tick_seconds"]
                publishing = publisher is not None and publisher.is_alive()
                status = tick(publishing)
                if not once and not status["low_disk"] and not publishing and time.time() >= next_publish:
                    publisher = threading.Thread(target=publish_clips, daemon=True)
                    publisher.start()
                    next_publish = time.time() + 60
                failures = 0
            except Exception as error:
                failures += 1
                event("tick_failed", error=safe_error(error))
                if once:
                    raise
            if once:
                break
            stopped.wait(min(300, delay * 2 ** min(failures, 4)))
        # Media workers have separate sessions and keep their persistent heartbeat.
        # Uploads interrupted by forced termination keep their delivery reservation.
        if publisher is not None and publisher.is_alive():
            publisher.join(timeout=20)
        event("stopped", pid=os.getpid())


def main():
    """Read run/health options, load local secrets and print safe health; return nothing."""
    parser = argparse.ArgumentParser(description="Independent clip supervisor")
    parser.add_argument("--once", action="store_true", help="Run one queue/maintenance tick")
    parser.add_argument("--health", action="store_true", help="Check the supervisor heartbeat")
    args = parser.parse_args()
    os.chdir(ROOT)
    load_dotenv(ROOT / ".env")
    if args.health:
        path = STATE / "health.json"
        status = json.loads(path.read_text()) if path.exists() else {}
        fresh = time.time() - status.get("updated_at", 0) < 90
        running = False
        if status.get("pid", 0) > 0:
            try:
                os.kill(status["pid"], 0)
                running = True
            except ProcessLookupError:
                running = False
        print(json.dumps({**status, "heartbeat_fresh":fresh, "process_running":running,
                          "alive":fresh and running}, indent=2))
        raise SystemExit(0 if fresh and running else 1)
    run(args.once)


if __name__ == "__main__":
    main()

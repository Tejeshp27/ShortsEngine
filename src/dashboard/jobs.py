# =============================================================
# Durable browser jobs. SQLite reserves capacity and spending in
# one transaction; each job runs in a separate Python process so
# rendering does not block page requests. Jobs never publish.
# =============================================================

import json
import os
import subprocess
import sys
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit, urlunsplit

from src.dashboard.settings import PROJECT_ROOT, channel_config, job_settings
from src.storage.database import get_connection
from src.storage.finance import spent_today, utc_now

ACTIVE_STATUSES = ("queued", "running")


def job_record(job_id):
    """Return the saved job identified by job_id, with decoded payload/result."""
    connection = get_connection()
    try:
        row = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            return None
        job = dict(row)
        job["payload"] = json.loads(job["payload"])
        job["result"] = json.loads(job["result"])
        job["cost_usd"] = connection.execute(
            "SELECT COALESCE(SUM(cost_usd), 0) FROM cost_events WHERE job_id = ?", (job_id,)
        ).fetchone()[0]
        job["events"] = []
        for event in connection.execute("SELECT * FROM job_events WHERE job_id = ? ORDER BY id", (job_id,)):
            job["events"].append(dict(event))
        return job
    finally:
        connection.close()


def recent_jobs(limit=20):
    """Return up to limit recent job summaries without payloads or tokens."""
    connection = get_connection()
    try:
        rows = []
        for row in connection.execute("""
            SELECT id, channel, kind, status, stage, progress, dry_run, created_at
            FROM jobs ORDER BY created_at DESC, rowid DESC LIMIT ?
        """, (limit,)):
            rows.append(dict(row))
        return rows
    finally:
        connection.close()


def recover_stale_jobs():
    """Mark workers with missing heartbeats interrupted; never retry paid work."""
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=job_settings().stale_after_seconds)).isoformat(timespec="seconds")
    connection = get_connection()
    try:
        connection.execute("""
            UPDATE jobs SET status = 'interrupted', stage = 'Worker stopped',
                error = 'The worker stopped. Review any saved draft and uncertain spending before starting again.',
                updated_at = ?
            WHERE status IN ('queued', 'running') AND heartbeat_at < ?
        """, (utc_now(), cutoff))
        connection.commit()
    finally:
        connection.close()


def estimate_job(config, kind, payload):
    """Return a conservative dollar cap for config, job kind and user options.

    Uses the configured token ceilings and voice rate. The worker checks the
    allowance before every paid call, so an oversized script stops safely.
    """
    from src.generation.llm import calculate_cost, load_pricing
    if kind == "sync" or (kind == "discover" and not payload.get("rank")):
        return 0.0
    prices = load_pricing()
    total = 0.0
    purposes = []
    if kind == "discover":
        purposes.append(("ranking", 35000))
    if kind == "generate":
        purposes.append(("writing", config.get("articles", {}).get("max_chars", 6000) * 4 + 12000))
    if kind == "render" or (kind == "generate" and payload.get("render")):
        if payload.get("animated", True):
            purposes.append(("storyboard", 50000))
        video = config.get("video", {})
        if not payload.get("free_voice") and video.get("tts_provider") == "elevenlabs":
            rate = video.get("elevenlabs_usd_per_1000_chars")
            if rate is None or float(rate) <= 0:
                raise ValueError("Set the ElevenLabs character price in this channel, or choose the free voice.")
            total += max_spoken_chars(config) * float(rate) / 1000
    for purpose, input_ceiling in purposes:
        model = config["models"].get(purpose, config["models"]["writing"])
        if model not in prices:
            raise ValueError(f"Add {model} to config/pricing.yaml before creating a paid job.")
        max_tokens = config["llm"].get(f"{purpose}_max_tokens", 6000)
        total += calculate_cost(prices, model, 0, max_tokens, cache_write_tokens=input_ceiling)
    return round(total + 0.01, 2)


def max_spoken_chars(config):
    """Return the spoken-text safety ceiling for a channel's target duration."""
    return max(1000, min(12000, int(config.get("target_length_seconds", 75)) * 30))


def enqueue_job(channel, kind, payload, dry_run, submission_id):
    """Save authorized work and return (job_id, newly_created).

    channel selects YAML, kind is discover/generate/render/sync, payload holds
    options, dry_run forbids paid calls, and submission_id prevents duplicates.
    Capacity and today's spending reservation are checked together under a lock.
    """
    config = channel_config(channel)
    if not config.get("enabled", True):
        raise ValueError("This example channel is disabled. Configure its sources and permissions before enabling it.")
    if kind not in ("discover", "generate", "render", "sync"):
        raise ValueError("Unknown job type.")
    if not submission_id or len(submission_id) > 100:
        raise ValueError("Reload the page before submitting this form.")
    recover_stale_jobs()
    settings = job_settings()
    budget = 0.0 if dry_run else estimate_job(config, kind, payload)
    if config.get("articles", {}).get("max_chars", 6000) > settings.max_source_chars:
        raise ValueError("This channel's article limit exceeds the dashboard source-text limit.")
    # Freeze configuration so editing YAML later cannot raise a queued job's cost.
    payload = dict(payload, config=config)
    connection = get_connection()
    try:
        connection.execute("BEGIN IMMEDIATE")
        existing = connection.execute("SELECT id FROM jobs WHERE submission_id = ?", (submission_id,)).fetchone()
        if existing:
            return existing["id"], False
        if kind == "generate":
            source_url = payload.get("url", "")
            if payload.get("story_id"):
                source = connection.execute("SELECT url FROM source_stories WHERE id=? AND channel=?",
                                            (payload["story_id"], channel)).fetchone()
                if not source:
                    raise ValueError("This source was not found. Find stories again.")
                source_url = source["url"]
            parts = urlsplit(source_url)
            # An article anchor does not identify a different story.
            payload["source_url"] = urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ""))
        active = connection.execute("SELECT * FROM jobs WHERE status IN ('queued', 'running')").fetchall()
        for job in active:
            options = json.loads(job["payload"])
            if job["channel"] == channel and job["kind"] == kind:
                if kind == "generate" and payload.get("source_url") == options.get("source_url"):
                    raise ValueError("This story already has a job in progress.")
                if kind == "render" and options.get("video_id") == payload.get("video_id"):
                    raise ValueError("This video already has a render in progress.")
        if len(active) >= settings.max_in_progress:
            raise ValueError("A job is already in progress. Wait for it to finish before starting another.")
        reserved = 0.0
        for job in active:
            spent = connection.execute("SELECT COALESCE(SUM(cost_usd), 0) FROM cost_events WHERE job_id = ?",
                                       (job["id"],)).fetchone()[0]
            reserved += max(0.0, job["budget_usd"] - spent)
        if budget > 0 and spent_today(connection) + reserved + budget > settings.daily_limit_usd + 0.000001:
            raise ValueError("This job's spending allowance would exceed today's limit. Use a dry run or adjust config/dashboard.yaml.")
        job_id = uuid.uuid4().hex
        now = utc_now()
        connection.execute("""
            INSERT INTO jobs (id, channel, kind, dry_run, budget_usd, payload,
                              created_at, updated_at, heartbeat_at, submission_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (job_id, channel, kind, int(dry_run), budget, json.dumps(payload), now, now, now, submission_id))
        if kind == "render":
            connection.execute("UPDATE jobs SET video_id=? WHERE id=?", (payload.get("video_id"), job_id))
        if kind == "render" and not dry_run:
            # Take the video out of the scheduler's approved queue before the
            # worker starts replacing its output, including on render failure.
            video = connection.execute("SELECT * FROM videos WHERE id=? AND channel=?",
                                       (payload.get("video_id"), channel)).fetchone()
            if not video or video["status"] == "posted":
                raise ValueError("Choose a draft or approved video from this channel.")
            delivery = connection.execute("SELECT 1 FROM publishing_deliveries WHERE video_id=?",
                                          (video["id"],)).fetchone()
            public_post = connection.execute("SELECT 1 FROM platform_posts WHERE video_id=?", (video["id"],)).fetchone()
            if delivery or public_post:
                raise ValueError("This video has publishing history. Create a new draft rather than replacing it.")
            connection.execute("UPDATE videos SET status='drafted', approved_at=NULL WHERE id=?", (video["id"],))
        connection.commit()
        return job_id, True
    finally:
        connection.close()


def update_job(job_id, stage, progress, status=None, result=None, video_id=None, error=""):
    """Save job_id's stage, progress and optional outcome; return nothing.

    status is a final state when finishing. result contains display-safe output;
    video_id links a saved draft, and error is a plain-English failure message.
    """
    connection = get_connection()
    try:
        connection.execute("""
            UPDATE jobs SET stage = ?, progress = ?, status = COALESCE(?, status),
                result = COALESCE(?, result), video_id = COALESCE(?, video_id),
                error = ?, updated_at = ?, heartbeat_at = ?
            WHERE id = ? AND status IN ('queued', 'running')
        """, (stage, progress, status, json.dumps(result) if result is not None else None,
              video_id, error, utc_now(), utc_now(), job_id))
        connection.execute("INSERT INTO job_events (job_id, message, created_at) VALUES (?, ?, ?)",
                           (job_id, stage, utc_now()))
        connection.commit()
    finally:
        connection.close()


def launch_job(job_id):
    """Start a separate worker for job_id; return nothing.

    Only the job ID is passed on the command line. Secrets stay in the inherited
    environment. The worker saves errors and progress to the private database.
    """
    from src.storage import database
    environment = os.environ.copy()
    environment["DATABASE_PATH"] = os.path.abspath(database.DB_PATH)
    try:
        process = subprocess.Popen(
            [sys.executable, "-m", "src.dashboard.worker", job_id], cwd=PROJECT_ROOT,
            env=environment, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, start_new_session=True,
        )
        # Reap this child when it exits; otherwise finished jobs leave zombies.
        import threading
        threading.Thread(target=process.wait, daemon=True).start()
    except OSError:
        update_job(job_id, "Could not start", 0, status="failed",
                   error="Could not start the worker. Check the Python environment and try again.")

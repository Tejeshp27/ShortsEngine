# =============================================================
# src/dashboard/jobs.py — work started from the browser
#
# Writing a script or rendering a video takes minutes, far too long
# for a web page to wait. So the page saves a "job" in the database
# and starts a separate Python process (worker.py) to do the work.
# The page then shows the job's progress.
#
# Job kinds:
#   discover — find current stories (free, unless AI ranking is ticked)
#   generate — write a script, optionally render it    (heavy, paid)
#   render   — render a saved script into a video       (heavy, paid)
#   sync     — collect follower/view statistics (free)
#   publish  — post one approved video to the channel's platforms
#              (free; only started by the "Post now" button)
#   clip_render — cut a clip video again with a new moment or hook
#              title from the review page (heavy, free: no AI call)
#   clip_discovery — public Twitch clip/VOD metadata only (light, free)
#
# Safety rules, all checked together inside one database lock so two
# clicks at the same moment cannot both slip through:
#   - the same form sent twice creates only one job
#   - only `max_in_progress` heavy jobs at a time (this computer can
#     comfortably render one video at once); light jobs may run beside
#     them, one per channel and kind
#   - the job's spending allowance must fit in today's limit
#   - only a "publish" job posts, and only an approved video
# =============================================================

import json
import os
import subprocess
import sys
import threading
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit, urlunsplit

from src.dashboard.settings import PROJECT_ROOT, channel_config, job_settings
from src.storage.database import get_connection
from src.storage.finance import ROUNDING_TOLERANCE, spent_today, utc_now

ACTIVE_STATUSES = ("queued", "running")
JOB_KINDS = ("discover", "generate", "render", "sync", "publish", "clip_render", "clip_pipeline", "clip_discovery")

# Heavy jobs use a lot of memory and CPU (rendering peaked at 1.1 GB),
# so only `max_in_progress` of them may be active at once.
HEAVY_KINDS = ("generate", "render", "clip_render", "clip_pipeline")

# Clips channels reuse their source-discovery, recording and review jobs.
CLIP_CHANNEL_KINDS = ("clip_render", "publish", "sync", "clip_pipeline", "clip_discovery")

# Worst-case input sizes, in tokens, used for spending allowances.
RANKING_INPUT_CEILING = 35000
STORYBOARD_INPUT_CEILING = 50000
WRITING_PROMPT_CEILING = 12000
# An article character is at most about one token; 4x is a safe margin.
TOKENS_PER_ARTICLE_CHAR_CEILING = 4

# A small extra amount added to every paid allowance for rounding.
ALLOWANCE_CUSHION_USD = 0.01


# ---------------------------------------------------------------------------
# Reading jobs
# ---------------------------------------------------------------------------

def job_record(job_id):
    """
    Get one job, with its options, result, spending and progress messages.

    Args:
        job_id (str): The job's ID.

    Returns:
        dict or None: The job (payload and result decoded from JSON, plus
                      "cost_usd" and "events"), or None if not found.
    """
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
    """
    List the newest jobs for the jobs page (without their private options).

    Args:
        limit (int): The most jobs to return.

    Returns:
        list[dict]: Each with id, channel, kind, status, stage, progress,
                    dry_run and created_at.
    """
    connection = get_connection()
    try:
        rows = []
        for row in connection.execute("""
            SELECT id, channel, kind, status, stage, progress, dry_run, created_at
            FROM jobs
            ORDER BY created_at DESC, rowid DESC
            LIMIT ?
        """, (limit,)):
            rows.append(dict(row))
        return rows
    finally:
        connection.close()


def recover_stale_jobs():
    """
    Mark jobs whose worker has gone silent as "interrupted".

    A running worker updates its "heartbeat" every 5 seconds. If that
    stops (the computer slept, the process crashed), the job is marked
    interrupted. It is never restarted automatically, because a paid
    call may already have happened.

    Returns:
        Nothing.
    """
    silent_for = timedelta(seconds=job_settings().stale_after_seconds)
    cutoff = (datetime.now(timezone.utc) - silent_for).isoformat(timespec="seconds")

    connection = get_connection()
    try:
        connection.execute("""
            UPDATE jobs
            SET status = 'interrupted',
                stage = 'Worker stopped',
                error = 'The worker stopped. Review any saved draft and uncertain spending before starting again.',
                updated_at = ?
            WHERE status IN ('queued', 'running') AND heartbeat_at < ?
        """, (utc_now(), cutoff))
        connection.commit()
    finally:
        connection.close()


# ---------------------------------------------------------------------------
# Spending allowances
# ---------------------------------------------------------------------------

def paid_ai_steps(config, kind, payload):
    """
    List the AI calls a job may make, with each call's worst-case input.

    Args:
        config (dict): The channel's settings.
        kind (str): The job kind.
        payload (dict): The job's options.

    Returns:
        list[tuple]: (purpose, input token ceiling) pairs, e.g. [("writing", 36000)].
    """
    steps = []
    clip_metadata_enabled = (config.get("analysis", {}).get("use_llm", False)
                             or config.get("hook_title", {}).get("use_llm", False))
    if kind == "clip_pipeline" and clip_metadata_enabled:
        from src.clips.settings import load_clip_settings
        settings = load_clip_settings(config)
        count = settings.analysis.max_clips_per_recording * settings.automation.max_recordings_per_run
        count = min(count, config.get("llm", {}).get("max_calls_per_run", 15))
        for index in range(count):
            steps.append(("clip_pick", 10000))
    if kind == "discover" and payload.get("rank"):
        steps.append(("ranking", RANKING_INPUT_CEILING))
    if kind == "generate":
        article_chars = config.get("articles", {}).get("max_chars", 6000)
        writing_input = article_chars * TOKENS_PER_ARTICLE_CHAR_CEILING + WRITING_PROMPT_CEILING
        steps.append(("writing", writing_input))
        # The automatic script check (reviewer.py): a score, at most one
        # rewrite (which also carries the first draft), and a second score.
        if (config.get("quality_check") or {}).get("enabled", False):
            steps.append(("review", writing_input))
            steps.append(("rewrite", writing_input + WRITING_PROMPT_CEILING))
            steps.append(("review", writing_input))
    if job_renders_video(kind, payload) and payload.get("animated", True):
        steps.append(("storyboard", STORYBOARD_INPUT_CEILING))
    return steps


def job_renders_video(kind, payload):
    """
    Check whether a job will render a video.

    Args:
        kind (str): The job kind.
        payload (dict): The job's options.

    Returns:
        bool: True for render jobs and for generate jobs with "render" ticked.
    """
    if kind == "render":
        return True
    return kind == "generate" and bool(payload.get("render"))


def voice_allowance(config, payload):
    """
    Work out the most a paid voiceover could cost.

    Args:
        config (dict): The channel's settings.
        payload (dict): The job's options ("free_voice" skips paid voice).

    Returns:
        float: Dollars (0 for a free voice).

    Raises:
        ValueError: If ElevenLabs is chosen but its price is not set.
    """
    video = config.get("video", {})
    if payload.get("free_voice") or video.get("tts_provider") != "elevenlabs":
        return 0.0
    rate = video.get("elevenlabs_usd_per_1000_chars")
    if rate is None or float(rate) <= 0:
        raise ValueError("Set the ElevenLabs character price in this channel, or choose the free voice.")
    return max_spoken_chars(config) * float(rate) / 1000


def estimate_job(config, kind, payload):
    """
    Work out the most a job may spend (its "allowance").

    The worker checks the remaining allowance before every paid call,
    so a job that would go over stops safely instead of overspending.

    Args:
        config (dict): The channel's settings.
        kind (str): "discover", "generate", "render" or "sync".
        payload (dict): The job's options, e.g. {"render": True, "free_voice": False}.

    Returns:
        float: Dollars, rounded to cents. 0.0 for free jobs.

    Raises:
        ValueError: If a model has no price in config/pricing.yaml.
    """
    # Imported here so this file can be loaded without the anthropic
    # package (the web pages that only read jobs don't need it).
    from src.generation.llm import calculate_cost, load_pricing

    steps = paid_ai_steps(config, kind, payload)
    voice_cost = 0.0
    if job_renders_video(kind, payload):
        voice_cost = voice_allowance(config, payload)
    if not steps and voice_cost == 0:
        return 0.0

    prices = load_pricing()
    total = voice_cost
    for purpose, input_ceiling in steps:
        model = config["models"].get(purpose) or config["models"].get("writing")
        if model not in prices:
            raise ValueError(f"Add {model} to config/pricing.yaml before creating a paid job.")
        max_tokens = config["llm"].get(f"{purpose}_max_tokens", 6000)
        if kind == "clip_pipeline":
            max_tokens = config.get("llm", {}).get("clip_metadata_max_tokens", 400)
        # Input is priced as a prompt-cache write, the most expensive kind.
        total += calculate_cost(prices, model, 0, max_tokens, cache_write_tokens=input_ceiling)
    return round(total + ALLOWANCE_CUSHION_USD, 2)


def max_spoken_chars(config):
    """
    Get the longest voiceover text a channel's videos may have.

    Speech is about 15 characters per second; 30 gives a generous margin.

    Args:
        config (dict): The channel's settings.

    Returns:
        int: A character limit between 1,000 and 12,000.
    """
    target_seconds = int(config.get("target_length_seconds", 75))
    return max(1000, min(12000, target_seconds * 30))


# ---------------------------------------------------------------------------
# Creating jobs
# ---------------------------------------------------------------------------

def check_job_request(config, kind, submission_id, settings):
    """
    Refuse a job request that is invalid before touching the database.

    Args:
        config (dict): The channel's settings.
        kind (str): The requested job kind.
        submission_id (str): The form's random ID.
        settings (JobSettings): The dashboard limits.

    Raises:
        ValueError: With a message for the page.
    """
    if not config.get("enabled", True):
        raise ValueError("This example channel is disabled. Configure its sources and permissions before enabling it.")
    if kind not in JOB_KINDS:
        raise ValueError("Unknown job type.")
    # Imported here to keep the clips package out of pages that never need it.
    from src.clips.settings import is_clips_channel
    if is_clips_channel(config) and kind not in CLIP_CHANNEL_KINDS:
        raise ValueError("Clips channels make new videos with: python -m src.clips --channel <name> run")
    if kind in ("clip_render", "clip_pipeline", "clip_discovery") and not is_clips_channel(config):
        raise ValueError("Only videos made from stream clips can be re-cut.")
    if not submission_id or len(submission_id) > 100:
        raise ValueError("Reload the page before submitting this form.")
    if config.get("articles", {}).get("max_chars", 6000) > settings.max_source_chars:
        raise ValueError("This channel's article limit exceeds the dashboard source-text limit.")


def find_source_url(connection, channel, payload):
    """
    Get the article address a "generate" job will write about.

    Args:
        connection: An open SQLite connection.
        channel (str): The channel.
        payload (dict): Has either "story_id" (a found story) or "url".

    Returns:
        str: The address without any "#section" part, since that part
             points into the same article, not to a different story.

    Raises:
        ValueError: If the chosen story no longer exists.
    """
    source_url = payload.get("url", "")
    if payload.get("story_id"):
        source = connection.execute(
            "SELECT url FROM source_stories WHERE id = ? AND channel = ?", (payload["story_id"], channel)
        ).fetchone()
        if not source:
            raise ValueError("This source was not found. Find stories again.")
        source_url = source["url"]
    parts = urlsplit(source_url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ""))


def check_not_duplicate(active_jobs, channel, kind, payload):
    """
    Refuse a job that repeats one already in progress.

    Args:
        active_jobs (list): Rows of queued or running jobs.
        channel (str): The new job's channel.
        kind (str): The new job's kind.
        payload (dict): The new job's options.

    Raises:
        ValueError: If the same work is already happening.
    """
    for job in active_jobs:
        if job["channel"] != channel or job["kind"] != kind:
            continue
        options = json.loads(job["payload"])
        if kind == "generate" and options.get("source_url") == payload.get("source_url"):
            raise ValueError("This story already has a job in progress.")
        if kind == "render" and options.get("video_id") == payload.get("video_id"):
            raise ValueError("This video already has a render in progress.")
        if kind == "discover":
            raise ValueError("Finding stories for this channel is already in progress. Wait for it to finish.")
        if kind == "sync":
            raise ValueError("Collecting this channel's statistics is already in progress.")
        if kind == "publish" and options.get("video_id") == payload.get("video_id"):
            raise ValueError("This video is already being posted.")
        if kind == "clip_render" and options.get("video_id") == payload.get("video_id"):
            raise ValueError("This clip is already being re-cut.")
        if kind == "clip_pipeline":
            raise ValueError("This channel already has a clips pipeline running.")
        if kind == "clip_discovery":
            raise ValueError("This channel already has source discovery running.")


def check_capacity(active_jobs, kind, settings):
    """
    Refuse a heavy job if too many heavy jobs are already active.

    Light jobs (finding stories, collecting statistics) are not limited
    here, so they still work while a long render is running.

    Args:
        active_jobs (list): Rows of queued or running jobs.
        kind (str): The new job's kind.
        settings (JobSettings): The dashboard limits.

    Raises:
        ValueError: If no heavy-job slot is free.
    """
    if kind not in HEAVY_KINDS:
        return
    heavy_count = 0
    for job in active_jobs:
        if job["kind"] in HEAVY_KINDS:
            heavy_count += 1
    if heavy_count >= settings.max_in_progress:
        raise ValueError("A video is already being written or rendered. Wait for it to finish before starting another.")


def check_daily_budget(connection, active_jobs, budget, settings):
    """
    Refuse a paid job whose allowance would pass today's spending limit.

    Counts money already spent today PLUS the unspent allowance of every
    active job, since those jobs may still spend it.

    Args:
        connection: An open SQLite connection.
        active_jobs (list): Rows of queued or running jobs.
        budget (float): The new job's allowance.
        settings (JobSettings): The dashboard limits.

    Raises:
        ValueError: If the limit would be passed.
    """
    if budget <= 0:
        return
    reserved = 0.0
    for job in active_jobs:
        spent = connection.execute(
            "SELECT COALESCE(SUM(cost_usd), 0) FROM cost_events WHERE job_id = ?", (job["id"],)
        ).fetchone()[0]
        reserved += max(0.0, job["budget_usd"] - spent)

    if spent_today(connection) + reserved + budget > settings.daily_limit_usd + ROUNDING_TOLERANCE:
        raise ValueError("This job's spending allowance would exceed today's limit. "
                         "Use a dry run or adjust config/dashboard.yaml.")


def prepare_video_for_render(connection, channel, video_id):
    """
    Take a video back to "drafted" before it is re-rendered.

    A finished video must be reviewed again, so it leaves the scheduler's
    approved queue now — even if the render later fails.

    Args:
        connection: An open SQLite connection (inside the job transaction).
        channel (str): The job's channel.
        video_id (int): The video to render.

    Raises:
        ValueError: If the video is not from this channel or has already
                    been sent to a platform.
    """
    video = connection.execute(
        "SELECT * FROM videos WHERE id = ? AND channel = ?", (video_id, channel)
    ).fetchone()
    if not video or video["status"] == "posted":
        raise ValueError("Choose a draft or approved video from this channel.")

    delivery = connection.execute("SELECT 1 FROM publishing_deliveries WHERE video_id = ?", (video_id,)).fetchone()
    public_post = connection.execute("SELECT 1 FROM platform_posts WHERE video_id = ?", (video_id,)).fetchone()
    if delivery or public_post:
        raise ValueError("This video has publishing history. Create a new draft rather than replacing it.")

    connection.execute("UPDATE videos SET status = 'drafted', approved_at = NULL WHERE id = ?", (video_id,))


def enqueue_job(channel, kind, payload, dry_run, submission_id):
    """
    Save a new job after checking every safety rule.

    Args:
        channel (str): The channel, e.g. "news".
        kind (str): "discover", "generate", "render" or "sync".
        payload (dict): The job's options from the form.
        dry_run (bool): True means no paid calls at all.
        submission_id (str): The form's random ID; sending the same form
                             twice returns the first job instead of a new one.

    Returns:
        tuple: (job_id, newly_created). newly_created is False when this
               form was already submitted.

    Raises:
        ValueError: If any rule refuses the job (message is for the page).
    """
    config = channel_config(channel)
    settings = job_settings()
    check_job_request(config, kind, submission_id, settings)
    recover_stale_jobs()

    if dry_run:
        budget = 0.0
    else:
        budget = estimate_job(config, kind, payload)

    # Save a copy of the channel settings with the job, so editing the
    # YAML while the job waits cannot raise its cost.
    payload = dict(payload)
    payload["config"] = config

    connection = get_connection()
    try:
        # Lock the database for writing until commit. Every check below
        # therefore sees the same state, and no other request can sneak
        # a job in between the checks and the insert.
        connection.execute("BEGIN IMMEDIATE")

        existing = connection.execute("SELECT id FROM jobs WHERE submission_id = ?", (submission_id,)).fetchone()
        if existing:
            return existing["id"], False

        if kind == "generate":
            payload["source_url"] = find_source_url(connection, channel, payload)

        active_jobs = connection.execute("SELECT * FROM jobs WHERE status IN ('queued', 'running')").fetchall()
        check_not_duplicate(active_jobs, channel, kind, payload)
        check_capacity(active_jobs, kind, settings)
        check_daily_budget(connection, active_jobs, budget, settings)

        video_id = None
        if kind in ("render", "clip_render"):
            video_id = payload.get("video_id")
            if not dry_run:
                prepare_video_for_render(connection, channel, video_id)

        job_id = uuid.uuid4().hex
        now = utc_now()
        connection.execute("""
            INSERT INTO jobs (id, channel, kind, dry_run, budget_usd, payload, video_id,
                              created_at, updated_at, heartbeat_at, submission_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (job_id, channel, kind, int(dry_run), budget, json.dumps(payload), video_id,
              now, now, now, submission_id))
        connection.commit()
        return job_id, True
    finally:
        # Closing without commit (after an error) cancels every change.
        connection.close()


# ---------------------------------------------------------------------------
# Running jobs
# ---------------------------------------------------------------------------

def update_job(job_id, stage, progress, status=None, result=None, video_id=None, error=""):
    """
    Save a job's progress (and its outcome when it finishes).

    Only active jobs are changed, so a job already marked interrupted
    stays interrupted.

    Args:
        job_id (str): The job.
        stage (str): A short description shown on the page, e.g. "Writing a script".
        progress (int): 0 to 100.
        status (str or None): A final status ("succeeded"/"failed"), or None.
        result (dict or None): What the job produced, shown on the page.
        video_id (int or None): The draft the job made or changed.
        error (str): A plain-English failure message, or "".

    Returns:
        Nothing.
    """
    result_text = None
    if result is not None:
        result_text = json.dumps(result)
    now = utc_now()

    connection = get_connection()
    try:
        # COALESCE(?, status) means "use the new value, or keep the old
        # one if the new value is empty (NULL)".
        connection.execute("""
            UPDATE jobs
            SET stage = ?, progress = ?, status = COALESCE(?, status),
                result = COALESCE(?, result), video_id = COALESCE(?, video_id),
                error = ?, updated_at = ?, heartbeat_at = ?
            WHERE id = ? AND status IN ('queued', 'running')
        """, (stage, progress, status, result_text, video_id, error, now, now, job_id))
        connection.execute(
            "INSERT INTO job_events (job_id, message, created_at) VALUES (?, ?, ?)", (job_id, stage, now)
        )
        connection.commit()
    finally:
        connection.close()


def launch_job(job_id):
    """
    Start a separate Python process that runs the job (worker.py).

    Only the job ID is passed on the command line; API keys reach the
    worker through its inherited environment, never as visible arguments.

    Args:
        job_id (str): The job to run.

    Returns:
        Nothing.
    """
    from src.storage import database
    environment = os.environ.copy()
    # Make sure the worker uses the same database file as the web app.
    environment["DATABASE_PATH"] = os.path.abspath(database.DB_PATH)
    try:
        process = subprocess.Popen(
            [sys.executable, "-m", "src.dashboard.worker", job_id],
            cwd=PROJECT_ROOT, env=environment,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            # Its own session: stopping the web server does not kill a render.
            start_new_session=True,
        )
    except OSError:
        update_job(job_id, "Could not start", 0, status="failed",
                   error="Could not start the worker. Check the Python environment and try again.")
        return
    # Wait for the process in the background. Without this, each
    # finished worker would linger as a "zombie" process entry.
    threading.Thread(target=process.wait, daemon=True).start()

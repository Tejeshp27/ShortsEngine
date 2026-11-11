# =============================================================
# Execute one saved dashboard job in a separate process.
# Source selection, scripts and renders reuse the pipeline modules.
# A completed render stays drafted for human review. No publishing
# function is imported or called by this worker.
# =============================================================

import json
import os
import signal
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from src.dashboard.jobs import job_record, max_spoken_chars, update_job
from src.dashboard.settings import job_settings
from src.generation.llm import LLMClient
from src.storage.database import create_tables, get_connection
from src.storage.finance import CostTracker, utc_now


def heartbeat(job_id, finished):
    """Update job_id's heartbeat until the finished event is set; return nothing."""
    while not finished.wait(5):
        connection = get_connection()
        try:
            connection.execute("UPDATE jobs SET heartbeat_at = ? WHERE id = ? AND status = 'running'",
                               (utc_now(), job_id))
            connection.commit()
        finally:
            connection.close()


def saved_story(channel, story_id):
    """Return a previously fetched story_id for channel, or raise ValueError."""
    connection = get_connection()
    try:
        row = connection.execute("SELECT * FROM source_stories WHERE id = ? AND channel = ?",
                                 (story_id, channel)).fetchone()
        if row is None:
            raise ValueError("This source was not found. Find stories again.")
        return dict(row)
    finally:
        connection.close()


def discover(job, llm):
    """Fetch current sources for job, optionally rank with llm, and return IDs."""
    from src.main import get_candidate_stories
    from src.generation.ranking import rank_stories
    payload = job["payload"]
    update_job(job["id"], "Reading channel sources", 15)
    stories = get_candidate_stories(payload["config"], job["channel"])
    query = payload.get("topic", "").casefold().strip()
    if query:
        filtered = []
        for story in stories:
            haystack = (story["title"] + " " + story.get("summary", "")).casefold()
            if query in haystack:
                filtered.append(story)
        stories = filtered
    stories = stories[:60]
    # Only web links are shown as sources, and bounded headlines keep ranking
    # prompts predictable even if a feed contains unusually large fields.
    from urllib.parse import urlsplit
    valid_stories = []
    for story in stories:
        parts = urlsplit(story.get("url", ""))
        if parts.scheme not in ("http", "https") or not parts.hostname or parts.username:
            continue
        story["title"] = story["title"][:300]
        story["source"] = story["source"][:100]
        story["summary"] = story.get("summary", "")[:3000]
        valid_stories.append(story)
    stories = valid_stories
    reason = "Newest first. No ranking charge."
    ranked = []
    if stories and payload.get("rank"):
        update_job(job["id"], "Ranking source headlines", 45)
        ranked, reason = rank_stories(stories, payload["config"], llm, channel_key=job["channel"])
        if job["dry_run"]:
            reason = "Dry run: ranking was simulated. No paid call was made."
    ids = []
    ordered = list(ranked)
    for story in stories:
        if story not in ordered:
            ordered.append(story)
    connection = get_connection()
    try:
        for number, story in enumerate(ordered, start=1):
            published = story.get("published", "")
            if hasattr(published, "isoformat"):
                published = published.isoformat()
            connection.execute("""
                INSERT INTO source_stories (channel, url, title, source, summary, published, fetched_at, rank)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(channel, url) DO UPDATE SET title=excluded.title, summary=excluded.summary,
                    fetched_at=excluded.fetched_at, rank=excluded.rank
            """, (job["channel"], story["url"], story["title"], story["source"],
                  story.get("summary", ""), str(published), utc_now(), number))
            row = connection.execute("SELECT id FROM source_stories WHERE channel = ? AND url = ?",
                                     (job["channel"], story["url"])).fetchone()
            ids.append(row["id"])
        connection.commit()
    finally:
        connection.close()
    return {"story_ids": ids, "reason": reason}


def article_for_story(story, config):
    """Return cached or safely downloaded source text for story and config.

    A sufficiently detailed RSS summary is allowed if the full page is
    unavailable. Empty or headline-only sources stop before any paid writing.
    """
    from src.sources.articles import cache_path_for_url, extract_paragraphs, strip_html, trim_text
    from src.sources.public_web import download_public_html
    cache = Path(cache_path_for_url(story["url"]))
    text = ""
    if cache.exists():
        try:
            text = json.loads(cache.read_text(encoding="utf-8"))["text"]
        except (ValueError, KeyError):
            text = ""
    if not text and config.get("articles", {}).get("fetch_full_text", True):
        try:
            text = extract_paragraphs(download_public_html(story["url"]))
        except ValueError:
            if not story.get("summary"):
                raise
        if text:
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps({"url": story["url"], "text": text}), encoding="utf-8")
    if not text:
        text = strip_html(story.get("summary", ""))
    text = trim_text(text, config.get("articles", {}).get("max_chars", 6000))
    if len(text.split()) < 25:
        raise ValueError("There is too little source text to write an accurate script. Choose a more detailed article.")
    return text


def render_saved_video(job, video_id, llm, tracker):
    """Render video_id using job options and llm; save costs with tracker.

    Returns the MP4 path. The output is placed back in the review queue because
    approving a script does not automatically approve its finished video.
    """
    from src.dashboard.media import output_file
    from src.storage.queries import get_video_by_id, set_video_path
    from src.video.render import render_video
    from src.video.tts import _extract_spoken_text
    payload = job["payload"]
    config = payload["config"]
    video = get_video_by_id(video_id)
    if not video or video["channel"] != job["channel"]:
        raise ValueError("The video does not belong to this channel.")
    if video["status"] == "posted":
        raise ValueError("Create a new draft instead of changing a posted video.")
    script = output_file(video["script_path"], ".md")
    if script is None:
        raise ValueError("The source script file is missing.")
    if len(_extract_spoken_text(str(script))) > max_spoken_chars(config):
        raise ValueError("The script is too long for this job's voice allowance. Shorten it before rendering.")
    tracker.attach_video(video_id)
    update_job(job["id"], "Generating voiceover and animated video", 65, video_id=video_id)
    settings = config.get("video", {})
    provider = settings.get("tts_provider", "edge")
    if payload.get("free_voice"):
        provider = "edge"
    voice_field = {"macos": "macos_voice", "kokoro": "kokoro_voice", "elevenlabs": "elevenlabs_voice"}.get(provider, "voice")
    voice = settings.get(voice_field, settings.get("voice", "en-US-AriaNeural"))
    path = render_video(str(script), video["story_title"], job["channel"], voice,
                        font_size=settings.get("font_size", 90), tts_provider=provider,
                        channel_config=config, llm=llm if payload.get("animated", True) else None,
                        animated=payload.get("animated", True), cost_tracker=tracker)
    if output_file(path, ".mp4") is None:
        raise ValueError("Rendering did not produce a video file. Check that FFmpeg is installed.")
    set_video_path(video_id, path)
    connection = get_connection()
    try:
        connection.execute("UPDATE videos SET status = 'drafted', approved_at = NULL WHERE id = ?", (video_id,))
        connection.commit()
    finally:
        connection.close()
    return path


def generate(job, llm, tracker):
    """Create a sourced draft for job with llm and tracker; return its ID."""
    from src.generation.writer import write_script
    from src.storage.history import mark_processed
    from src.storage.queries import save_video
    payload = job["payload"]
    if payload.get("story_id"):
        story = saved_story(job["channel"], payload["story_id"])
    else:
        from urllib.parse import urlsplit
        url = payload.get("url", "").strip()
        story = {"url": url, "title": payload.get("title", "").strip() or urlsplit(url).path.rstrip("/").split("/")[-1].replace("-", " ") or urlsplit(url).hostname,
                 "source": urlsplit(url).hostname or "Article", "summary": ""}
    story["url"] = payload.get("source_url") or story["url"]
    connection = get_connection()
    try:
        existing = connection.execute("SELECT id FROM videos WHERE channel = ? AND story_url = ?",
                                      (job["channel"], story["url"])).fetchone()
    finally:
        connection.close()
    if existing:
        raise ValueError(f"This story already has draft #{existing['id']}. Open it in the library instead.")
    update_job(job["id"], "Reading and checking the source article", 20)
    article = article_for_story(story, payload["config"])
    if job["dry_run"]:
        return {"message": "Source checked. Writing and rendering were skipped; no paid calls or draft records were made.",
                "source_title": story["title"], "source_words": len(article.split())}
    update_job(job["id"], "Writing a sourced script", 40)
    script = write_script(story, article, payload["config"], job["channel"], llm)
    video_id = save_video(job["channel"], story["title"], story["url"], script, 0)
    tracker.attach_video(video_id)
    update_job(job["id"], "Script saved for review", 55, video_id=video_id)
    mark_processed(job["channel"], story, script)
    if payload.get("render"):
        render_saved_video(job, video_id, llm, tracker)
    return {"video_id": video_id, "message": "Draft ready for your review. Nothing has been published."}


def run_job(job_id):
    """Claim and execute job_id once, recording its result or a clear error."""
    connection = get_connection()
    try:
        claimed = connection.execute("UPDATE jobs SET status = 'running', heartbeat_at = ? WHERE id = ? AND status = 'queued'",
                                     (utc_now(), job_id)).rowcount
        connection.commit()
    finally:
        connection.close()
    if not claimed:
        return
    job = job_record(job_id)
    tracker = CostTracker(job["channel"], job_id=job_id, budget=job["budget_usd"])
    config = job["payload"]["config"]
    llm = LLMClient(dry_run=bool(job["dry_run"]), max_calls=config.get("llm", {}).get("max_calls_per_run", 5),
                    cost_tracker=None if job["dry_run"] else tracker)
    try:
        if job["kind"] == "discover":
            result = discover(job, llm)
        elif job["kind"] == "generate":
            result = generate(job, llm, tracker)
        elif job["kind"] == "render":
            if job["dry_run"]:
                result = {"message": "Dry run: voice generation and rendering were skipped. No paid calls were made."}
            else:
                video_id = job["payload"]["video_id"]
                render_saved_video(job, video_id, llm, tracker)
                result = {"video_id": video_id, "message": "Rendered video is ready for review."}
        else:
            from src.analytics.accounts import sync_channel
            result = {"message": sync_channel(job["channel"], dry_run=bool(job["dry_run"]))}
        update_job(job_id, "Complete", 100, status="succeeded", result=result)
    except Exception as error:
        # Provider errors must not expose token-bearing request URLs in the UI.
        from src.dashboard.accounts import safe_error
        update_job(job_id, "Stopped", job_record(job_id)["progress"], status="failed", error=safe_error(error))
    finally:
        llm.write_cost_log(job["channel"], note=f"dashboard job {job_id}")


def time_limit_reached(signum, frame):
    """Raise a clear timeout when the operating system alarm fires."""
    raise TimeoutError("This job exceeded the configured time limit. Any saved draft remains in the library.")


def main():
    """Run the job ID passed on the command line, with heartbeat and timeout."""
    load_dotenv()
    create_tables()
    job_id = sys.argv[1]
    finished = threading.Event()
    threading.Thread(target=heartbeat, args=(job_id, finished), daemon=True).start()
    # SIGALRM works on macOS and Linux and interrupts a stalled network/render
    # operation. The normal error handler then saves a useful job outcome.
    signal.signal(signal.SIGALRM, time_limit_reached)
    signal.alarm(job_settings().max_job_minutes * 60)
    try:
        run_job(job_id)
    finally:
        finished.set()
        signal.alarm(0)


if __name__ == "__main__":
    main()

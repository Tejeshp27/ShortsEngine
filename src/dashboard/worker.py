# =============================================================
# src/dashboard/worker.py — runs one browser job in its own process
#
# jobs.launch_job() starts this file as:
#     python -m src.dashboard.worker <job_id>
#
# It reuses the normal pipeline (story fetching, script writing,
# rendering) and saves progress to the database so the job page can
# show it. A finished video always goes back to "drafted" for human
# review. The only job that posts is "publish", started by the
# "Post now" button on an approved video (see publish_saved_video).
#
# Note on imports: several pipeline functions are imported INSIDE
# the functions below rather than at the top. Two reasons:
#   1. Speed: a "find stories" job never loads the video renderer.
#   2. Tests replace those functions (e.g. write_script) with fakes;
#      importing at call time makes sure the fake is the one used.
# =============================================================

import json
import signal
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv

from src.dashboard.jobs import job_record, max_spoken_chars, update_job
from src.dashboard.settings import job_settings
from src.generation.llm import LLMClient
from src.storage.database import create_tables, get_connection
from src.storage.finance import CostTracker, utc_now

HEARTBEAT_SECONDS = 5
MAX_STORIES_SHOWN = 60
MIN_SOURCE_WORDS = 25


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def heartbeat(job_id, finished):
    """
    Tell the database "still alive" every few seconds until the job ends.

    Runs in a background thread. If these updates stop, the dashboard
    marks the job interrupted (see jobs.recover_stale_jobs).

    Args:
        job_id (str): The running job.
        finished (threading.Event): Set by main() when the job is done.

    Returns:
        Nothing.
    """
    # finished.wait(5) pauses up to 5 seconds and returns True once the
    # job is done, which ends the loop.
    while not finished.wait(HEARTBEAT_SECONDS):
        connection = get_connection()
        try:
            connection.execute(
                "UPDATE jobs SET heartbeat_at = ? WHERE id = ? AND status = 'running'", (utc_now(), job_id)
            )
            connection.commit()
        finally:
            connection.close()


def saved_story(channel, story_id):
    """
    Get a story found earlier by a "find stories" job.

    Args:
        channel (str): The channel the story must belong to.
        story_id (int): The story's ID.

    Returns:
        dict: The story's saved details (url, title, source, summary...).

    Raises:
        ValueError: If there is no such story for this channel.
    """
    connection = get_connection()
    try:
        row = connection.execute(
            "SELECT * FROM source_stories WHERE id = ? AND channel = ?", (story_id, channel)
        ).fetchone()
        if row is None:
            raise ValueError("This source was not found. Find stories again.")
        return dict(row)
    finally:
        connection.close()


def is_web_link(url):
    """
    Check that an address is an ordinary http(s) link without a login.

    Args:
        url (str): The address.

    Returns:
        bool: True for links like "https://npr.org/story".
    """
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        return False
    if not parts.hostname or parts.username:
        return False
    return True


# ---------------------------------------------------------------------------
# "Find stories" jobs
# ---------------------------------------------------------------------------

def filter_by_topic(stories, topic):
    """
    Keep only stories whose title or summary mentions a topic.

    Args:
        stories (list[dict]): Stories from the channel's feeds.
        topic (str): Text to look for (any capitalisation), or "" for all.

    Returns:
        list[dict]: The matching stories.
    """
    topic = topic.casefold().strip()
    if not topic:
        return stories
    matches = []
    for story in stories:
        text = (story["title"] + " " + story.get("summary", "")).casefold()
        if topic in text:
            matches.append(story)
    return matches


def clean_stories(stories):
    """
    Drop stories without a normal web link and shorten very long fields.

    Shortening keeps the ranking prompt (and its cost) predictable even
    if a feed sends an unusually long title or summary.

    Args:
        stories (list[dict]): Stories from the feeds.

    Returns:
        list[dict]: The usable stories.
    """
    cleaned = []
    for story in stories:
        if not is_web_link(story.get("url", "")):
            continue
        story["title"] = story["title"][:300]
        story["source"] = story["source"][:100]
        story["summary"] = story.get("summary", "")[:3000]
        cleaned.append(story)
    return cleaned


def published_text(story):
    """
    Get a story's publish time as text for saving and display.

    Args:
        story (dict): A story with "published" and/or "published_ts".

    Returns:
        str: For example "2026-11-11T06:30:00+00:00", or "" if unknown.
    """
    published = story.get("published", "")
    if hasattr(published, "isoformat"):
        return published.isoformat()
    if published:
        return str(published)
    # Feeds and news search give a Unix timestamp; 0 means "unknown".
    timestamp = story.get("published_ts", 0.0)
    if not timestamp:
        return ""
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat(timespec="seconds")


def merge_search_results(feed_stories, search_stories, channel):
    """
    Add web search results after the channel's own matching feed stories.

    Duplicates (same link) and stories that already have a script are
    left out.

    Args:
        feed_stories (list[dict]): Matching stories from the channel's feeds.
        search_stories (list[dict]): Stories from search_news().
        channel (str): The channel (to skip stories it already covered).

    Returns:
        list[dict]: The combined stories.
    """
    from src.storage.history import load_processed, remove_processed

    seen_urls = set()
    for story in feed_stories:
        seen_urls.add(story["url"])

    combined = list(feed_stories)
    for story in remove_processed(search_stories, load_processed(channel)):
        if story["url"] in seen_urls:
            continue
        seen_urls.add(story["url"])
        combined.append(story)
    return combined


def describe_sources(feed_count, search_count, searched, notes):
    """
    Write the line shown above the found stories, explaining where they came from.

    Args:
        feed_count (int): Stories from the channel's own feeds.
        search_count (int): Stories added by the web news search.
        searched (bool): Whether a web news search ran.
        notes (list[str]): Warnings from the search (e.g. a busy service).

    Returns:
        str: For example "Newest first. 2 from this channel's feeds and
             28 from web news search. No ranking charge."
    """
    if searched:
        text = (f"Newest first. {feed_count} from this channel's feeds and "
                f"{search_count} from web news search. No ranking charge.")
    else:
        text = "Newest first. No ranking charge."
    for note in notes:
        text += " " + note
    return text


def save_found_stories(channel, stories):
    """
    Save found stories so the "new video" page can list them.

    Args:
        channel (str): The channel.
        stories (list[dict]): Stories in the order to show them.

    Returns:
        list[int]: The saved stories' IDs, in the same order.
    """
    ids = []
    connection = get_connection()
    try:
        rank = 1
        for story in stories:
            published = published_text(story)
            # A story seen before (same channel and link) is updated, not duplicated.
            connection.execute("""
                INSERT INTO source_stories (channel, url, title, source, summary, published, fetched_at, rank)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(channel, url) DO UPDATE SET
                    title = excluded.title, summary = excluded.summary,
                    fetched_at = excluded.fetched_at, rank = excluded.rank
            """, (channel, story["url"], story["title"], story["source"],
                  story.get("summary", ""), published, utc_now(), rank))
            row = connection.execute(
                "SELECT id FROM source_stories WHERE channel = ? AND url = ?", (channel, story["url"])
            ).fetchone()
            ids.append(row["id"])
            rank += 1
        connection.commit()
    finally:
        connection.close()
    return ids


def discover(job, llm):
    """
    Run a "find stories" job: read the feeds, filter, optionally rank, save.

    When a topic is typed and the channel has a "search:" section, the
    web's recent news is searched too (free; see news_search.py), so a
    topic the channel's feeds never mention can still be found.

    Args:
        job (dict): The job record.
        llm (LLMClient): Used only if AI ranking was ticked.

    Returns:
        dict: "story_ids" (in display order) and "reason" (how they were ordered).
    """
    from src.main import get_candidate_stories
    from src.generation.ranking import rank_stories

    from src.sources.news_search import search_news, search_settings

    payload = job["payload"]
    config = payload["config"]
    topic = payload.get("topic", "").strip()

    update_job(job["id"], "Reading channel sources", 15)
    stories = filter_by_topic(get_candidate_stories(config, job["channel"]), topic)
    feed_count = len(stories)

    searched = bool(topic) and search_settings(config) is not None
    notes = []
    search_count = 0
    if searched:
        update_job(job["id"], "Searching recent news across the web", 30)
        found, notes = search_news(topic, config)
        stories = merge_search_results(stories, found, job["channel"])
        search_count = len(stories) - feed_count

    stories = clean_stories(stories[:MAX_STORIES_SHOWN])
    reason = describe_sources(feed_count, search_count, searched, notes)
    ordered = []
    if stories and payload.get("rank"):
        update_job(job["id"], "Ranking source headlines", 45)
        ranked, reason = rank_stories(stories, payload["config"], llm, channel_key=job["channel"])
        ordered = list(ranked)
        if job["dry_run"]:
            reason = "Dry run: ranking was simulated. No paid call was made."
        # Keep any search warnings visible after ranking replaces the text.
        for note in notes:
            reason += " " + note

    # Ranked stories first, then every other story in feed order.
    for story in stories:
        if story not in ordered:
            ordered.append(story)

    return {"story_ids": save_found_stories(job["channel"], ordered), "reason": reason}


# ---------------------------------------------------------------------------
# "Write a script" and "render" jobs
# ---------------------------------------------------------------------------

def article_for_story(story, config):
    """
    Get the article text a script will be written from.

    Uses the cached copy if there is one, otherwise downloads the page
    safely. If the page can't be read, a detailed feed summary is used
    instead. Too little text stops the job BEFORE any paid writing, so
    the AI is never asked to write from a headline alone.

    Args:
        story (dict): Has "url" and optionally "summary".
        config (dict): The channel's settings.

    Returns:
        str: The article text, trimmed to the channel's limit.

    Raises:
        ValueError: If there is not enough source text.
    """
    from src.sources.articles import cache_path_for_url, extract_paragraphs, strip_html, trim_text
    from src.sources.public_web import download_public_html

    articles_config = config.get("articles", {})
    cache = Path(cache_path_for_url(story["url"]))
    text = ""
    if cache.exists():
        try:
            text = json.loads(cache.read_text(encoding="utf-8"))["text"]
        except (ValueError, KeyError):
            text = ""

    if not text and articles_config.get("fetch_full_text", True):
        try:
            text = extract_paragraphs(download_public_html(story["url"]))
        except ValueError:
            # No summary to fall back on: the download error is the answer.
            if not story.get("summary"):
                raise
        if text:
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps({"url": story["url"], "text": text}), encoding="utf-8")

    if not text:
        text = strip_html(story.get("summary", ""))
    text = trim_text(text, articles_config.get("max_chars", 6000))
    if len(text.split()) < MIN_SOURCE_WORDS:
        raise ValueError("There is too little source text to write an accurate script. "
                         "Choose a more detailed article.")
    return text


def choose_voice(video_settings, free_voice):
    """
    Pick the voice provider and voice name for a render.

    Args:
        video_settings (dict): The channel's "video" settings.
        free_voice (bool): True forces the free edge-tts voice.

    Returns:
        tuple: (provider, voice), e.g. ("edge", "en-US-AriaNeural").
    """
    provider = video_settings.get("tts_provider", "edge")
    if free_voice:
        provider = "edge"

    # Each provider has its own setting for the voice name.
    if provider == "macos":
        voice_field = "macos_voice"
    elif provider == "kokoro":
        voice_field = "kokoro_voice"
    elif provider == "elevenlabs":
        voice_field = "elevenlabs_voice"
    else:
        voice_field = "voice"

    default_voice = video_settings.get("voice", "en-US-AriaNeural")
    return provider, video_settings.get(voice_field, default_voice)


def mark_video_for_review(video_id):
    """
    Put a freshly rendered video back in the review queue.

    Approving a SCRIPT does not approve the finished VIDEO, so it must
    be watched and approved again.

    Args:
        video_id (int): The video.
    """
    connection = get_connection()
    try:
        connection.execute("UPDATE videos SET status = 'drafted', approved_at = NULL WHERE id = ?", (video_id,))
        connection.commit()
    finally:
        connection.close()


def render_saved_video(job, video_id, llm, tracker):
    """
    Render a saved script into a video.

    Args:
        job (dict): The job record (its payload holds the options).
        video_id (int): The video to render.
        llm (LLMClient): Used for the animated storyboard.
        tracker (CostTracker): Records paid voice and AI calls.

    Returns:
        str: The path of the finished MP4.

    Raises:
        ValueError: If the video can't be rendered (wrong channel,
                    already posted, script missing or too long).
    """
    from src.dashboard.media import output_file
    from src.storage.queries import get_video_by_id, set_video_path
    from src.video.render import render_video
    from src.video.tts import _extract_spoken_text

    payload = job["payload"]
    config = payload["config"]
    animated = payload.get("animated", True)

    video = get_video_by_id(video_id)
    if not video or video["channel"] != job["channel"]:
        raise ValueError("The video does not belong to this channel.")
    if video["status"] == "posted":
        raise ValueError("Create a new draft instead of changing a posted video.")
    script = output_file(video["script_path"], ".md")
    if script is None:
        raise ValueError("The source script file is missing.")
    # The voice allowance was worked out for this many characters.
    if len(_extract_spoken_text(str(script))) > max_spoken_chars(config):
        raise ValueError("The script is too long for this job's voice allowance. Shorten it before rendering.")

    tracker.attach_video(video_id)
    update_job(job["id"], "Generating voiceover and animated video", 65, video_id=video_id)

    video_settings = config.get("video", {})
    provider, voice = choose_voice(video_settings, payload.get("free_voice"))
    storyboard_llm = None
    if animated:
        storyboard_llm = llm

    path = render_video(str(script), video["story_title"], job["channel"], voice,
                        font_size=video_settings.get("font_size", 90), tts_provider=provider,
                        channel_config=config, llm=storyboard_llm, animated=animated,
                        cost_tracker=tracker)
    if output_file(path, ".mp4") is None:
        raise ValueError("Rendering did not produce a video file. Check that FFmpeg is installed.")

    set_video_path(video_id, path)
    mark_video_for_review(video_id)
    return path


def story_from_payload(job):
    """
    Get the story a "write a script" job is about.

    Args:
        job (dict): The job record. Its payload has either "story_id"
                    (a found story) or "url" (a pasted link).

    Returns:
        dict: "url", "title", "source" and "summary".
    """
    payload = job["payload"]
    if payload.get("story_id"):
        story = saved_story(job["channel"], payload["story_id"])
    else:
        url = payload.get("url", "").strip()
        parts = urlsplit(url)
        title = payload.get("title", "").strip()
        if not title:
            # Use the last part of the link: ".../city-opens-library" → "city opens library".
            title = parts.path.rstrip("/").split("/")[-1].replace("-", " ")
        if not title:
            title = parts.hostname
        story = {"url": url, "title": title, "source": parts.hostname or "Article", "summary": ""}

    # Use the cleaned address saved by enqueue_job (no "#section" part).
    if payload.get("source_url"):
        story["url"] = payload["source_url"]
    return story


def existing_draft_id(channel, url):
    """
    Find a video already made from the same article.

    Args:
        channel (str): The channel.
        url (str): The article address.

    Returns:
        int or None: The existing video's ID, or None.
    """
    connection = get_connection()
    try:
        row = connection.execute(
            "SELECT id FROM videos WHERE channel = ? AND story_url = ?", (channel, url)
        ).fetchone()
    finally:
        connection.close()
    if row is None:
        return None
    return row["id"]


def generate(job, llm, tracker):
    """
    Run a "write a script" job, and render it too if that was ticked.

    Args:
        job (dict): The job record.
        llm (LLMClient): Writes the script.
        tracker (CostTracker): Records spending.

    Returns:
        dict: "video_id" and a message, or (dry run) what was checked.

    Raises:
        ValueError: If the story already has a draft or lacks source text.
    """
    from src.generation.writer import write_script
    from src.storage.history import mark_processed
    from src.storage.queries import save_video

    payload = job["payload"]
    story = story_from_payload(job)

    existing = existing_draft_id(job["channel"], story["url"])
    if existing:
        raise ValueError(f"This story already has draft #{existing}. Open it in the library instead.")

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


# ---------------------------------------------------------------------------
# Running one job
# ---------------------------------------------------------------------------

def claim_job(job_id):
    """
    Mark a queued job as running, so it can only ever run once.

    Args:
        job_id (str): The job.

    Returns:
        bool: True if this process claimed it; False if it was not queued
              (already run, or marked interrupted).
    """
    connection = get_connection()
    try:
        changed = connection.execute(
            "UPDATE jobs SET status = 'running', heartbeat_at = ? WHERE id = ? AND status = 'queued'",
            (utc_now(), job_id),
        ).rowcount
        connection.commit()
    finally:
        connection.close()
    return changed == 1


def publish_saved_video(job):
    """
    Post an approved video to its channel's platforms (the "Post now" button).

    Args:
        job (dict): The job record; its payload holds "video_id".

    Returns:
        dict: The video ID and a message per platform.

    Raises:
        RuntimeError: If any platform did not succeed, so the job page
                      shows it as failed with the reason for each platform.
    """
    from src.publishing.scheduler import channel_platforms, publish_video_now

    video_id = job["payload"]["video_id"]
    config = job["payload"]["config"]
    if job["dry_run"]:
        from src.publishing.scheduler import video_platforms
        from src.storage.queries import get_video_by_id
        video = get_video_by_id(video_id)
        destinations = channel_platforms(config)
        if video:
            destinations = video_platforms(video, config)
        platforms = ", ".join(destinations)
        return {"video_id": video_id, "message": f"Dry run: would post to {platforms}. Nothing was sent."}

    messages, fully_posted = publish_video_now(job["channel"], config, video_id)
    summary = " ".join(messages)
    if not fully_posted:
        raise RuntimeError(summary)
    return {"video_id": video_id, "message": summary}


def recut_clip(job):
    """
    Save a clip's new moment/title from the review page and render it again.

    No AI call is made, so this job is free. The video was already put
    back to "drafted" when the job was created, so it must be watched
    and approved again.

    Args:
        job (dict): The job record; its payload holds "video_id",
                    "start", "end" and "hook_title".

    Returns:
        dict: The video ID and a message for the job page.
    """
    from src.clips.maker import rerender_clip, save_new_moment

    payload = job["payload"]
    video_id = payload["video_id"]
    if job["dry_run"]:
        return {"video_id": video_id, "message": "Dry run: nothing was changed."}
    update_job(job["id"], "Saving the new moment", 10, video_id=video_id)
    save_new_moment(video_id, payload["start"], payload["end"], payload["hook_title"], payload["config"])
    update_job(job["id"], "Rendering the clip", 30, video_id=video_id)
    rerender_clip(video_id, payload["config"])
    mark_video_for_review(video_id)
    return {"video_id": video_id, "message": "Clip re-cut. It is back in the review queue."}


def do_job_work(job, llm, tracker):
    """
    Do the work for a job's kind.

    Args:
        job (dict): The job record.
        llm (LLMClient): The AI client.
        tracker (CostTracker): Records spending.

    Returns:
        dict: The result to show on the job page.
    """
    kind = job["kind"]
    if kind == "discover":
        return discover(job, llm)
    if kind == "generate":
        return generate(job, llm, tracker)
    if kind == "render":
        if job["dry_run"]:
            return {"message": "Dry run: voice generation and rendering were skipped. No paid calls were made."}
        video_id = job["payload"]["video_id"]
        render_saved_video(job, video_id, llm, tracker)
        return {"video_id": video_id, "message": "Rendered video is ready for review."}
    if kind == "publish":
        return publish_saved_video(job)
    if kind == "clip_render":
        return recut_clip(job)
    if kind == "clip_pipeline":
        from src.clips.automation import run_pipeline
        update_job(job["id"], "Monitoring sources and preparing highlights", 15)
        return run_pipeline(job["channel"], job["payload"]["config"], llm,
                            bool(job["dry_run"]), job["payload"].get("recording"))

    from src.analytics.accounts import sync_channel
    return {"message": sync_channel(job["channel"], dry_run=bool(job["dry_run"]))}


def run_job(job_id):
    """
    Run a saved job once and record its result or a clear error.

    Args:
        job_id (str): The job.

    Returns:
        Nothing.
    """
    if not claim_job(job_id):
        return
    job = job_record(job_id)
    config = job["payload"]["config"]

    tracker = CostTracker(job["channel"], job_id=job_id, budget=job["budget_usd"])
    cost_tracker = tracker
    if job["dry_run"]:
        cost_tracker = None
    max_calls = config.get("llm", {}).get("max_calls_per_run", 5)
    llm = LLMClient(dry_run=bool(job["dry_run"]), max_calls=max_calls, cost_tracker=cost_tracker)

    try:
        result = do_job_work(job, llm, tracker)
        update_job(job_id, "Complete", 100, status="succeeded", result=result)
    except Exception as error:
        # Any failure becomes a message on the job page. safe_error removes
        # web addresses and secrets that an error from a provider may contain.
        from src.dashboard.accounts import safe_error
        progress = job_record(job_id)["progress"]
        update_job(job_id, "Stopped", progress, status="failed", error=safe_error(error))
    finally:
        llm.write_cost_log(job["channel"], note=f"dashboard job {job_id}")


def time_limit_reached(signum, frame):
    """
    Stop a job that has run too long (called by the operating system alarm).

    Args:
        signum: The signal number (unused).
        frame: Where the program was (unused).

    Raises:
        TimeoutError: Always; run_job() turns it into a failed job.
    """
    raise TimeoutError("This job exceeded the configured time limit. Any saved draft remains in the library.")


def main():
    """
    Run the job whose ID was given on the command line.

    Also starts the heartbeat thread and an alarm that stops the job
    after `max_job_minutes`.
    """
    load_dotenv()
    create_tables()
    job_id = sys.argv[1]

    finished = threading.Event()
    threading.Thread(target=heartbeat, args=(job_id, finished), daemon=True).start()

    # SIGALRM is an alarm clock from the operating system (macOS and
    # Linux). When it rings, time_limit_reached() runs and stops the job,
    # even if it is stuck waiting on the network or FFmpeg.
    signal.signal(signal.SIGALRM, time_limit_reached)
    signal.alarm(job_settings().max_job_minutes * 60)
    try:
        run_job(job_id)
    finally:
        finished.set()
        signal.alarm(0)


if __name__ == "__main__":
    main()

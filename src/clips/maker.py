# =============================================================
# src/clips/maker.py — from downloaded clip to a video waiting for
#                      review (steps L.4, L.5 and L.6)
#
# For each downloaded clip:
#   1. Transcribe it locally with Whisper (free, cached).
#   2. Let the cheap AI pick the best moment and a hook title
#      (moments.py, ~$0.002, cost logged against the video).
#   3. Render the vertical video (src/video/clip_render.py).
#   4. Write a notes file next to the other scripts: credit,
#      permission, chosen moment, the ready-to-paste post caption
#      and the transcript. The dashboard shows it on the video page.
#   5. Save a normal `videos` row with status "drafted", so the clip
#      appears in the existing review queue (CLI and dashboard).
#
# rerender_clip() is used by the dashboard's "change the moment or
# title" form; it renders again without any AI call (free).
# =============================================================

import os
import json
import subprocess
from datetime import datetime, timezone

from src.clips.moments import clean_hook_title, pick_moment
from src.clips.settings import StreamerNotApproved, find_approved_streamer, load_clip_settings
from src.clips.store import clips_with_status, get_clip, get_clip_for_video, update_clip
from src.clips.transcribe import TranscriptionError, transcribe_clip
from src.generation.llm import CallLimitReached, LLMError
from src.storage.database import get_connection
from src.storage.finance import CostTracker
from src.storage.queries import get_video_by_id, set_video_path

# Shortest moment the review form accepts, in seconds.
MIN_RETRIM_SECONDS = 3


def build_post_caption(hook_title, clip, hashtags):
    """
    Write the post caption: hook title, streamer credit with link, hashtags.

    TikTok drafts can't receive a caption through the API, so this text
    is shown on the video page for you to paste in the TikTok app
    (Instagram/YouTube uploads use it directly).

    Args:
        hook_title (str): The video's hook title.
        clip (dict): The clip row ("credit_text", e.g. "kick.com/streamer").
        hashtags (list[str]): From the channel YAML's publishing section.

    Returns:
        str: The caption.

    Example:
        build_post_caption("He did NOT expect that", clip, ["#twitch"])
        # → "He did NOT expect that\\n\\n🎥 Clip: twitch.tv/streamer — follow them at
        #    https://twitch.tv/streamer\\n\\n#twitch"
    """
    credit = clip["credit_text"]
    credit_line = f"🎥 Clip: {credit} — follow them at https://{credit}"
    caption = f"{hook_title}\n\n{credit_line}"
    if hashtags:
        caption += "\n\n" + " ".join(hashtags)
    return caption[:2200]


def output_paths(channel_key, clip):
    """
    Decide where a clip's finished video and notes file are saved.

    Args:
        channel_key (str): The channel.
        clip (dict): The clip row.

    Returns:
        tuple: (video path, notes path), e.g.
               ("output/videos/twitch_clips/2026-11-17-streamer-clip12.mp4",
                "output/twitch_clips/2026-11-17-streamer-clip12.md")
    """
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    safe_streamer = ""
    for character in clip["streamer"].lower():
        if character.isalnum():
            safe_streamer += character
    name = f"{today}-{safe_streamer[:30] or 'creator'}-clip{clip['id']}"
    video_path = os.path.join("output", "videos", channel_key, name + ".mp4")
    notes_path = os.path.join("output", channel_key, name + ".md")
    return video_path, notes_path


def permission_line(clip, settings):
    """
    Describe why we may use this clip (for the notes file).

    Args:
        clip (dict): The clip row.
        settings (ClipChannelSettings): The channel's clip settings.

    Returns:
        str: For example "Said yes in a Twitch DM (2026-11-17)".
    """
    streamer = find_approved_streamer(settings, clip["streamer"], platform=clip["platform"])
    line = f"{streamer.permission_note} ({streamer.permission_date.isoformat()})"
    if clip["ownership_note"]:
        line += f" · {clip['ownership_note']}"
    return line


def moment_transcript(words, start, end):
    """
    Get the words spoken inside the chosen moment, as plain text.

    Args:
        words (list[dict]): The clip's timed words.
        start (float): Moment start, in seconds.
        end (float): Moment end, in seconds.

    Returns:
        str: The spoken words, or "(no speech)".
    """
    spoken = []
    for word in words:
        if word["end"] > start and word["start"] < end:
            spoken.append(word["word"])
    if not spoken:
        return "(no speech)"
    return " ".join(spoken)


def write_notes(notes_path, clip, moment, words, caption, settings):
    """
    Save the clip's review notes as a Markdown file.

    Args:
        notes_path (str): Where to save the file.
        clip (dict): The clip row.
        moment (dict): {"start", "end", "hook_title", "reason"}.
        words (list[dict]): The clip's timed words.
        caption (str): The post caption.
        settings (ClipChannelSettings): The channel's clip settings.
    """
    lines = [
        f"# {moment['hook_title']}",
        "",
        f"- Credit: {clip['credit_text']}",
        f"- Original clip: {clip['clip_url'] or '(uploaded file)'}",
        f"- Clip title: {clip['title'] or '(none)'} · {clip['view_count']:,} views",
        f"- Permission: {permission_line(clip, settings)}",
        f"- Moment used: {moment['start']:.1f}s to {moment['end']:.1f}s "
        f"of {clip['duration_seconds']:.1f}s ({moment['end'] - moment['start']:.1f}s long)",
        f"- Why this moment: {moment.get('reason') or '(not given)'}",
        "",
        "## Post caption (paste this into TikTok)",
        "",
        caption,
        "",
        "## What is said in this moment",
        "",
        moment_transcript(words, moment["start"], moment["end"]),
        "",
    ]
    os.makedirs(os.path.dirname(notes_path), exist_ok=True)
    with open(notes_path, "w", encoding="utf-8") as file:
        file.write("\n".join(lines))


def facecam_for(clip, settings):
    """
    Get the webcam position to use for a clip, checking permission again.

    A streamer removed from the approved list must not be rendered,
    even if their clip was downloaded earlier.

    Args:
        clip (dict): The clip row.
        settings (ClipChannelSettings): The channel's clip settings.

    Returns:
        FacecamBox or None: The webcam position (None keeps the entire source visible).

    Raises:
        StreamerNotApproved: If the clip's streamer is no longer approved.
    """
    return find_approved_streamer(settings, clip["streamer"], platform=clip["platform"]).facecam


def words_for(clip, settings):
    """
    Get the clip's timed words (Whisper runs only the first time).

    Args:
        clip (dict): The clip row.
        settings (ClipChannelSettings): The transcription settings.

    Returns:
        list[dict]: The timed words.
    """
    if clip.get("recording_id"):
        from src.clips.recordings import recording_row, recording_words
        return recording_words(recording_row(clip["recording_id"]), settings)
    cache_name = f"{clip['source']}_{clip['id']}"
    return transcribe_clip(clip["file_path"], cache_name, settings.transcription.whisper_model,
                           settings.transcription.language)


def render_and_save(config, settings, clip, moment, words, video_path, notes_path):
    """
    Render a clip's video and write its notes file.

    Args:
        config (dict): The channel's YAML settings.
        settings (ClipChannelSettings): The clip settings.
        clip (dict): The clip row.
        moment (dict): {"start", "end", "hook_title", "reason"}.
        words (list[dict]): The clip's timed words.
        video_path (str): Where to save the MP4.
        notes_path (str): Where to save the notes file.
    """
    # Imported here so finding clips never loads the video code.
    from src.video.clip_render import render_clip_video

    facecam = facecam_for(clip, settings)
    temporary_path = os.path.splitext(video_path)[0] + ".rendering.mp4"
    from src.clips.editing import remove_long_silence
    edited_source = os.path.splitext(video_path)[0] + ".edited.mp4"
    os.makedirs(os.path.dirname(video_path), exist_ok=True)
    editing_note = ""
    try:
        source, start, end, captions, cleanup = remove_long_silence(
            clip["file_path"], moment["start"], moment["end"], words, edited_source, config.get("editing", {}))
    except (ValueError, OSError, subprocess.CalledProcessError):
        # Silence removal is optional; keep the original audio and timings if it fails.
        source, start, end, captions, cleanup = clip["file_path"], moment["start"], moment["end"], words, None
        editing_note = "Optional silence trimming failed; original audio and timing preserved."
    try:
        render_clip_video(source, start, end, captions,
                          moment["hook_title"], clip["credit_text"], facecam, settings, temporary_path)
    finally:
        if cleanup and os.path.exists(cleanup):
            os.remove(cleanup)
    from src.clips.quality import check_export
    report = check_export(temporary_path, end - start)
    if editing_note:
        report["editing_note"] = editing_note
    update_clip(clip["id"], quality_json=json.dumps(report))
    if not report["passed"]:
        raise ValueError("Clip failed quality checks: " + " ".join(report["errors"]))
    os.replace(temporary_path, video_path)
    hashtags = config.get("publishing", {}).get("hashtags", [])
    from src.clips.metadata import publishing_caption
    caption = publishing_caption(clip, moment["hook_title"], hashtags)
    write_notes(notes_path, clip, moment, words, caption, settings)


def saved_moment(clip):
    """
    Read the moment already chosen for a clip from its database row.

    Args:
        clip (dict): A clip row with status "picked" or "rendered".

    Returns:
        dict: {"start", "end", "hook_title", "reason"}.
    """
    return {"start": clip["start_seconds"], "end": clip["end_seconds"],
            "hook_title": clip["hook_title"], "reason": clip["pick_reason"]}


def make_clip_video(channel_key, config, settings, clip, llm, dry_run=False):
    """
    Turn one downloaded (or already picked) clip into a drafted video.

    Args:
        channel_key (str): The channel.
        config (dict): The channel's YAML settings.
        settings (ClipChannelSettings): The clip settings.
        clip (dict): The clip row.
        llm (LLMClient): The run's AI client.
        dry_run (bool): True = pick (no paid call) and print, save nothing.

    Returns:
        int or None: The new video's ID, or None in a dry run.
    """
    facecam_for(clip, settings)
    words = words_for(clip, settings)

    tracker = None
    if clip["status"] == "downloaded":
        # A fresh cost tracker per clip, so this clip's AI cost is
        # linked to this clip's video (not to the first video of the run).
        if not dry_run:
            tracker = CostTracker(channel_key)
            llm.cost_tracker = tracker
        moment = pick_moment(clip, words, config, settings, llm)
        if dry_run:
            print(f"    would use {moment['start']:.1f}s-{moment['end']:.1f}s · "
                  f"title: \"{moment['hook_title']}\"")
            return None
        update_clip(clip["id"], status="picked", start_seconds=moment["start"],
                    end_seconds=moment["end"], hook_title=moment["hook_title"],
                    pick_reason=moment["reason"], last_error="")
        clip = get_clip(clip["id"])
    elif dry_run:
        print(f"    already picked; would render {clip['start_seconds']:.1f}s-{clip['end_seconds']:.1f}s")
        return None

    moment = saved_moment(clip)
    video_path, notes_path = output_paths(channel_key, clip)
    render_and_save(config, settings, clip, moment, words, video_path, notes_path)

    # Save the draft and its clip link together: a crash cannot leave an
    # unlinked draft that would be created a second time on recovery.
    from src.storage.finance import utc_now
    connection = get_connection()
    try:
        connection.execute("BEGIN IMMEDIATE")
        existing = connection.execute("SELECT video_id FROM clips WHERE id=?", (clip["id"],)).fetchone()
        if existing["video_id"]:
            return existing["video_id"]
        cursor = connection.execute("""
            INSERT INTO videos (channel, story_title, story_url, script_path, video_path, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (channel_key, moment["hook_title"], clip["clip_url"] or clip["credit_text"],
              notes_path, video_path, utc_now()))
        video_id = cursor.lastrowid
        connection.execute("UPDATE clips SET status='rendered', video_id=? WHERE id=?", (video_id, clip["id"]))
        connection.commit()
    finally:
        connection.close()
    if tracker is not None:
        tracker.attach_video(video_id)
    return video_id


def make_videos(channel_key, config, llm, only_streamer=None, dry_run=False, limit=None):
    """
    Run steps L.4-L.6 for every clip that is downloaded or picked.

    One clip's problem (no Whisper, an AI error, a removed streamer)
    is recorded on that clip and the others continue.

    Args:
        channel_key (str): The channel.
        config (dict): The channel's YAML settings.
        llm (LLMClient): The run's AI client.
        only_streamer (str or None): Just this streamer's clips.
        dry_run (bool): True = no paid calls, nothing saved.
        limit (int or None): Make at most this many videos.

    Returns:
        list[int]: The IDs of the new drafted videos.
    """
    settings = load_clip_settings(config)
    if only_streamer:
        find_approved_streamer(settings, only_streamer, channel_key)

    waiting = clips_with_status(channel_key, "picked", only_streamer)
    waiting += clips_with_status(channel_key, "downloaded", only_streamer)
    if limit is not None:
        waiting = waiting[:limit]

    video_ids = []
    for clip in waiting:
        print(f"  clip {clip['id']} · {clip['streamer']} · {clip['title'][:50]}")
        try:
            video_id = make_clip_video(channel_key, config, settings, clip, llm, dry_run)
        except CallLimitReached:
            print("    STOPPED: this run reached its AI call limit (llm.max_calls_per_run).")
            break
        except (LLMError, TranscriptionError, StreamerNotApproved, RuntimeError, ValueError) as error:
            print(f"    NOT MADE: {error}")
            if not dry_run:
                update_clip(clip["id"], last_error=str(error)[:500])
            continue
        if video_id is not None:
            print(f"    drafted video {video_id} — waiting for your review")
            video_ids.append(video_id)
    return video_ids


def check_new_moment(clip, start, end, hook_title, settings):
    """
    Check a moment and title typed into the review form.

    Args:
        clip (dict): The clip row.
        start (float): New start, in seconds from the clip's beginning.
        end (float): New end.
        hook_title (str): New hook title.
        settings (ClipChannelSettings): The clip settings.

    Returns:
        dict: The cleaned {"start", "end", "hook_title"}.

    Raises:
        ValueError: With a message for the page if something is not allowed.
    """
    if start < 0 or end > clip["duration_seconds"] + 0.05:
        raise ValueError(f"Choose times between 0 and {clip['duration_seconds']:.1f} seconds.")
    length = end - start
    if length < MIN_RETRIM_SECONDS:
        raise ValueError(f"The moment must be at least {MIN_RETRIM_SECONDS} seconds long.")
    if length > settings.clips.moment_max_seconds:
        raise ValueError(f"The moment may be at most {settings.clips.moment_max_seconds:g} seconds long.")
    title = clean_hook_title(hook_title, settings.hook_title.max_words)
    if not title:
        raise ValueError("Write a hook title.")
    return {"start": round(start, 2), "end": round(min(end, clip["duration_seconds"]), 2),
            "hook_title": title}


def save_new_moment(video_id, start, end, hook_title, config):
    """
    Save a moment/title change from the review form (before re-rendering).

    Args:
        video_id (int): The clip's drafted video.
        start (float): New start in seconds.
        end (float): New end in seconds.
        hook_title (str): New hook title.
        config (dict): The channel's YAML settings.

    Raises:
        ValueError: If the video is not a clip or the values are not allowed.
    """
    clip = get_clip_for_video(video_id)
    if clip is None:
        raise ValueError("This video was not made from a clip.")
    settings = load_clip_settings(config)
    moment = check_new_moment(clip, start, end, hook_title, settings)
    update_clip(clip["id"], start_seconds=moment["start"], end_seconds=moment["end"],
                hook_title=moment["hook_title"], pick_reason="Changed by hand in review.")
    connection = get_connection()
    try:
        connection.execute("UPDATE videos SET story_title = ? WHERE id = ?",
                           (moment["hook_title"], video_id))
        connection.commit()
    finally:
        connection.close()


def rerender_clip(video_id, config):
    """
    Render a clip's video again with its saved moment and title (free).

    Used by the dashboard after the review form changes the moment.
    The video keeps its ID, and its MP4 and notes file are overwritten
    in place (same file names), so no old copies are left behind.

    Args:
        video_id (int): The clip's video.
        config (dict): The channel's YAML settings.

    Returns:
        str: The new video path.

    Raises:
        ValueError: If the video is not a clip.
    """
    clip = get_clip_for_video(video_id)
    if clip is None:
        raise ValueError("This video was not made from a clip.")
    settings = load_clip_settings(config)
    words = words_for(clip, settings)

    video = get_video_by_id(video_id)
    new_video_path, new_notes_path = output_paths(clip["channel"], clip)
    video_path = video["video_path"] or new_video_path
    notes_path = video["script_path"] or new_notes_path
    render_and_save(config, settings, clip, saved_moment(clip), words, video_path, notes_path)
    set_video_path(video_id, video_path)
    return video_path

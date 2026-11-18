# =============================================================
# Small launch-day fallbacks for the existing clips pipeline.
# Exact timestamp cuts reuse local Whisper, rendering and review.
# Export bundles support official manual uploads without API approval.
# Neither operation connects accounts, approves videos or uploads.
# =============================================================

import json
import math
import shutil
from pathlib import Path

from src.clips import maker, metadata, quality, recordings, store
from src.clips.finder import file_fingerprint
from src.clips.moments import clean_hook_title
from src.clips.settings import credit_for, find_approved_streamer, load_clip_settings
from src.publishing.youtube import build_title
from src.storage.database import get_connection
from src.storage.queries import get_video_by_id


def validate_interval(start, end, maximum=60):
    """
    Check proposed launch timestamps before reading footage or starting transcription.

    Args:
        start: Start time in seconds from the original recording.
        end: End time in seconds from the original recording.
        maximum: The channel's longest allowed clip, capped at 60 seconds for launch.

    Returns:
        Nothing; raises ValueError for invalid times or clips outside 15–60 seconds.
    """
    if not math.isfinite(start) or not math.isfinite(end) or start < 0:
        raise ValueError("Start and end must be finite seconds, with start at least zero.")
    if not 15 <= end - start <= min(60, maximum):
        raise ValueError(f"Choose a clip lasting 15–{min(60, maximum):g} seconds.")


def cut_recording(channel, config, path, streamer, platform, start, end, title, confirmed=False):
    """
    Render one authorized timestamp selection using the existing pipeline and review queue.

    Args:
        channel: The clips channel configuration name.
        config: That channel's YAML settings.
        path: The completed original recording's local file path.
        streamer: The approved creator's login name.
        platform: The creator's source site, twitch or kick.
        start: Start time in seconds from the recording.
        end: End time in seconds from the recording.
        title: A truthful hook to show in the video and upload text.
        confirmed: True when the caller confirms permission to repurpose this file.

    Returns:
        The existing or newly rendered draft video's ID. No paid calls or uploads occur.
    """
    settings = load_clip_settings(config)
    if not config.get("enabled", True):
        raise ValueError("Channel is disabled.")
    approved = find_approved_streamer(settings, streamer, channel, platform)
    start = round(start, 2)
    end = round(end, 2)
    validate_interval(start, end, settings.clips.moment_max_seconds)
    hook = clean_hook_title(title, settings.hook_title.max_words)
    if not hook:
        raise ValueError("Supply a truthful hook title with --title.")
    recording_id = recordings.import_recording(channel, config, path, streamer, platform, confirmed)
    recording = recordings.recording_row(recording_id)
    if end > recording["duration"]:
        raise ValueError(f"End must be within the {recording['duration']:.2f}-second recording.")
    # The same identity as automatic selection prevents a second draft for an identical cut.
    identity = f"recording:{recording['fingerprint']}:{start:.2f}:{end:.2f}"
    connection = get_connection()
    try:
        existing = connection.execute(
            "SELECT * FROM clips WHERE source='upload' AND source_clip_id=?", (identity,)
        ).fetchone()
        if existing is not None and existing["video_id"]:
            if existing["start_seconds"] != start or existing["end_seconds"] != end:
                raise ValueError("That draft was re-cut in review; inspect it before making another selection.")
            draft = get_video_by_id(existing["video_id"])
            if draft is None or not Path(draft["video_path"] or "").is_file():
                raise ValueError("The existing draft's video is missing; re-render it from its review page.")
            return existing["video_id"]
        overlap = connection.execute("""
            SELECT id FROM clips WHERE recording_id=? AND source_clip_id<>?
                AND status IN ('picked','rendered') AND start_seconds < ? AND end_seconds > ?
        """, (recording_id, identity, end, start)).fetchone()
        if overlap is not None:
            raise ValueError(f"This overlaps clip {overlap['id']}; choose a distinct moment or re-cut that clip.")
    finally:
        connection.close()
    words = recordings.recording_words(recording, settings)
    excerpt = maker.moment_transcript(words, start, end)
    if excerpt == "(no speech)":
        raise ValueError("No speech was transcribed inside this selection; choose another moment.")
    if recordings.repeated_transcript(channel, approved, excerpt):
        raise ValueError("This speech nearly duplicates an existing clip; choose a distinct moment.")
    clip_id = store.save_found_clip(channel, {
        "source": "upload", "source_clip_id": identity, "streamer": approved.username,
        "platform": platform, "credit_text": credit_for(approved), "title": hook,
        "duration_seconds": recording["duration"],
        "ownership_note": "Authorized timestamp selection; SHA-256 " + recording["fingerprint"],
    })
    if clip_id is None:
        connection = get_connection()
        try:
            clip_id = connection.execute(
                "SELECT id FROM clips WHERE source='upload' AND source_clip_id=?", (identity,)
            ).fetchone()["id"]
        finally:
            connection.close()
    store.update_clip(clip_id, recording_id=recording_id, file_path=recording["file_path"],
                      status="picked", start_seconds=start, end_seconds=end, hook_title=hook,
                      pick_reason="Exact timestamps selected for human review; no AI quality score implied.")
    metadata.create_metadata(store.get_clip(clip_id), words, config, settings)
    return maker.make_clip_video(channel, config, settings, store.get_clip(clip_id), None)


def export_item(channel, config, settings, video_id, platforms):
    """
    Validate one rendered clip and collect safe, ready-to-paste upload text.

    Args:
        channel: The expected clips channel, preventing accidental news exports.
        config: The channel's current YAML settings.
        settings: Validated current permission and editing settings.
        video_id: The local draft or approved video ID to export.
        platforms: The intended manual destinations, youtube and/or tiktok.

    Returns:
        A dictionary with source path, upload text, review status, hash and fresh quality checks.
    """
    video = get_video_by_id(video_id)
    clip = store.get_clip_for_video(video_id)
    if video is None or clip is None or video["channel"] != channel:
        raise ValueError(f"Video {video_id} is not a rendered clip in {channel}.")
    find_approved_streamer(settings, clip["streamer"], channel, clip["platform"])
    if video["status"] not in ("drafted", "approved") or clip["status"] != "rendered":
        raise ValueError(f"Video {video_id} is not a current reviewable export.")
    connection = get_connection()
    try:
        active = connection.execute(
            "SELECT 1 FROM jobs WHERE video_id=? AND status IN ('queued','running')", (video_id,)
        ).fetchone()
        deliveries = connection.execute("""
            SELECT platform FROM publishing_deliveries WHERE video_id=?
                AND status IN ('sending','uncertain','succeeded')
        """, (video_id,)).fetchall()
        posts = connection.execute("SELECT platform FROM platform_posts WHERE video_id=?", (video_id,)).fetchall()
    finally:
        connection.close()
    blocked = bool(active)
    for previous in list(deliveries) + list(posts):
        if previous["platform"] in platforms:
            blocked = True
    if blocked:
        raise ValueError(f"Video {video_id} has active work or upload history; reconcile it before manual uploading.")
    path = Path(video["video_path"] or "").resolve()
    if not path.is_file() or not path.is_relative_to(Path("output").resolve()):
        raise ValueError(f"Video {video_id} needs an existing rendered file inside output/.")
    saved_quality = json.loads(clip["quality_json"] or "{}")
    expected = saved_quality.get("duration", clip["end_seconds"] - clip["start_seconds"])
    validate_interval(0, expected)
    report = quality.check_export(path, expected)
    if not report["passed"] or report["warnings"]:
        raise ValueError(f"Video {video_id} needs media correction/review: "
                         + " ".join(report["errors"] + report["warnings"]))
    title = build_title(video["story_title"])
    caption = metadata.publishing_caption(clip, title, config.get("publishing", {}).get("hashtags", []))
    description = caption + "\n\n#Shorts"
    return {"video_id": video_id, "source_path": str(path), "title": title,
            "youtube_description": description[:5000], "tiktok_caption": caption,
            "creator": clip["credit_text"], "review_status": video["status"],
            "sha256": file_fingerprint(str(path)), "quality": report,
            "source_start": clip["start_seconds"], "source_end": clip["end_seconds"],
            "recording_id": clip["recording_id"], "source_clip_id": clip["source_clip_id"],
            "notes_path": video["script_path"]}


def export_bundle(channel, config, video_ids, destination, dry_run=False, platforms=None):
    """
    Package distinct checked clips for official Studio uploads, without publishing or approving them.

    Args:
        channel: The clips channel configuration name.
        config: The current channel YAML, including approved creators and hashtags.
        video_ids: The exact video IDs to include; duplicate IDs are refused.
        destination: A new folder inside output/ for MP4s, notes and metadata.
        dry_run: True to validate inputs and list IDs without media checks or file writes.
        platforms: Optional list of manual destinations; defaults to YouTube and TikTok.
            Choose only TikTok when YouTube has already received the video, for example.

    Returns:
        A manifest dictionary. Its upload/publication counts remain zero because exporting is local.
    """
    if not config.get("enabled", True):
        raise ValueError("Channel is disabled.")
    settings = load_clip_settings(config)
    if platforms is None:
        platforms = ["youtube", "tiktok"]
    if not platforms or not set(platforms).issubset({"youtube", "tiktok"}):
        raise ValueError("Choose youtube, tiktok, or both as manual destinations.")
    if not video_ids or len(set(video_ids)) != len(video_ids):
        raise ValueError("Supply distinct video IDs to export.")
    folder = Path(destination).resolve()
    output_root = Path("output").resolve()
    if folder == output_root or not folder.is_relative_to(output_root) or folder.exists():
        raise ValueError("Choose a new export folder inside output/; existing folders are never overwritten.")
    if dry_run:
        return {"dry_run": True, "video_ids": video_ids, "destination": str(folder), "platforms": platforms,
                "message": "Would validate and package these clips. No files, media checks or uploads performed."}
    items = []
    hashes = set()
    for video_id in video_ids:
        item = export_item(channel, config, settings, video_id, platforms)
        if item["sha256"] in hashes:
            raise ValueError("Two selected videos contain identical bytes; export distinct clips.")
        for previous in items:
            same_source = (item["recording_id"] and item["recording_id"] == previous["recording_id"])
            same_source = same_source or item["source_clip_id"] == previous["source_clip_id"]
            if same_source and item["source_start"] < previous["source_end"] and item["source_end"] > previous["source_start"]:
                raise ValueError("Selected clips overlap the same source; export distinct moments.")
        hashes.add(item["sha256"])
        items.append(item)
    # All media must pass before creating the folder, so a failed gate leaves no misleading bundle.
    folder.mkdir(parents=True)
    for item in items:
        name = f"clip-{item['video_id']}"
        shutil.copyfile(item["source_path"], folder / (name + ".mp4"))
        item["file"] = name + ".mp4"
        text = (f"Manual destinations: {', '.join(platforms)}\n\n"
                f"YouTube title:\n{item['title']}\n\nYouTube description:\n{item['youtube_description']}"
                f"\n\nTikTok caption:\n{item['tiktok_caption']}\n\nReview status: {item['review_status']}\n")
        (folder / (name + ".txt")).write_text(text, encoding="utf-8")
        if item["notes_path"] and Path(item["notes_path"]).is_file():
            shutil.copyfile(item["notes_path"], folder / (name + "-review.md"))
    manifest = {"channel": channel, "platforms": platforms, "clips": items,
                "youtube_uploads": 0, "tiktok_uploads": 0,
                "public_urls": [], "note": "Local exports only. Review actual clips before publishing."}
    (folder / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    instructions = (
        "# Manual publication\n\nReview every MP4 and its text before publishing. Drafts require approval.\n"
        f"Publish this bundle only to: {', '.join(platforms)}. Skip other destinations.\n\n"
        "1. YouTube: https://studio.youtube.com/ → Create → Upload videos. Use each clip's text file, "
        "answer the audience question truthfully, finish Checks and choose Public → Publish.\n"
        "2. TikTok: https://www.tiktok.com/tiktokstudio → Upload. Paste the clip's caption, "
        "check the preview, account and privacy, then Post.\n"
        "3. Verify each public URL plays and record platform confirmation/URLs in TODAY_LAUNCH_CHECKLIST.md. "
        "Do not count this export or an inbox notification as publication.\n"
    )
    (folder / "UPLOAD_INSTRUCTIONS.md").write_text(instructions, encoding="utf-8")
    return manifest

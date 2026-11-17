# =============================================================
# src/clips/finder.py — find new clips and download them (step L.3)
#
# For each approved streamer:
#   1. Ask Twitch for their most-viewed clips of the last 24 hours
#      (official Get Clips endpoint, app token).
#   2. Skip clips already in the database (never process twice)
#      and clips that are too short or have too few views.
#   3. Save the new ones with status "found".
#   4. Download each file through Twitch's official clip-download
#      endpoint (needs the streamer's one-time login) into
#      cache/clips/twitch/, then mark it "downloaded".
#
# --dry-run lists what would be downloaded and saves nothing.
#
# Kick has no official API for clips, so Kick streamers are never
# fetched. Instead the streamer sends you the clip file and you add
# it with add_uploaded_video() (the "upload" command, which needs
# --i-own-this). The streamer must still be on the approved list.
# =============================================================

import hashlib
import os
import re
import shutil

from src.clips.settings import (
    approved_streamers_to_use,
    credit_for,
    find_approved_streamer,
    load_clip_settings,
)
from src.clips.store import clips_with_status, is_clip_saved, save_found_clip, update_clip
from src.sources import twitch

CLIP_CACHE_FOLDER = os.path.join("cache", "clips")

# Video file types accepted by the upload command.
UPLOAD_EXTENSIONS = (".mp4", ".mov", ".mkv", ".webm")


def clip_file_path(source, clip_id, extension=".mp4"):
    """
    Decide where a clip's video file is kept.

    Args:
        source (str): "twitch" or "upload".
        clip_id (str): The clip's ID (anything unsafe in a file name is replaced).
        extension (str): The file ending, e.g. ".mp4".

    Returns:
        str: For example "cache/clips/twitch/FunnyClip-abc123.mp4".
    """
    safe_id = re.sub(r"[^A-Za-z0-9_-]", "_", clip_id)[:120]
    return os.path.join(CLIP_CACHE_FOLDER, source, safe_id + extension)


def clip_details(clip, streamer):
    """
    Pick out the parts of a Twitch clip we save in the database.

    Args:
        clip (dict): One clip object from twitch.fetch_top_clips().
        streamer (ApprovedStreamer): The approved streamer.

    Returns:
        dict: Ready for store.save_found_clip().
    """
    return {
        "source": "twitch",
        "source_clip_id": clip["id"],
        "streamer": streamer.username,
        "platform": "twitch",
        "credit_text": credit_for(streamer),
        "clip_url": clip.get("url", ""),
        "title": clip.get("title", ""),
        "view_count": clip.get("view_count", 0),
        "clip_created_at": clip.get("created_at", ""),
        "duration_seconds": clip.get("duration", 0),
    }


def reason_to_skip(clip, limits):
    """
    Decide whether a clip is too short or too unpopular to use.

    Args:
        clip (dict): A Twitch clip object.
        limits (ClipLimits): The channel's clip settings.

    Returns:
        str: Why it is skipped, or "" if the clip is fine.
    """
    if clip.get("duration", 0) < limits.skip_shorter_than_seconds:
        return f"shorter than {limits.skip_shorter_than_seconds:g}s"
    if clip.get("view_count", 0) < limits.min_views:
        return f"fewer than {limits.min_views} views"
    return ""


def find_new_clips(channel_key, streamer, limits, app_token, dry_run):
    """
    Look up one streamer's top clips and save the ones we have not seen.

    Args:
        channel_key (str): The channel, e.g. "twitch_clips".
        streamer (ApprovedStreamer): An approved streamer.
        limits (ClipLimits): The channel's clip settings.
        app_token (str): A Twitch app token.
        dry_run (bool): True = only report, save nothing.

    Returns:
        dict: Counts: {"new": n, "already_saved": n, "skipped": n}.
    """
    user = twitch.get_user(streamer.username, app_token)
    clips = twitch.fetch_top_clips(user["id"], limits.hours_back, limits.clips_per_streamer, app_token)

    counts = {"new": 0, "already_saved": 0, "skipped": 0}
    for clip in clips:
        title = clip.get("title", "")[:50]
        if is_clip_saved("twitch", clip["id"]):
            counts["already_saved"] += 1
            continue

        skip_reason = reason_to_skip(clip, limits)
        if skip_reason:
            counts["skipped"] += 1
            print(f"    skip  '{title}' ({skip_reason})")
            continue

        counts["new"] += 1
        views = clip.get("view_count", 0)
        if dry_run:
            print(f"    would download  '{title}' · {views:,} views · {clip.get('duration', 0):.0f}s")
            continue
        save_found_clip(channel_key, clip_details(clip, streamer))
        print(f"    found  '{title}' · {views:,} views")
    return counts


def download_found_clips(channel_key, streamer_login):
    """
    Download every "found" clip of one streamer and mark it "downloaded".

    Clips that failed in an earlier run are still "found", so they are
    simply tried again. A file already in the cache is not downloaded again.

    Args:
        channel_key (str): The channel.
        streamer_login (str): The approved streamer's login.

    Returns:
        int: How many clips are now downloaded.
    """
    # Imported here so finding clips in a dry run never needs the login code.
    from src.clips.twitch_login import user_token_for

    waiting = clips_with_status(channel_key, "found", streamer=streamer_login)
    if not waiting:
        return 0

    editor_id, user_token = user_token_for(streamer_login, channel_key)
    downloaded = 0
    # Twitch gives links for at most 10 clips per request.
    for start in range(0, len(waiting), twitch.MAX_CLIPS_PER_DOWNLOAD_REQUEST):
        batch = waiting[start:start + twitch.MAX_CLIPS_PER_DOWNLOAD_REQUEST]
        clip_ids = []
        for row in batch:
            clip_ids.append(row["source_clip_id"])
        # The streamer logged in themselves, so their ID is both the
        # broadcaster and the "editor" asking for the download.
        links = twitch.get_download_links(editor_id, editor_id, clip_ids, user_token)

        for row in batch:
            path = clip_file_path("twitch", row["source_clip_id"])
            link = links.get(row["source_clip_id"])
            if not os.path.exists(path):
                if link is None:
                    update_clip(row["id"], last_error="Twitch gave no download link for this clip.")
                    continue
                twitch.download_file(link, path)
            update_clip(row["id"], status="downloaded", file_path=path, last_error="")
            downloaded += 1
    return downloaded


def find_and_download(channel_key, config, only_streamer=None, dry_run=False):
    """
    Run step L.3 for a channel: find new top clips and download them.

    Args:
        channel_key (str): The channel, e.g. "twitch_clips".
        config (dict): The channel's YAML settings.
        only_streamer (str or None): Just this streamer (must be approved).
        dry_run (bool): True = list what would happen, save and download nothing.

    Returns:
        dict: Totals {"new", "already_saved", "skipped", "downloaded"}.

    Raises:
        StreamerNotApproved: If only_streamer is not approved.
        ValueError / TwitchError: With a plain message for other problems.
    """
    settings = load_clip_settings(config)
    streamers = approved_streamers_to_use(settings, only_streamer, channel_key)

    totals = {"new": 0, "already_saved": 0, "skipped": 0, "downloaded": 0}
    app_token = None
    for streamer in streamers:
        if streamer.platform == "kick":
            print(f"  {streamer.username} (Kick): Kick has no official clip API — add their clips with "
                  f"'upload FILE --streamer {streamer.username} --i-own-this'.")
            continue
        # Asked for once, and only when there is a Twitch streamer to look up.
        if app_token is None:
            app_token = twitch.get_app_token()
        print(f"  {streamer.username}:")
        counts = find_new_clips(channel_key, streamer, settings.clips, app_token, dry_run)
        for key in counts:
            totals[key] += counts[key]
        print(f"    {counts['new']} new · {counts['already_saved']} already saved · {counts['skipped']} skipped")
        if dry_run:
            continue
        try:
            totals["downloaded"] += download_found_clips(channel_key, streamer.username)
        except twitch.TwitchError as error:
            # One streamer's missing login must not stop the others.
            print(f"    NOT DOWNLOADED: {error}")
    return totals


def file_fingerprint(path):
    """
    Make a fingerprint (SHA-256 hash) of a file's contents.

    Two identical files always get the same fingerprint, so the same
    upload is recognised even under a different name.

    Args:
        path (str): The file.

    Returns:
        str: 64 hexadecimal characters.
    """
    digest = hashlib.sha256()
    with open(path, "rb") as file:
        # Read 1 MB at a time so a large video never fills the memory.
        piece = file.read(1024 * 1024)
        while piece:
            digest.update(piece)
            piece = file.read(1024 * 1024)
    return digest.hexdigest()


def add_uploaded_video(channel_key, config, file_path, streamer_name, platform, i_own_this, note=""):
    """
    Add a clip file you were given by an approved streamer (mainly for Kick).

    Kick has no official clip API, so a Kick streamer sends you the
    file and this adds it to the pipeline. It works for an approved
    Twitch streamer's file too (for example a moment they sent you).

    Args:
        channel_key (str): The clips channel.
        config (dict): The channel's YAML settings.
        file_path (str): The video file on this computer.
        streamer_name (str): The approved streamer's login.
        platform (str): "kick" or "twitch" — where that streamer streams.
        i_own_this (bool): Must be True: you confirm the file came from the
                           streamer (or you made it) and you may use it.
        note (str): Optional note, e.g. "sent by DM on 2026-11-18".

    Returns:
        int or None: The new clip's ID, or None if this exact file was added before.

    Raises:
        StreamerNotApproved: If the streamer is not on the approved list.
        ValueError: If the confirmation is missing or the file is unusable.
    """
    from src.video.clip_render import probe_video

    settings = load_clip_settings(config)
    streamer = find_approved_streamer(settings, streamer_name, channel_key, platform)
    if not i_own_this:
        raise ValueError("Uploaded clips need --i-own-this: confirm the file came from the "
                         "streamer (or you made it) and you have permission to use it.")
    if not os.path.isfile(file_path):
        raise ValueError(f"File not found: {file_path}")
    extension = os.path.splitext(file_path)[1].lower()
    if extension not in UPLOAD_EXTENSIONS:
        raise ValueError(f"Use a video file ({', '.join(UPLOAD_EXTENSIONS)}).")

    fingerprint = file_fingerprint(file_path)
    if is_clip_saved("upload", fingerprint):
        return None

    details = probe_video(file_path)
    cached_path = clip_file_path("upload", fingerprint[:32], extension)
    os.makedirs(os.path.dirname(cached_path), exist_ok=True)
    shutil.copyfile(file_path, cached_path)

    ownership = "File added with --i-own-this"
    if note:
        ownership += f": {note}"
    clip_id = save_found_clip(channel_key, {
        "source": "upload",
        "source_clip_id": fingerprint,
        "streamer": streamer.username,
        "platform": streamer.platform,
        "credit_text": credit_for(streamer),
        "title": os.path.splitext(os.path.basename(file_path))[0][:200],
        "duration_seconds": details["duration"],
        "ownership_note": ownership,
    })
    update_clip(clip_id, status="downloaded", file_path=cached_path)
    return clip_id

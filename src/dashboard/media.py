# =============================================================
# src/dashboard/media.py — the private video library
#
# Lists videos (from the database and any MP4 files found in
# output/videos/), and makes thumbnails and durations for them.
#
# Safety: a file path can come from the browser, so every path is
# checked with output_file(). Only files inside output/ may be read;
# "../" tricks and shortcuts pointing elsewhere are refused.
#
# Thumbnails and durations take FFmpeg a moment to make, so they are
# saved in cache/dashboard/. The cache name includes the file's size
# and change time, so a re-rendered video gets a fresh thumbnail.
# =============================================================

import hashlib
import json
import os
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from src.dashboard.data import video_rows
from src.dashboard.settings import PROJECT_ROOT

OUTPUT_ROOT = PROJECT_ROOT / "output"
MEDIA_CACHE = PROJECT_ROOT / "cache" / "dashboard"

LIBRARY_PAGE_SIZE = 12


def output_file(path, suffix=None):
    """
    Check that a path is a real file inside output/ and return it.

    Args:
        path (str or Path or None): A saved path (absolute, or relative
                                    to the project folder).
        suffix (str or None): The required file ending, e.g. ".mp4".

    Returns:
        Path or None: The full, checked path, or None if the file is
                      missing, outside output/, or has the wrong ending.

    Example:
        output_file("output/news/story.md", ".md")    # → Path(...)
        output_file("../.env")                         # → None
    """
    if not path:
        return None
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = PROJECT_ROOT / candidate
    # resolve() removes "../" parts and follows shortcuts, so the check
    # below sees where the path REALLY points.
    candidate = candidate.resolve()

    if not candidate.is_relative_to(OUTPUT_ROOT.resolve()):
        return None
    if not candidate.is_file():
        return None
    if suffix and candidate.suffix.lower() != suffix:
        return None
    return candidate


def relative_video(path):
    """
    Get a video's path relative to output/, for use in page links.

    Args:
        path (str or Path or None): The saved video path.

    Returns:
        str or None: For example "videos/news/story.mp4", or None if
                     the path is not a safe MP4 file.
    """
    candidate = output_file(path, ".mp4")
    if candidate is None:
        return None
    return candidate.relative_to(OUTPUT_ROOT.resolve()).as_posix()


def requested_video(relative):
    """
    Turn a path from a page link back into a checked MP4 file.

    Args:
        relative (str): For example "videos/news/story.mp4".

    Returns:
        Path or None: The file, or None if it is unsafe or missing.
    """
    if Path(relative).is_absolute():
        return None
    return output_file(OUTPUT_ROOT / relative, ".mp4")


def cache_key(path):
    """
    Make a name for a file's cached thumbnail or duration.

    Args:
        path (Path): The video file.

    Returns:
        str: A fingerprint that changes when the file changes.
    """
    stat = path.stat()
    identity = f"{path}:{stat.st_size}:{stat.st_mtime_ns}"
    return hashlib.sha256(identity.encode()).hexdigest()


def video_duration(path):
    """
    Get a video's length in seconds (cached after the first time).

    Args:
        path (Path or None): The video file.

    Returns:
        float or None: The length, or None if the file is missing or
                       ffprobe (part of FFmpeg) cannot read it.
    """
    if path is None:
        return None
    try:
        MEDIA_CACHE.mkdir(parents=True, exist_ok=True)
        cache = MEDIA_CACHE / f"{cache_key(path)}.json"
        if cache.exists():
            return json.loads(cache.read_text())["duration"]

        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
            capture_output=True, text=True, timeout=10, check=True,
        )
        duration = float(json.loads(result.stdout)["format"]["duration"])
        cache.write_text(json.dumps({"duration": duration}), encoding="utf-8")
        return duration
    except (OSError, subprocess.SubprocessError, ValueError, KeyError):
        # OSError also covers a file deleted while the page was loading.
        return None


def thumbnail(path):
    """
    Get a small JPEG preview of a video's first frame (cached).

    Args:
        path (Path): The video file.

    Returns:
        Path or None: The JPEG file, or None if FFmpeg fails.
    """
    MEDIA_CACHE.mkdir(parents=True, exist_ok=True)
    target = MEDIA_CACHE / f"{cache_key(path)}.jpg"
    if target.exists():
        return target

    # FFmpeg writes to a temporary file first, which is then renamed.
    # Renaming is instant, so another browser asking for the same
    # thumbnail never reads a half-written image.
    handle, temporary_name = tempfile.mkstemp(suffix=".jpg", dir=MEDIA_CACHE)
    os.close(handle)
    try:
        subprocess.run(
            ["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(path),
             "-frames:v", "1", "-vf", "scale=360:-2", temporary_name],
            capture_output=True, timeout=25, check=True,
        )
        os.replace(temporary_name, target)
        return target
    except (OSError, subprocess.SubprocessError):
        return None
    finally:
        if os.path.exists(temporary_name):
            os.remove(temporary_name)


def tracked_entries():
    """
    List the videos saved in the database, for the library.

    Returns:
        tuple: (list of video dicts, set of their relative MP4 paths)
    """
    entries = []
    known_paths = set()
    for video in video_rows():
        relative = relative_video(video["video_path"])
        if relative:
            known_paths.add(relative)
        video["relative"] = relative
        video["duration"] = None
        entries.append(video)
    return entries, known_paths


def untracked_entries(known_paths):
    """
    List MP4 files in output/videos/ that the database doesn't know about.

    Args:
        known_paths (set[str]): Relative paths already in the database.

    Returns:
        list[dict]: One entry per unknown file, with status "untracked".
    """
    entries = []
    for path in sorted((OUTPUT_ROOT / "videos").rglob("*.mp4")):
        relative = relative_video(path)
        if relative is None or relative in known_paths:
            continue
        # "videos/news/story.mp4" → the channel is the middle folder.
        parts = Path(relative).parts
        if len(parts) > 2:
            channel = parts[1]
        else:
            channel = "unknown"
        # Use UTC like the database, so both kinds of entry sort together.
        modified = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
        entries.append({
            "id": None,
            "story_title": path.stem.replace("-", " "),
            "channel": channel,
            "created_at": modified.isoformat(timespec="seconds"),
            "status": "untracked",
            "relative": relative,
            "duration": None,
            "views": None,
            "total_cost_usd": None,
        })
    return entries


def matches_filters(item, channel, status, search):
    """
    Check whether a library entry matches the page's filters.

    Args:
        item (dict): A library entry.
        channel (str): Required channel, or "" for any.
        status (str): Required status, or "" for any.
        search (str): Text the title must contain (any capitalisation).

    Returns:
        bool: True if the entry should be shown.
    """
    if channel and item["channel"] != channel:
        return False
    if status and item["status"] != status:
        return False
    if search.casefold() not in item["story_title"].casefold():
        return False
    return True


def newest_first_key(item):
    """
    Give the value used to sort library entries (newest first).

    Args:
        item (dict): A library entry.

    Returns:
        str: Its creation time (UTC text sorts in time order).
    """
    return item["created_at"] or ""


def library(channel="", status="", search="", page=1, page_size=LIBRARY_PAGE_SIZE):
    """
    Get one page of the video library.

    Args:
        channel (str): Only this channel, or "" for all.
        status (str): Only this status (e.g. "drafted", "untracked"), or "".
        search (str): Only titles containing this text, or "".
        page (int): Which page to show, starting at 1.
        page_size (int): How many videos per page.

    Returns:
        dict: "items" (this page's entries), "page", "page_count" and
              "total" (how many entries matched).
    """
    entries, known_paths = tracked_entries()
    entries.extend(untracked_entries(known_paths))

    filtered = []
    for item in entries:
        if matches_filters(item, channel, status, search):
            filtered.append(item)
    filtered.sort(key=newest_first_key, reverse=True)

    # Round up: 13 videos at 12 per page need 2 pages.
    page_count = max(1, (len(filtered) + page_size - 1) // page_size)
    page = min(max(1, page), page_count)
    first = (page - 1) * page_size
    visible = filtered[first:first + page_size]

    # Durations need FFmpeg, so they are only worked out for this page.
    for item in visible:
        if item["relative"]:
            item["duration"] = video_duration(requested_video(item["relative"]))

    return {"items": visible, "page": page, "page_count": page_count, "total": len(filtered)}


# Files the pipeline saves next to a script, named "<script name><ending>".
SCRIPT_COMPANION_ENDINGS = ["_storyboard.json", "_sceneplan.json", "_visuals.json"]


def remove_cached_previews(path):
    """
    Delete a video's cached thumbnail and duration, if there are any.

    Args:
        path (Path): The video file (it must still exist).
    """
    key = cache_key(path)
    for ending in (".jpg", ".json"):
        cached = MEDIA_CACHE / f"{key}{ending}"
        if cached.exists():
            cached.unlink()


def delete_video_file(path):
    """
    Delete one MP4 inside output/, along with its cached previews and the
    clip review files saved next to it (transcript and bleep log).

    Args:
        path (str or Path or None): The saved video path.

    Returns:
        bool: True if a file was deleted, False if there was nothing safe
              to delete (missing, outside output/, or not an MP4).
    """
    video = output_file(path, ".mp4")
    if video is None:
        return False
    remove_cached_previews(video)
    for ending in (".transcript.json", ".bleeps.json"):
        companion = output_file(video.with_name(video.stem + ending), ".json")
        if companion is not None:
            companion.unlink()
    video.unlink()
    return True


def delete_script_files(script_path):
    """
    Delete a script and the storyboard/plan files saved next to it.

    Args:
        script_path (str or None): The saved script path (a .md file).

    Returns:
        int: How many files were deleted.
    """
    script = output_file(script_path, ".md")
    if script is None:
        return 0
    removed = 0
    for ending in SCRIPT_COMPANION_ENDINGS:
        # "story.md" → "story_storyboard.json", "story_sceneplan.json", ...
        companion = output_file(script.with_name(script.stem + ending), ".json")
        if companion is not None:
            companion.unlink()
            removed += 1
    script.unlink()
    return removed + 1

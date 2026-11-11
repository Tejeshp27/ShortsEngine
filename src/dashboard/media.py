# =============================================================
# Private video library helpers. Only files inside output/ may be
# read. Thumbnails and durations are generated locally and cached
# using each file's size and modification time.
# =============================================================

import hashlib
import json
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path

from src.dashboard.data import video_rows
from src.dashboard.settings import PROJECT_ROOT

OUTPUT_ROOT = PROJECT_ROOT / "output"
MEDIA_CACHE = PROJECT_ROOT / "cache" / "dashboard"


def output_file(path, suffix=None):
    """Return a safe resolved path or None for an unsafe or missing file.

    path may be a saved absolute path or project-relative path. suffix, when
    supplied, is the required file extension. Symlinks outside output are refused.
    """
    if not path:
        return None
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = PROJECT_ROOT / candidate
    candidate = candidate.resolve()
    if not candidate.is_relative_to(OUTPUT_ROOT.resolve()) or not candidate.is_file():
        return None
    if suffix and candidate.suffix.lower() != suffix:
        return None
    return candidate


def relative_video(path):
    """Return a video's path relative to output, or None when it is unsafe."""
    candidate = output_file(path, ".mp4")
    if candidate is None:
        return None
    return candidate.relative_to(OUTPUT_ROOT.resolve()).as_posix()


def requested_video(relative):
    """Resolve relative under output and return a safe MP4 path or None."""
    if Path(relative).is_absolute():
        return None
    return output_file(OUTPUT_ROOT / relative, ".mp4")


def cache_key(path):
    """Return a fingerprint for path and its current file contents' metadata."""
    stat = path.stat()
    identity = f"{path}:{stat.st_size}:{stat.st_mtime_ns}"
    return hashlib.sha256(identity.encode()).hexdigest()


def video_duration(path):
    """Return cached duration in seconds for path, or None if FFprobe fails."""
    MEDIA_CACHE.mkdir(parents=True, exist_ok=True)
    cache = MEDIA_CACHE / f"{cache_key(path)}.json"
    try:
        if cache.exists():
            return json.loads(cache.read_text())["duration"]
        result = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                 "-of", "json", str(path)], capture_output=True, text=True, timeout=10, check=True)
        duration = float(json.loads(result.stdout)["format"]["duration"])
        cache.write_text(json.dumps({"duration": duration}), encoding="utf-8")
        return duration
    except (OSError, subprocess.SubprocessError, ValueError, KeyError):
        return None


def thumbnail(path):
    """Return a cached JPEG thumbnail for path, or None if FFmpeg fails."""
    MEDIA_CACHE.mkdir(parents=True, exist_ok=True)
    target = MEDIA_CACHE / f"{cache_key(path)}.jpg"
    if target.exists():
        return target
    # A unique temporary file allows two browsers to ask for the same frame.
    with tempfile.NamedTemporaryFile(suffix=".jpg", dir=MEDIA_CACHE) as temporary:
        try:
            subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(path),
                            "-frames:v", "1", "-vf", "scale=360:-2", temporary.name],
                           capture_output=True, timeout=25, check=True)
            target.write_bytes(Path(temporary.name).read_bytes())
            return target
        except (OSError, subprocess.SubprocessError):
            return None


def library(channel="", status="", search="", page=1, page_size=12):
    """Return a filtered page of tracked videos and untracked MP4 files.

    channel/status filter exact values, search matches titles, and page starts
    at 1. Metadata is inspected only for the visible page to keep it responsive.
    """
    entries = []
    known = set()
    for video in video_rows():
        relative = relative_video(video["video_path"])
        if relative:
            known.add(relative)
        video["relative"] = relative
        video["duration"] = None
        entries.append(video)
    for path in sorted((OUTPUT_ROOT / "videos").rglob("*.mp4")):
        relative = relative_video(path)
        if relative is None or relative in known:
            continue
        parts = Path(relative).parts
        entries.append({"id": None, "story_title": path.stem.replace("-", " "),
                        "channel": parts[1] if len(parts) > 2 else "unknown",
                        "created_at": datetime.fromtimestamp(path.stat().st_mtime).isoformat(),
                        "status": "untracked", "relative": relative, "duration": None,
                        "views": None, "total_cost_usd": None})
    filtered = []
    for item in entries:
        if channel and item["channel"] != channel:
            continue
        if status and item["status"] != status:
            continue
        if search.casefold() not in item["story_title"].casefold():
            continue
        filtered.append(item)
    # Sorting by a named field is clearer here than a hand-written sort loop.
    from operator import itemgetter
    filtered.sort(key=itemgetter("created_at"), reverse=True)
    page_count = max(1, (len(filtered) + page_size - 1) // page_size)
    page = min(max(1, page), page_count)
    visible = filtered[(page - 1) * page_size:page * page_size]
    for item in visible:
        if item["relative"]:
            item["duration"] = video_duration(requested_video(item["relative"]))
    return {"items": visible, "page": page, "page_count": page_count, "total": len(filtered)}

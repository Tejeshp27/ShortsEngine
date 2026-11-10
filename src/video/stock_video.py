# =============================================================
# src/video/stock_video.py — video B-roll integration (M.3)
#
# When the scene plan calls for "footage", this module:
#   1. Searches for a clip matching the query (Pexels first, then Pixabay).
#   2. Downloads the clip (preferring portrait orientation for 9:16 output;
#      falls back to landscape and crops the center vertical strip).
#   3. Extracts N evenly-spaced frames using FFmpeg, one per caption group.
#   4. Returns the PNG frame paths — these go into build_frames() as
#      backgrounds, so the video "plays" in the background while captions
#      change in the foreground.
#
# The downloaded clip is cached under cache/stock_video/ so the same search
# query does not trigger a re-download on the next render.
#
# Provider priority:
#   1. Pexels Video (PEXELS_API_KEY) — best quality, free, commercial use
#   2. Pixabay Video (PIXABAY_API_KEY) — free, commercial use, no attribution
#   3. Returns [] if neither key is set → caller falls back to stock photos
#
# Credits are logged to output/video_credits.txt.
#
# How it fits in:
#   render.py → _build_visual_frames() → get_video_frames() (here)
# =============================================================

import hashlib
import json
import os
import subprocess
import urllib.parse
import urllib.request


STOCK_VIDEO_CACHE = os.path.join("cache", "stock_video")
PEXELS_VIDEO_API = "https://api.pexels.com/videos/search"
PIXABAY_VIDEO_API = "https://pixabay.com/api/videos/"


def _search_pexels_video(query, orientation="portrait"):
    """
    Search Pexels Video for clips matching the query.

    Args:
        query (str): Search term, e.g. "plane flying sky".
        orientation (str): "portrait", "landscape", or "square".

    Returns:
        list[dict]: Raw video objects from the Pexels API, or [] on failure.
    """
    api_key = os.getenv("PEXELS_API_KEY", "")
    if not api_key:
        return []

    params = urllib.parse.urlencode({
        "query": query,
        "per_page": 10,
        "orientation": orientation,
        "size": "medium",   # medium = at least 1280×720
    })
    url = f"{PEXELS_VIDEO_API}?{params}"

    try:
        req = urllib.request.Request(url, headers={"Authorization": api_key})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
        return data.get("videos", [])
    except Exception as err:
        print(f"  Pexels Video search failed: {err}")
        return []


def _pick_best_video_file(video_files, prefer_portrait=True):
    """
    Choose the best quality video file from a Pexels video's file list.

    Scoring: portrait orientation > HD quality > MP4 format.

    Args:
        video_files (list[dict]): The "video_files" list from a Pexels video.
        prefer_portrait (bool): If True, rank portrait videos higher.

    Returns:
        tuple: (download_url, is_portrait) or (None, False) if list is empty.
    """
    if not video_files:
        return None, False

    def score(f):
        """Higher is better."""
        h = f.get("height", 0)
        w = f.get("width", 1)
        is_portrait = 1 if h > w else 0
        is_hd = 1 if f.get("quality") == "hd" else 0
        is_mp4 = 1 if "mp4" in f.get("file_type", "") else 0
        return (is_portrait * 10 if prefer_portrait else 0) + is_hd * 2 + is_mp4

    best = max(video_files, key=score)
    h = best.get("height", 0)
    w = best.get("width", 1)
    is_portrait = h > w
    return best.get("link"), is_portrait


def _download_clip(url, dest_path):
    """
    Download a video file to dest_path.

    Args:
        url (str): The direct video file URL from Pexels.
        dest_path (str): Where to save the .mp4 file.

    Returns:
        bool: True if the download succeeded.
    """
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "ShortsEngine/0.1 (portfolio project)"},
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = resp.read()

        with open(dest_path, "wb") as f:
            f.write(data)
        return True

    except Exception as err:
        print(f"  Video download failed: {err}")
        return False


def _get_clip_duration(clip_path):
    """
    Ask FFprobe for the duration of a video file in seconds.

    Args:
        clip_path (str): Local path to the .mp4 file.

    Returns:
        float: Duration in seconds, or 0.0 on failure.
    """
    result = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            clip_path,
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return 0.0
    try:
        return float(result.stdout.strip())
    except ValueError:
        return 0.0


def _extract_frame(clip_path, timestamp, output_path, is_portrait):
    """
    Extract a single frame from a video clip at the given timestamp.

    Portrait clips (taller than wide) are scaled to 1080×1920.
    Landscape clips are cropped to the center 9:16 strip, then scaled.

    Args:
        clip_path (str): Local path to the .mp4 file.
        timestamp (float): Time in seconds at which to extract the frame.
        output_path (str): Where to save the .png frame.
        is_portrait (bool): Whether the clip is taller than it is wide.

    Returns:
        bool: True if the frame was extracted successfully.
    """
    if is_portrait:
        # Portrait clip — scale to fill 1080×1920, then crop any overhang.
        vf = "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920"
    else:
        # Landscape clip — crop the center vertical 9:16 strip, then scale.
        # Explanation of the crop filter:
        #   crop=W:H:X:Y
        #   W = ih * 9/16  → width of the crop, equal to (height × 9/16)
        #   H = ih          → crop the full height
        #   X = (iw-W) / 2  → start at the horizontal centre
        #   Y = 0            → start at the top
        vf = "crop=ih*9/16:ih:(iw-ih*9/16)/2:0,scale=1080:1920"

    result = subprocess.run(
        [
            "ffmpeg", "-y",
            "-ss", str(timestamp),
            "-i", clip_path,
            "-vframes", "1",
            "-vf", vf,
            output_path,
        ],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0 and os.path.exists(output_path)


def _search_pixabay_video(query, orientation="vertical"):
    """
    Search Pixabay Video for clips matching the query.

    Pixabay is a free alternative to Pexels. No attribution is required
    but results vary in quality — use Pexels first when available.

    Args:
        query (str): Search term, e.g. "plane flying sky".
        orientation (str): "vertical" (portrait) or "horizontal" (landscape).

    Returns:
        list[dict]: Pixabay video hit objects, or [] on failure / no key.
    """
    api_key = os.getenv("PIXABAY_API_KEY", "")
    if not api_key:
        return []

    params = urllib.parse.urlencode({
        "key": api_key,
        "q": query,
        "per_page": 10,
        "video_type": "film",
        "orientation": orientation,
        "safesearch": "true",   # only clips suitable for all ages
    })
    url = f"{PIXABAY_VIDEO_API}?{params}"

    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "ShortsEngine/0.1"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
        return data.get("hits", [])
    except Exception as err:
        print(f"  Pixabay Video search failed: {err}")
        return []


def _pick_pixabay_video_url(hit):
    """
    Pick which size of a Pixabay video to download.

    Pixabay offers each video in up to four sizes: tiny, small, medium
    and large. We take the smallest one that is at least 720 pixels on
    its shorter side — sharp enough for a background, without downloading
    a huge 4K file. If none is that big, we take the biggest available.

    Args:
        hit (dict): A Pixabay video hit from the API response.

    Returns:
        tuple: (download_url, is_portrait) or (None, False).
    """
    videos = hit.get("videos", {})

    # Smallest first, so the first one that is big enough wins.
    for size in ("tiny", "small", "medium", "large"):
        entry = videos.get(size, {})
        w = entry.get("width", 0)
        h = entry.get("height", 0)
        if entry.get("url", "") and min(w, h) >= 720:
            return entry["url"], h > w

    # Nothing reached 720 pixels — fall back to the biggest size on offer.
    for size in ("large", "medium", "small", "tiny"):
        entry = videos.get(size, {})
        if entry.get("url", ""):
            return entry["url"], entry.get("height", 1) > entry.get("width", 1)

    return None, False


def _log_credit(query, video_id, photographer):
    """
    Append a Pexels credit line to output/pexels_credits.txt.

    Args:
        query (str): The search query that found this clip.
        video_id (int): The Pexels video ID.
        photographer (str): The photographer/videographer name from Pexels.
    """
    credits_path = os.path.join("output", "pexels_credits.txt")
    os.makedirs("output", exist_ok=True)
    with open(credits_path, "a", encoding="utf-8") as f:
        f.write(f"Video: {video_id} | Query: {query} | By: {photographer}\n")


def get_video_clip(query, variant=0):
    """
    Find one stock video clip matching the query and download it.

    Tries Pexels first (if PEXELS_API_KEY is set), then Pixabay (if
    PIXABAY_API_KEY is set). The clip is saved in cache/stock_video/ and
    reused on later runs, so the same search never downloads twice.

    Args:
        query (str): Search term, e.g. "capitol building".
        variant (int): Which search result to use: 0 is the top result,
                       1 the second, and so on. Asking for different
                       variants of one query gives different clips, so a
                       video can reuse a topic without repeating a clip.

    Returns:
        tuple: (clip_path, is_portrait). clip_path is the downloaded .mp4
               file, and is_portrait is True if the clip is taller than
               it is wide. Returns (None, False) if no clip was found.

    Example:
        clip_path, is_portrait = get_video_clip("voting booth")
        # → ("cache/stock_video/3fa9c1d2e4b5a6f7.mp4", False)
    """
    os.makedirs(STOCK_VIDEO_CACHE, exist_ok=True)

    # Cache key based on query so the same search reuses the same clip.
    # Variant 0 keeps the plain query as its key (so clips cached before
    # variants existed are still found); other variants add their number.
    cache_name = query
    if variant > 0:
        cache_name = f"{query}#{variant}"
    query_hash = hashlib.sha256(cache_name.encode()).hexdigest()[:16]
    clip_path = os.path.join(STOCK_VIDEO_CACHE, f"{query_hash}.mp4")
    meta_path = clip_path.replace(".mp4", "_meta.json")

    # --- Ensure we have a downloaded clip ---
    if not os.path.exists(clip_path):
        download_url = None
        is_portrait = False
        photographer = "Unknown"
        video_id = 0

        # Priority 1: Pexels portrait (best quality for 9:16 output).
        videos = _search_pexels_video(query, orientation="portrait")
        if len(videos) > variant:
            video_obj = videos[variant]
            files = video_obj.get("video_files", [])
            download_url, is_portrait = _pick_best_video_file(files, prefer_portrait=True)
            photographer = video_obj.get("user", {}).get("name", "Unknown")
            video_id = video_obj.get("id", 0)

        # Priority 2: Pexels landscape (crop to 9:16 during frame extraction).
        if not download_url:
            videos = _search_pexels_video(query, orientation="landscape")
            if len(videos) > variant:
                video_obj = videos[variant]
                files = video_obj.get("video_files", [])
                download_url, is_portrait = _pick_best_video_file(files, prefer_portrait=False)
                photographer = video_obj.get("user", {}).get("name", "Unknown")
                video_id = video_obj.get("id", 0)

        # Priority 3: Pixabay vertical (free, no attribution required).
        if not download_url:
            videos = _search_pixabay_video(query, orientation="vertical")
            if len(videos) > variant:
                video_obj = videos[variant]
                download_url, is_portrait = _pick_pixabay_video_url(video_obj)
                photographer = video_obj.get("user", "Unknown")
                video_id = video_obj.get("id", 0)

        # Priority 4: Pixabay horizontal (crop to 9:16).
        if not download_url:
            videos = _search_pixabay_video(query, orientation="horizontal")
            if len(videos) > variant:
                video_obj = videos[variant]
                download_url, is_portrait = _pick_pixabay_video_url(video_obj)
                photographer = video_obj.get("user", "Unknown")
                video_id = video_obj.get("id", 0)

        if not download_url:
            # No clip found anywhere — the caller decides what to show instead.
            return None, False

        print(f"  Downloading video clip for: {query[:40]}...")
        success = _download_clip(download_url, clip_path)
        if not success:
            return None, False

        # Save metadata so we know orientation on the next render.
        with open(meta_path, "w") as f:
            json.dump({
                "is_portrait": is_portrait,
                "query": query,
                "id": video_id,
                "photographer": photographer,
            }, f)

        _log_credit(query, video_id, photographer)

    else:
        # Load cached metadata.
        is_portrait = False
        if os.path.exists(meta_path):
            with open(meta_path) as f:
                meta = json.load(f)
            is_portrait = meta.get("is_portrait", False)

    return clip_path, is_portrait


def get_video_frames(query, num_frames, segment_index=0):
    """
    Find a Pexels Video clip matching the query and extract num_frames
    evenly-spaced frames from it.

    The frames are PNG files sized 1080×1920 (9:16 portrait). They go
    into the existing build_frames() pipeline as background images, so
    the background "plays" through the clip while captions change.

    If PEXELS_API_KEY is not set, or no clip matches the query,
    this function returns [] and the caller falls back to stock photos.

    Args:
        query (str): Search term, e.g. "plane flying sky".
        num_frames (int): How many frames to extract (should equal the
                          number of caption groups for this segment, typically 5).
        segment_index (int): Used to generate unique output filenames
                             so frames from different segments don't overwrite each other.

    Returns:
        list[str]: Local PNG paths (length ≤ num_frames), or [] on failure.

    Example:
        frames = get_video_frames("crowd at rally", 5, segment_index=2)
        # → ["cache/stock_video/abc123_seg2_f0.png", ..., "..._f4.png"]
    """
    # --- Step 1: Ensure we have a downloaded clip ---
    clip_path, is_portrait = get_video_clip(query)
    if clip_path is None:
        # No clip found anywhere — caller will fall back to stock photos.
        return []

    # Frame file names start with the same hash the clip is saved under.
    query_hash = os.path.splitext(os.path.basename(clip_path))[0]

    # --- Step 2: Extract frames ---
    duration = _get_clip_duration(clip_path)
    if duration <= 0.0:
        return []

    frame_paths = []

    for i in range(num_frames):
        frame_path = os.path.join(
            STOCK_VIDEO_CACHE,
            f"{query_hash}_seg{segment_index}_f{i}.png",
        )

        if os.path.exists(frame_path):
            frame_paths.append(frame_path)
            continue

        # Spread frames evenly across the clip.
        # Stay 0.1s before the end to avoid requesting past EOF.
        timestamp = min(i * duration / max(num_frames, 1), duration - 0.1)

        ok = _extract_frame(clip_path, timestamp, frame_path, is_portrait)
        if ok:
            frame_paths.append(frame_path)
        else:
            # Stop on first failure — partial frames look better than a crash.
            print(f"  Frame extraction failed at t={timestamp:.1f}s — "
                  f"using {len(frame_paths)}/{num_frames} frames.")
            break

    if frame_paths:
        print(f"  Video frames ready: '{query[:30]}' "
              f"({len(frame_paths)}/{num_frames} frames)")

    return frame_paths

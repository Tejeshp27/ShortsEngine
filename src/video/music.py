# =============================================================
# src/video/music.py — background music generation for videos
#
# Generates a royalty-free background music track that plays
# quietly under the voiceover during video playback.
#
# Provider: Mubert (mubert.com/api)
#   - Free tier available; sign up at mubert.com/api to get a key.
#   - Generates music matched to a mood and exact duration.
#   - Tracks are cached after first use — the same mood+duration
#     combo won't re-call the API on the next render.
#
# Setup:
#   1. Sign up at mubert.com/api (free tier available).
#   2. Add MUBERT_API_KEY and MUBERT_EMAIL to .env.
#   3. Set music_mood and music_volume in the channel YAML (optional).
#   4. Run with --music flag: python -m src.main --channel news --render --music
#
# If neither key is set, get_background_music() returns None and
# the renderer simply skips the music step — no crash, no error.
#
# How it fits in:
#   main.py → render.py → get_background_music() (here)
# =============================================================

import hashlib
import json
import os
import time
import urllib.request


MUBERT_ACCESS_URL = "https://api.mubert.com/v2/GetServiceAccess"
MUBERT_GENERATE_URL = "https://api.mubert.com/v2/RecordTrackTTM"
MUSIC_CACHE = os.path.join("cache", "music")


def _get_mubert_pat():
    """
    Exchange the Mubert API key + email for a Personal Access Token (PAT).

    Mubert uses a two-step auth: first you trade your API key for a
    session token (PAT), then you use the PAT for all music requests.

    Args: (reads MUBERT_API_KEY and MUBERT_EMAIL from environment)

    Returns:
        str or None: A Personal Access Token, or None if auth failed.
    """
    api_key = os.getenv("MUBERT_API_KEY", "")
    email = os.getenv("MUBERT_EMAIL", "")

    if not api_key or not email:
        return None

    payload = json.dumps({
        "method": "GetServiceAccess",
        "params": {
            "email": email,
            "license": "ttm",   # "ttm" = text-to-music licence
            "token": api_key,
        },
    }).encode("utf-8")

    req = urllib.request.Request(
        MUBERT_ACCESS_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
        return data.get("data", {}).get("pat")
    except Exception as err:
        print(f"  Mubert auth failed: {err}")
        return None


def _request_track(pat, duration, mood):
    """
    Ask Mubert to generate a music track and return its download URL.

    Mubert generates the track asynchronously — the URL it returns
    will be ready within about 10–30 seconds.

    Args:
        pat (str): Personal Access Token from _get_mubert_pat().
        duration (int): Length of the track in seconds.
        mood (str): Music mood, e.g. "focused", "calm", "intense".

    Returns:
        str or None: The download URL for the generated track.
    """
    payload = json.dumps({
        "method": "RecordTrackTTM",
        "params": {
            "pat": pat,
            "duration": duration,
            "mode": "loop",   # the track will loop cleanly if the video is longer
            "bpm": 90,
            "mood": [mood],
        },
    }).encode("utf-8")

    req = urllib.request.Request(
        MUBERT_GENERATE_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
        tasks = data.get("data", {}).get("tasks", [])
        if tasks:
            return tasks[0].get("download_link")
        return None
    except Exception as err:
        print(f"  Mubert track request failed: {err}")
        return None


def _download_track(url, dest_path, max_retries=5):
    """
    Download the generated music track, retrying while it's still being prepared.

    Mubert generates tracks asynchronously. The URL is valid immediately
    but the file might not be ready for 10–30 seconds. We retry a few
    times with increasing waits.

    Args:
        url (str): The Mubert download URL.
        dest_path (str): Where to save the .mp3 file.
        max_retries (int): How many times to retry before giving up.

    Returns:
        bool: True if the download succeeded.
    """
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)

    for attempt in range(max_retries):
        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "ShortsEngine/0.1"},
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = resp.read()

            # A real MP3 is always at least a few KB.
            # If the file is tiny, Mubert is probably still generating.
            if len(data) < 2000:
                raise ValueError("Track not ready yet (response too small)")

            with open(dest_path, "wb") as f:
                f.write(data)
            return True

        except Exception:
            if attempt < max_retries - 1:
                # Wait longer each retry: 10s, 15s, 20s, 25s.
                wait = 10 + (attempt * 5)
                print(f"  Music not ready yet, retrying in {wait}s "
                      f"({attempt + 1}/{max_retries - 1})...")
                time.sleep(wait)

    print("  Failed to download background music after all retries.")
    return False


def get_background_music(duration, mood="focused", channel_key="news"):
    """
    Generate (or load from cache) a background music track for the video.

    Uses the Mubert API to create a royalty-free track that matches
    the given mood and lasts exactly as long as the video. The track is
    cached so the same mood+duration combo doesn't re-call the API.

    If MUBERT_API_KEY or MUBERT_EMAIL is not set, returns None immediately
    and the renderer skips music — no crash.

    Args:
        duration (float): How long the video is in seconds.
        mood (str): Music mood. Good options for a news channel:
                    "focused" (default), "calm", "informative",
                    "intense" (for dramatic stories), "happy".
        channel_key (str): Used to build the cache file path.

    Returns:
        str or None: Local path to the .mp3 music file, or None on failure.

    Example:
        path = get_background_music(75.0, mood="focused", channel_key="news")
        # → "cache/music/abc123def456.mp3"  (or None if no Mubert key)
    """
    if not os.getenv("MUBERT_API_KEY"):
        return None

    # Cache key: round duration to nearest 5 seconds so nearby lengths
    # reuse the same track rather than generating a new one every render.
    rounded_duration = round(int(duration) / 5) * 5
    fingerprint = hashlib.sha256(
        f"{rounded_duration}:{mood}".encode()
    ).hexdigest()[:16]
    music_path = os.path.join(MUSIC_CACHE, f"{fingerprint}.mp3")

    if os.path.exists(music_path):
        print("  Background music loaded from cache.")
        return music_path

    print(f"  Generating background music (mood={mood}, {rounded_duration}s via Mubert)...")

    pat = _get_mubert_pat()
    if not pat:
        return None

    download_url = _request_track(pat, rounded_duration, mood)
    if not download_url:
        return None

    # Mubert needs a few seconds to prepare the track before the URL is ready.
    print("  Waiting for Mubert to prepare the track (this takes ~15–40s)...")
    time.sleep(12)

    success = _download_track(download_url, music_path)
    if success:
        print(f"  Background music ready: {os.path.basename(music_path)}")
        return music_path

    return None

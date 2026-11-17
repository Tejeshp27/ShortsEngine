# =============================================================
# src/video/sound_effects.py — download hook sound effects from Freesound
#
# A punchy sound at the very start of a short-form video is one of
# the cheapest ways to hook viewers in the first second. This module
# finds and caches a short audio clip from Freesound (freesound.org)
# that plays at the beginning of every render.
#
# Freesound is a community of free, Creative Commons licensed sounds.
# No attribution is required for CC0 sounds; we save the licence info
# just in case the sound has a different licence.
#
# Requires FREESOUND_API_KEY in .env.
# If the key is missing, get_hook_sound() returns None and the
# renderer skips the effect — no crash.
#
# How it fits in:
#   main.py → render.py → get_hook_sound() (here)
#       ↓
#   The sound is mixed into the first second of the video's audio track.
# =============================================================

import hashlib
import json
import os
import urllib.parse
import urllib.request


FREESOUND_SEARCH_URL = "https://freesound.org/apiv2/search/text/"
SOUNDS_CACHE = os.path.join("cache", "sounds")


def get_hook_sound(query="news intro impact hit"):
    """
    Find and cache a short, punchy sound effect from Freesound.

    Searches for sounds matching the query that are under 3 seconds long,
    downloads the highest-quality preview, and returns the local file path.

    The same query always returns the same cached file on subsequent calls
    — Freesound is only contacted once per unique query.

    Args:
        query (str): What kind of sound to look for.
                     Default: "news intro impact hit" (works well for news channels).
                     You can also set hook_sound_query in channels/news.yaml.

    Returns:
        str or None: Local path to the downloaded .mp3 file, or None if the
                     key is missing or no matching sound is found.

    Example:
        sfx_path = get_hook_sound("news intro impact")
        # → "cache/sounds/abc123.mp3"  (or None)
    """
    api_key = os.getenv("FREESOUND_API_KEY", "")
    if not api_key:
        return None

    # Check the cache before making any network request.
    sfx_path = _cache_path(query)
    if os.path.exists(sfx_path):
        return sfx_path

    os.makedirs(SOUNDS_CACHE, exist_ok=True)

    # Search Freesound for short sounds matching the query.
    # filter=duration:[0 TO 3] limits results to sounds under 3 seconds.
    # fields=id,name,previews,duration,license tells the API what to return.
    params = urllib.parse.urlencode({
        "query": query,
        "token": api_key,
        "fields": "id,name,previews,duration,license",
        # Only short sounds, and only CC0 (public domain): other Freesound
        # licences may forbid commercial use or require credit.
        "filter": 'duration:[0 TO 3] license:"Creative Commons 0"',
        "page_size": 10,
        "sort": "rating_desc",          # highest-rated first
    })
    search_url = f"{FREESOUND_SEARCH_URL}?{params}"

    print(f"  Searching Freesound for hook sound: '{query}'...")

    try:
        req = urllib.request.Request(
            search_url,
            headers={"User-Agent": "ShortsEngine/0.1"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
    except Exception as err:
        print(f"  Freesound search failed: {err}")
        return None

    results = data.get("results", [])
    if not results:
        print(f"  Freesound: no sounds found for '{query}'.")
        return None

    # Try to download the first sound that has a preview URL.
    for sound in results:
        previews = sound.get("previews", {})

        # Prefer the high-quality preview; fall back to low-quality.
        preview_url = (
            previews.get("preview-hq-mp3")
            or previews.get("preview-lq-mp3")
        )
        if not preview_url:
            continue

        success = _download_sound(preview_url, sfx_path, sound)
        if success:
            return sfx_path

    print("  Freesound: could not download any preview sounds.")
    return None


def _download_sound(url, dest_path, sound_info):
    """
    Download one Freesound preview file and save it to dest_path.

    Args:
        url (str): The preview MP3 URL from the Freesound API.
        dest_path (str): Where to save the file on disk.
        sound_info (dict): The sound object from the API (for logging).

    Returns:
        bool: True if the download succeeded.
    """
    name = sound_info.get("name", "unknown")
    sound_id = sound_info.get("id", "?")
    licence = sound_info.get("license", "unknown licence")

    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "ShortsEngine/0.1"},
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            audio_bytes = resp.read()

        # A real MP3 file should be at least a few KB.
        if len(audio_bytes) < 1000:
            return False

        with open(dest_path, "wb") as f:
            f.write(audio_bytes)

        # Save the licence info alongside the audio file so we have a record.
        meta_path = dest_path.replace(".mp3", "_meta.json")
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump({
                "id": sound_id,
                "name": name,
                "license": licence,
                "url": f"https://freesound.org/people/{sound_id}/",
                "source": "Freesound",
            }, f, indent=2)

        print(f"  Hook sound downloaded: '{name}' (ID {sound_id}) — {licence}")
        return True

    except Exception as err:
        print(f"  Freesound download failed for '{name}': {err}")
        return False


def _cache_path(query):
    """
    Return the file path where this query's sound should be cached.

    Args:
        query (str): The search query.

    Returns:
        str: Full path, e.g. "cache/sounds/a1b2c3d4.mp3".
    """
    fingerprint = hashlib.sha256(query.lower().encode()).hexdigest()[:16]
    return os.path.join(SOUNDS_CACHE, f"{fingerprint}.mp3")

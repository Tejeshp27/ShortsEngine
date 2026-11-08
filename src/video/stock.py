# =============================================================
# src/video/stock.py — search and download stock photos from Pexels
# (Phase V.3)
#
# Pexels (pexels.com) offers a free API for photos with a licence
# that allows commercial use. No attribution is required by default,
# but we save source/creator/URL/licence info so we have a record.
#
# How the search works:
#   1. Turn the visual planner's query into a Pexels search request.
#   2. Pick the best portrait (tall/vertical) photo from the results.
#   3. Download and cache the full-size image so the same query is
#      never fetched twice.
#   4. Save one line to output/<channel>/credits.txt recording where
#      the image came from.
#
# To use this module, add your free Pexels API key to .env:
#   PEXELS_API_KEY=your-key-here
# Get a free key at https://www.pexels.com/api/
#
# How it fits in:
#   render.py → _get_visual_for_segment() → fetch_stock_photo() (here)
# =============================================================

import hashlib
import json
import os
import urllib.request
import urllib.parse


STOCK_CACHE_FOLDER = os.path.join("cache", "stock")
PEXELS_SEARCH_URL = "https://api.pexels.com/v1/search"


def _search_pexels(query, api_key, per_page=15):
    """
    Search the Pexels API and return a list of photo results.

    We ask for portrait orientation so photos fit the 9:16 frame.

    Args:
        query (str): Search term, e.g. "Capitol building exterior".
        api_key (str): Your Pexels API key.
        per_page (int): How many results to fetch (we pick the best one).

    Returns:
        list[dict]: Photo objects from the Pexels API, or [] on failure.
    """
    params = urllib.parse.urlencode({
        "query": query,
        "per_page": per_page,
        "orientation": "portrait",   # prefer tall photos for 9:16 video
    })
    url = f"{PEXELS_SEARCH_URL}?{params}"

    request = urllib.request.Request(url)
    request.add_header("Authorization", api_key)

    try:
        with urllib.request.urlopen(request, timeout=15) as resp:
            data = json.loads(resp.read())
        return data.get("photos", [])
    except Exception as err:
        print(f"  Pexels search failed for '{query}': {err}")
        return []


def _pick_best_photo(photos):
    """
    Choose the most portrait-shaped photo from a list of Pexels results.

    Portrait photos (taller than they are wide) fit the 9:16 frame
    without needing to crop as much.

    Args:
        photos (list[dict]): Pexels photo objects.

    Returns:
        dict or None: The best photo object, or None if the list is empty.
    """
    if not photos:
        return None

    # Sort by how portrait-like the photo is (height / width ratio).
    def portrait_score(photo):
        w = photo.get("width", 1)
        h = photo.get("height", 1)
        return h / w

    return max(photos, key=portrait_score)


def _cache_path_for_query(query):
    """
    Work out where to cache the downloaded image for a query.

    Args:
        query (str): The search term.

    Returns:
        tuple[str, str]: (image .jpg path, metadata .json path)
    """
    fingerprint = hashlib.sha256(query.lower().encode()).hexdigest()[:20]
    img_path = os.path.join(STOCK_CACHE_FOLDER, f"{fingerprint}.jpg")
    meta_path = os.path.join(STOCK_CACHE_FOLDER, f"{fingerprint}_meta.json")
    return img_path, meta_path


def _save_credit(channel_key, photo):
    """
    Append one line to output/<channel>/credits.txt with the photo source.

    Args:
        channel_key (str): The channel key, e.g. "news".
        photo (dict): A Pexels photo object.
    """
    folder = os.path.join("output", channel_key)
    os.makedirs(folder, exist_ok=True)
    credits_path = os.path.join(folder, "credits.txt")

    photographer = photo.get("photographer", "Unknown")
    photo_url = photo.get("url", "")
    photo_id = photo.get("id", "")

    line = (
        f"Photo ID {photo_id} by {photographer} | "
        f"Pexels | Commercial use allowed | {photo_url}\n"
    )

    with open(credits_path, "a", encoding="utf-8") as f:
        f.write(line)


def fetch_stock_photo(query, channel_key="news", api_key=None):
    """
    Search Pexels for a portrait photo matching the query, download and cache it.

    Returns the local file path so the renderer can use it as a frame.
    Returns None if no key is configured or no photo is found.

    Args:
        query (str): The search term from the visual plan.
        channel_key (str): Used to build the credits file path.
        api_key (str or None): Pexels API key. If None, reads from
                               PEXELS_API_KEY in the environment.

    Returns:
        str or None: Local path to the downloaded .jpg, or None on failure.

    Example:
        path = fetch_stock_photo("Capitol building exterior", "news")
        # -> "cache/stock/a3f7b1c2d4e5.jpg"  (or None if key not set)
    """
    key = api_key or os.getenv("PEXELS_API_KEY")

    if not key:
        # No key set — return None so the renderer falls back to a text card.
        return None

    img_path, meta_path = _cache_path_for_query(query)

    # Already downloaded? Use the cached file.
    if os.path.exists(img_path) and os.path.exists(meta_path):
        return img_path

    os.makedirs(STOCK_CACHE_FOLDER, exist_ok=True)

    photos = _search_pexels(query, key)
    photo = _pick_best_photo(photos)

    if not photo:
        print(f"  No Pexels result for: '{query}'")
        return None

    # Download the medium-size version (good enough, smaller file).
    download_url = photo["src"].get("large", photo["src"].get("original", ""))
    if not download_url:
        return None

    try:
        urllib.request.urlretrieve(download_url, img_path)
    except Exception as err:
        print(f"  Failed to download photo: {err}")
        return None

    # Save metadata for the credits file.
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump({
            "id": photo.get("id"),
            "photographer": photo.get("photographer"),
            "url": photo.get("url"),
            "query": query,
            "licence": "Pexels Licence — free for commercial use",
        }, f, indent=2)

    _save_credit(channel_key, photo)
    print(f"  Stock photo downloaded: {os.path.basename(img_path)} "
          f"(by {photo.get('photographer', 'unknown')})")
    return img_path

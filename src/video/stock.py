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
import time
import urllib.request
import urllib.parse


STOCK_CACHE_FOLDER = os.path.join("cache", "stock")
PEXELS_SEARCH_URL = "https://api.pexels.com/v1/search"
PIXABAY_API_URL = "https://pixabay.com/api/"
WIKIMEDIA_API_URL = "https://commons.wikimedia.org/w/api.php"


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


def fetch_pixabay_photo(query):
    """
    Search Pixabay for a portrait photo and download it.

    Pixabay is a completely free alternative to Pexels. No attribution is
    required. It requires a free API key (same key as Pixabay video).

    Used as a fallback when PEXELS_API_KEY is not set, or when Pexels
    returns no results for a query.

    Args:
        query (str): The search term, e.g. "Capitol building exterior".

    Returns:
        str or None: Local path to the downloaded image, or None on failure.

    Example:
        path = fetch_pixabay_photo("Capitol building")
        # → "cache/stock/px_a3f7b1.jpg"  (or None if no key)
    """
    api_key = os.getenv("PIXABAY_API_KEY", "")
    if not api_key:
        return None

    params = urllib.parse.urlencode({
        "key": api_key,
        "q": query,
        "per_page": 15,
        "image_type": "photo",
        "orientation": "vertical",   # prefer tall photos for 9:16 video
        "min_width": 500,
    })
    url = f"{PIXABAY_API_URL}?{params}"

    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "ShortsEngine/0.1"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
    except Exception as err:
        print(f"  Pixabay search failed for '{query}': {err}")
        return None

    hits = data.get("hits", [])
    if not hits:
        return None

    # Pick the most portrait-shaped photo (highest height/width ratio).
    best = max(
        hits,
        key=lambda h: h.get("imageHeight", 1) / max(h.get("imageWidth", 1), 1),
    )

    # Use a "px:" prefix in the cache key so Pixabay results don't collide
    # with Pexels results for the same query.
    img_path, meta_path = _cache_path_for_query(f"px:{query}")

    if os.path.exists(img_path):
        return img_path

    os.makedirs(STOCK_CACHE_FOLDER, exist_ok=True)

    # largeImageURL is the full-size file; webformatURL is a safe fallback.
    download_url = best.get("largeImageURL") or best.get("webformatURL", "")
    if not download_url:
        return None

    try:
        req = urllib.request.Request(
            download_url,
            headers={"User-Agent": "ShortsEngine/0.1"},
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            with open(img_path, "wb") as f:
                f.write(resp.read())
    except Exception as err:
        print(f"  Pixabay photo download failed: {err}")
        return None

    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump({
            "id": best.get("id"),
            "user": best.get("user"),
            "pageURL": best.get("pageURL", ""),
            "query": query,
            "source": "pixabay",
            "licence": "Pixabay Licence — free for commercial use, no attribution required",
        }, f, indent=2)

    print(f"  Pixabay photo downloaded: {os.path.basename(img_path)} "
          f"(by {best.get('user', 'unknown')})")
    return img_path


def fetch_wikimedia_photo(query):
    """
    Search Wikimedia Commons for a free photo and download it.

    Wikimedia Commons is a library of freely licensed images. No API
    key is needed. We use it as a fallback when no Pexels key is set.

    The images are in the public domain or under Creative Commons
    licences — they are safe to use in videos, including commercially.

    Args:
        query (str): The search term, e.g. "aircraft wreckage debris".

    Returns:
        str or None: Local path to the downloaded image, or None if not found.
    """
    # Step 1: Search Wikimedia Commons for image files matching the query.
    params = urllib.parse.urlencode({
        "action": "query",
        "generator": "search",
        "gsrnamespace": "6",        # namespace 6 = File: (images)
        "gsrsearch": query,
        "gsrlimit": "15",
        "prop": "imageinfo",
        # Ask for a 1080px-wide thumbnail URL instead of the full-res file.
        # Wikimedia explicitly recommends this — full-res downloads get rate-limited.
        "iiprop": "url|thumburl|size|mime",
        "iiurlwidth": "1080",
        "format": "json",
    })
    url = f"{WIKIMEDIA_API_URL}?{params}"
    request = urllib.request.Request(url)
    # Wikimedia requires a User-Agent header.
    request.add_header("User-Agent",
                       "ShortsEngine/0.1 (personal news project; "
                       "contact: shortsengine@example.com)")

    try:
        with urllib.request.urlopen(request, timeout=15) as resp:
            data = json.loads(resp.read())
    except Exception as err:
        print(f"  Wikimedia search failed for '{query}': {err}")
        return None

    pages = data.get("query", {}).get("pages", {})
    if not pages:
        return None

    # Step 2: Filter to JPEG/PNG images and pick the most portrait-shaped.
    candidates = []
    for page in pages.values():
        info_list = page.get("imageinfo", [])
        if not info_list:
            continue
        info = info_list[0]
        mime = info.get("mime", "")
        if mime not in ("image/jpeg", "image/png"):
            continue
        w = info.get("width", 1)
        h = info.get("height", 1)
        # Skip tiny thumbnails.
        if w < 400 or h < 400:
            continue
        candidates.append({
            "url": info["url"],
            "thumburl": info.get("thumburl"),   # 1080px thumbnail, if the API returned one
            "width": w,
            "height": h,
        })

    if not candidates:
        return None

    # Sort candidates: portrait images first (taller than wide), then any image.
    portrait = [c for c in candidates if c["height"] >= c["width"]]
    ordered = portrait + [c for c in candidates if c["height"] < c["width"]]

    # Step 3: Download and cache the image, trying candidates in order.
    # If one URL is rate-limited (HTTP 429), skip it and try the next candidate.
    # Wikimedia sometimes throttles specific popular files.
    img_path, _ = _cache_path_for_query(f"wm:{query}")
    os.makedirs(STOCK_CACHE_FOLDER, exist_ok=True)

    for candidate_index, candidate in enumerate(ordered):
        # Prefer the 1080px thumbnail URL — Wikimedia explicitly recommends
        # this over full-resolution files to avoid rate limiting.
        thumb_url = candidate.get("thumburl") or candidate["url"]

        # Small delay between requests so we don't hammer Wikimedia.
        time.sleep(1.0)

        try:
            req = urllib.request.Request(thumb_url)
            req.add_header("User-Agent",
                           "ShortsEngine/0.1 (personal news project)")
            with urllib.request.urlopen(req, timeout=30) as resp:
                with open(img_path, "wb") as f:
                    f.write(resp.read())
            # Success — stop trying other candidates.
            print(f"  Wikimedia photo downloaded: {os.path.basename(img_path)} "
                  f"(for query: '{query}')")
            return img_path
        except Exception as err:
            err_str = str(err)
            if "429" in err_str:
                # This specific file is rate-limited; try the next candidate.
                print(f"  Wikimedia 429 on candidate {candidate_index + 1}, "
                      f"trying next...")
            else:
                print(f"  Wikimedia download failed: {err}")
                return None  # non-rate-limit errors are unlikely to clear

    print(f"  Wikimedia: all {len(ordered)} candidates rate-limited for '{query}'")
    return None


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

    # Try Pexels first (highest quality, requires a free API key).
    if key:
        img_path, meta_path = _cache_path_for_query(query)

        # Already downloaded from Pexels? Use the cached file.
        if os.path.exists(img_path) and os.path.exists(meta_path):
            return img_path

        os.makedirs(STOCK_CACHE_FOLDER, exist_ok=True)

        photos = _search_pexels(query, key)
        photo = _pick_best_photo(photos)

        if photo:
            # Pexels found something — download it and return.
            download_url = photo["src"].get("large", photo["src"].get("original", ""))
            if download_url:
                try:
                    urllib.request.urlretrieve(download_url, img_path)
                except Exception as err:
                    print(f"  Failed to download Pexels photo: {err}")
                    # Fall through to Pixabay below.
                else:
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
        else:
            print(f"  No Pexels result for: '{query}' — trying Pixabay...")

    # Fallback 1: Pixabay (free, same quality tier, requires PIXABAY_API_KEY).
    pixabay_result = fetch_pixabay_photo(query)
    if pixabay_result:
        return pixabay_result

    # Fallback 2: Wikimedia Commons (completely free, no key needed).
    return fetch_wikimedia_photo(query)

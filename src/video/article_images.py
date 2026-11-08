# =============================================================
# src/video/article_images.py — extract real photos from a news article
#
# When we write a script about a news article, the article itself
# contains editorial photos chosen by the journalist — photos of the
# actual event (crash site, emergency response, the people involved).
# Those photos are far more relevant than generic Wikimedia stock.
#
# This module fetches those images by:
#   1. Reading the source URL out of the script's ## Sources section.
#   2. Downloading the article page (same HTML we already fetched for
#      the script text — but here we look at <img> tags and og:image).
#   3. Filtering out logos/icons and downloading the best editorial photos.
#   4. Caching them so the same article is never fetched twice.
#
# Attribution: images are credited to the original outlet in credits.txt.
# These images are from news reporting — we use them briefly in an
# educational video summary (fair-use context). No claim of ownership.
#
# How it fits in:
#   render.py → _build_visual_frames() → get_article_images() (here)
# =============================================================

import hashlib
import html
import json
import os
import re
import urllib.parse
import urllib.request


ARTICLE_IMAGES_CACHE = os.path.join("cache", "article_images")


def _extract_source_url(script_path):
    """
    Read the first URL from the ## Sources section of a script file.

    Args:
        script_path (str): Path to the .md script file.

    Returns:
        str or None: The first source URL found, or None.
    """
    in_sources = False
    with open(script_path, "r", encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            if stripped == "## Sources":
                in_sources = True
                continue
            if in_sources and stripped.startswith("- ["):
                # Markdown link format: - [https://...](https://...)
                # Pull the URL out of the first set of parens.
                match = re.search(r'\]\((https?://[^\)]+)\)', stripped)
                if match:
                    return match.group(1)
    return None


def _fetch_html(url):
    """
    Download a web page and return its HTML as a string.

    Args:
        url (str): The page URL.

    Returns:
        str or None: HTML content, or None if the request failed.
    """
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml",
            },
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.read(500_000).decode("utf-8", errors="replace")
    except Exception as err:
        print(f"  Article fetch failed ({err})")
        return None


def _extract_image_urls(html_content, page_url):
    """
    Pull image URLs out of article HTML.

    Looks for:
      1. og:image meta tag — the main editorial photo chosen by the outlet.
      2. <img> tags that are likely editorial photos (not logos or icons).

    Args:
        html_content (str): The article HTML.
        page_url (str): The article URL (used to resolve relative URLs).

    Returns:
        list[str]: Absolute image URLs, deduplicated.
    """
    image_urls = []
    base = urllib.parse.urlparse(page_url)
    base_url = f"{base.scheme}://{base.netloc}"

    # 1. og:image — the main thumbnail chosen by the publisher.
    og_match = re.search(
        r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
        html_content, re.IGNORECASE
    )
    if not og_match:
        # Try the reversed attribute order.
        og_match = re.search(
            r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',
            html_content, re.IGNORECASE
        )
    if og_match:
        url = html.unescape(og_match.group(1).strip())
        if url.startswith("//"):
            url = "https:" + url
        elif url.startswith("/"):
            url = base_url + url
        if url.startswith("http"):
            image_urls.append(url)

    # 2. <img> tags — look for large editorial photos.
    img_pattern = re.compile(
        r'<img[^>]+(?:src|data-src)=["\']([^"\']+)["\']',
        re.IGNORECASE
    )
    for match in img_pattern.finditer(html_content):
        raw_url = html.unescape(match.group(1).strip())

        # Resolve relative URLs.
        if raw_url.startswith("//"):
            raw_url = "https:" + raw_url
        elif raw_url.startswith("/") and not raw_url.startswith("//"):
            raw_url = base_url + raw_url

        if not raw_url.startswith("http"):
            continue

        # Skip images that are almost certainly not editorial photos.
        lower = raw_url.lower()
        skip_keywords = [
            "logo", "icon", "avatar", "sprite", "button", "badge",
            "ad", "banner", "tracking", "pixel", "spacer", "placeholder",
            "1x1", ".gif", "data:image",
        ]
        if any(kw in lower for kw in skip_keywords):
            continue

        image_urls.append(raw_url)

    # Remove duplicates while keeping order.
    seen = set()
    unique = []
    for url in image_urls:
        # Strip query strings when de-duping (same image, different sizes).
        clean = url.split("?")[0]
        if clean not in seen:
            seen.add(clean)
            unique.append(url)

    return unique


def _download_image(url, dest_path):
    """
    Download one image file and save it to dest_path.

    Args:
        url (str): The image URL.
        dest_path (str): Where to save the file.

    Returns:
        bool: True if the download succeeded.
    """
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36"
                ),
            },
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            content_type = resp.headers.get("Content-Type", "")
            if "image" not in content_type:
                return False
            data = resp.read()

        # Minimum size filter — skip tiny images (icons, tracking pixels).
        if len(data) < 20_000:   # less than 20 KB → almost certainly not a photo
            return False

        with open(dest_path, "wb") as f:
            f.write(data)
        return True

    except Exception:
        return False


def _cache_path_for_url(image_url):
    """
    Return the local cache path for a given image URL.

    Args:
        image_url (str): The original image URL.

    Returns:
        str: Path like cache/article_images/abc123.jpg
    """
    fingerprint = hashlib.sha256(image_url.encode()).hexdigest()[:20]
    return os.path.join(ARTICLE_IMAGES_CACHE, f"{fingerprint}.jpg")


def get_article_images(script_path, max_images=8):
    """
    Download the editorial photos from the news article that the script
    was written about.

    Reads the source URL from the script's ## Sources section, fetches
    the article page, extracts images, and downloads up to max_images.

    Args:
        script_path (str): Path to the .md script file.
        max_images (int): Maximum number of images to download.

    Returns:
        list[str]: Local file paths to downloaded images, or [] on failure.
    """
    source_url = _extract_source_url(script_path)
    if not source_url:
        return []

    # Check if we already have cached images for this article.
    url_hash = hashlib.sha256(source_url.encode()).hexdigest()[:16]
    manifest_path = os.path.join(ARTICLE_IMAGES_CACHE, f"{url_hash}_manifest.json")

    if os.path.exists(manifest_path):
        with open(manifest_path, "r") as f:
            cached = json.load(f)
        # Only return paths that still exist on disk.
        valid = [p for p in cached if os.path.exists(p)]
        if valid:
            print(f"  Article images loaded from cache: {len(valid)} photos.")
            return valid

    print(f"  Fetching article images from: {source_url}")
    html_content = _fetch_html(source_url)
    if not html_content:
        return []

    image_urls = _extract_image_urls(html_content, source_url)
    if not image_urls:
        print("  No suitable images found in article.")
        return []

    os.makedirs(ARTICLE_IMAGES_CACHE, exist_ok=True)

    downloaded = []
    for url in image_urls[:max_images * 2]:   # try more than we need in case some fail
        dest = _cache_path_for_url(url)

        if os.path.exists(dest):
            downloaded.append(dest)
        else:
            if _download_image(url, dest):
                downloaded.append(dest)

        if len(downloaded) >= max_images:
            break

    if downloaded:
        print(f"  Article images downloaded: {len(downloaded)} photos.")
        with open(manifest_path, "w") as f:
            json.dump(downloaded, f)

    return downloaded

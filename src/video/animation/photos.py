# =============================================================
# src/video/animation/photos.py — real, freely licensed photos
#
# When a sentence names a specific person, place or organization
# ("the FAA", "Scott Bessent"), a real photo of it is far better
# than a generic stock clip or a circle with initials.
#
# Where photos come from (free, no API key):
#   1. Wikipedia tells us the main image of the subject's article.
#   2. Wikimedia Commons tells us that image's licence and author.
#   3. We ONLY keep it if the licence allows commercial reuse:
#      public domain, CC0, CC BY or CC BY-SA. Anything else (fair-use
#      logos, government licences we haven't checked, non-commercial
#      licences) is skipped. The author and licence are saved so the
#      video's credits list them (CC BY needs attribution).
#
# Only real photographs (JPEG) are used. Logos and seals (SVG/PNG)
# are skipped, because a logo blown up full screen looks wrong.
#
# How it fits in:
#   renderer.py → find_photos_for_scenes() (here) → PhotoBackground
# =============================================================

import hashlib
import html
import json
import os
import re
import urllib.parse
import urllib.request

from PIL import Image, ImageEnhance

from src.video.animation.drawing import VIDEO_HEIGHT, VIDEO_WIDTH

PHOTO_CACHE = os.path.join("cache", "photos")
WIKIPEDIA_API = "https://en.wikipedia.org/w/api.php"
COMMONS_API = "https://commons.wikimedia.org/w/api.php"
REQUEST_HEADERS = {"User-Agent": "ShortsEngine/0.1 (educational video tool)"}

# Licence names (as Commons writes them) that allow commercial reuse.
# A licence is accepted if its name starts with one of these, e.g.
# "Public domain", "PD-USGov", "CC0", "CC BY 4.0", "CC BY-SA 3.0".
ALLOWED_LICENCE_STARTS = ("public domain", "pd", "cc0", "cc by ", "cc by-sa")

# Photos are drawn this much bigger than the screen, so there is room
# to slowly zoom and pan ("Ken Burns" effect) without showing an edge.
PHOTO_OVERSIZE = 1.15


def licence_is_allowed(licence_name):
    """
    Check whether a Commons licence allows us to use the photo commercially.

    Args:
        licence_name (str): e.g. "CC BY-SA 4.0", "Public domain", "CC BY-NC 2.0".

    Returns:
        bool: True for public domain, CC0, CC BY and CC BY-SA.
              False for non-commercial (NC), no-derivatives (ND) and
              anything we don't recognise.

    Example:
        licence_is_allowed("CC BY-SA 3.0")   # → True
        licence_is_allowed("CC BY-NC 2.0")   # → False
    """
    name = licence_name.strip().lower()
    # "NC" (non-commercial) and "ND" (no changes allowed) rule a photo out,
    # because we sell ads on the videos and we crop and zoom every photo.
    words = name.replace("-", " ").split()
    if "nc" in words or "nd" in words:
        return False
    return name.startswith(ALLOWED_LICENCE_STARTS)


def plain_text(html_text):
    """
    Remove HTML tags from a Commons field, e.g. the author's name.

    Args:
        html_text (str): Text that may contain tags like <a href=...>.

    Returns:
        str: The visible text only, e.g. "Gage Skidmore".
    """
    without_tags = re.sub(r"<[^>]+>", "", html_text or "")
    return html.unescape(without_tags).strip()


def fetch_json(url, params):
    """
    Call a Wikimedia API and return its JSON answer.

    Args:
        url (str): The API address.
        params (dict): The query parameters.

    Returns:
        dict: The parsed answer ({} if the request failed).
    """
    full_url = url + "?" + urllib.parse.urlencode(params)
    try:
        request = urllib.request.Request(full_url, headers=REQUEST_HEADERS)
        with urllib.request.urlopen(request, timeout=15) as response:
            return json.loads(response.read())
    except Exception as error:
        print(f"  Wikimedia request failed: {error}")
        return {}


def find_article_image(subject):
    """
    Find the main image file of a subject's Wikipedia article.

    Args:
        subject (str): A person, place or organization, e.g. "Scott Bessent".

    Returns:
        str or None: The image's file name on Commons, or None.
    """
    data = fetch_json(WIKIPEDIA_API, {
        "action": "query", "titles": subject, "prop": "pageimages",
        "piprop": "name", "redirects": 1, "format": "json",
    })
    pages = data.get("query", {}).get("pages", {})
    for page in pages.values():
        if page.get("pageimage"):
            return page["pageimage"]
    return None


def find_commons_details(file_name):
    """
    Look up a Commons image's download address, licence and author.

    Args:
        file_name (str): e.g. "Official_portrait_of_Scott_Bessent.jpg".

    Returns:
        dict or None: {"url", "licence", "licence_url", "author", "page_url"},
                      or None if the file isn't on Commons (for example a
                      fair-use logo kept only on English Wikipedia).
    """
    data = fetch_json(COMMONS_API, {
        "action": "query", "titles": "File:" + file_name, "prop": "imageinfo",
        "iiprop": "url|extmetadata", "iiurlwidth": 1400, "format": "json",
    })
    pages = data.get("query", {}).get("pages", {})
    for page in pages.values():
        if "missing" in page or not page.get("imageinfo"):
            return None
        info = page["imageinfo"][0]
        metadata = info.get("extmetadata", {})
        return {
            "url": info.get("thumburl") or info.get("url"),
            "licence": plain_text(metadata.get("LicenseShortName", {}).get("value", "")),
            "licence_url": metadata.get("LicenseUrl", {}).get("value", ""),
            "author": plain_text(metadata.get("Artist", {}).get("value", "")) or "Unknown",
            "page_url": info.get("descriptionurl", ""),
        }
    return None


def photo_cache_paths(subject):
    """
    Work out where a subject's photo and its details are cached.

    Args:
        subject (str): e.g. "Federal Aviation Administration".

    Returns:
        tuple[str, str]: (image path, details .json path)
    """
    fingerprint = hashlib.sha256(subject.lower().encode()).hexdigest()[:16]
    return (os.path.join(PHOTO_CACHE, f"{fingerprint}.jpg"),
            os.path.join(PHOTO_CACHE, f"{fingerprint}.json"))


def find_photo(subject):
    """
    Get a freely licensed photo of a subject, downloading it once.

    Args:
        subject (str): A person, place or organization named in the script.

    Returns:
        dict or None: {"path": image file, "credit": {...}} or None if no
                      usable photo exists. "credit" has "source", "title",
                      "author", "licence", "licence_url" and "page_url".

    Example:
        photo = find_photo("Scott Bessent")
        photo["credit"]["licence"]   # → "Public domain"
    """
    subject = subject.strip()
    if not subject:
        return None
    image_path, details_path = photo_cache_paths(subject)

    # A cached answer, including "no usable photo", is reused for free.
    if os.path.exists(details_path):
        with open(details_path, "r", encoding="utf-8") as f:
            saved = json.load(f)
        if saved.get("usable") and os.path.exists(image_path):
            return {"path": image_path, "credit": saved["credit"]}
        return None

    os.makedirs(PHOTO_CACHE, exist_ok=True)
    result = download_usable_photo(subject, image_path)
    with open(details_path, "w", encoding="utf-8") as f:
        json.dump({"usable": result is not None,
                   "credit": result["credit"] if result else None}, f, indent=2)
    return result


def download_usable_photo(subject, image_path):
    """
    Find, check and download a subject's photo (no cache involved).

    Args:
        subject (str): The subject to look up.
        image_path (str): Where to save the JPEG.

    Returns:
        dict or None: {"path", "credit"} or None if nothing usable was found.
    """
    file_name = find_article_image(subject)
    if not file_name:
        return None
    if not file_name.lower().endswith((".jpg", ".jpeg")):
        print(f"  Photo for '{subject}' is a logo or drawing ({file_name}) — skipped.")
        return None

    details = find_commons_details(file_name)
    if details is None or not details["url"]:
        print(f"  Photo for '{subject}' is not on Wikimedia Commons — skipped.")
        return None
    if not licence_is_allowed(details["licence"]):
        print(f"  Photo for '{subject}' has licence '{details['licence']}' — skipped.")
        return None

    try:
        request = urllib.request.Request(details["url"], headers=REQUEST_HEADERS)
        with urllib.request.urlopen(request, timeout=30) as response:
            with open(image_path, "wb") as f:
                f.write(response.read())
        Image.open(image_path).verify()   # make sure it is a real image
    except Exception as error:
        print(f"  Could not download the photo for '{subject}': {error}")
        return None

    credit = {
        "source": "Wikimedia Commons",
        "title": file_name,
        "author": details["author"],
        "licence": details["licence"],
        "licence_url": details["licence_url"],
        "page_url": details["page_url"],
    }
    print(f"  Photo for '{subject}': {details['licence']} by {details['author'][:40]}")
    return {"path": image_path, "credit": credit}


def find_photos_for_scenes(scenes):
    """
    Look up a photo for every scene whose storyboard names a subject.

    Each scene gains "photo_path" (a file or None) and "photo_credit".

    Args:
        scenes (list[dict]): Storyboard scenes; a scene may have "photo",
                             the subject to show.

    Returns:
        int: How many scenes got a photo.
    """
    found = 0
    for scene in scenes:
        scene["photo_path"] = None
        scene["photo_credit"] = None
        subject = scene.get("photo", "")
        if not subject:
            continue
        photo = find_photo(subject)
        if photo is not None:
            scene["photo_path"] = photo["path"]
            scene["photo_credit"] = photo["credit"]
            found += 1
    return found


class PhotoBackground:
    """
    Shows a still photo full screen with slow zoom and pan, so it is never still.

    Example:
        background = PhotoBackground("cache/photos/abc.jpg", brightness=0.6, direction=1)
        frame = background.frame_at(0.8, 2.5)   # 0.8 s into a 2.5 s scene
    """

    def __init__(self, photo_path, brightness, direction=1):
        """
        Load the photo once and prepare it at a size bigger than the screen.

        Args:
            photo_path (str): The JPEG file.
            brightness (float): 1.0 = as is, 0.6 = darkened for readable text.
            direction (int): 1 zooms in and pans right, -1 zooms out and
                             pans left, so back-to-back photos move differently.
        """
        picture = Image.open(photo_path).convert("RGB")
        # Scale so the photo covers the whole (oversized) frame.
        target_width = int(VIDEO_WIDTH * PHOTO_OVERSIZE)
        target_height = int(VIDEO_HEIGHT * PHOTO_OVERSIZE)
        scale = max(target_width / picture.width, target_height / picture.height)
        picture = picture.resize((int(picture.width * scale) + 1, int(picture.height * scale) + 1),
                                 Image.BILINEAR)
        # Keep the top-middle part: faces in portraits sit near the top.
        left = (picture.width - target_width) // 2
        top = min((picture.height - target_height) // 4, picture.height - target_height)
        picture = picture.crop((left, top, left + target_width, top + target_height))
        self.picture = ImageEnhance.Brightness(picture).enhance(brightness)
        self.direction = direction

    def frame_at(self, time_in_scene, duration):
        """
        Get the background picture for one moment of the scene.

        Args:
            time_in_scene (float): Seconds since the scene began.
            duration (float): The scene's length in seconds.

        Returns:
            PIL.Image: A 1080×1920 frame.
        """
        amount = min(1.0, max(0.0, time_in_scene / max(duration, 0.1)))
        if self.direction < 0:
            amount = 1.0 - amount
        # Zoom: show a window that shrinks from 100% to 87% of the photo.
        window_width = self.picture.width * (1.0 - 0.13 * amount)
        window_height = window_width * VIDEO_HEIGHT / VIDEO_WIDTH
        # Pan: the window drifts sideways as it zooms.
        spare_x = self.picture.width - window_width
        spare_y = self.picture.height - window_height
        left = spare_x * (0.3 + 0.4 * amount)
        top = spare_y * 0.3
        box = (int(left), int(top), int(left + window_width), int(top + window_height))
        return self.picture.crop(box).resize((VIDEO_WIDTH, VIDEO_HEIGHT), Image.BILINEAR)

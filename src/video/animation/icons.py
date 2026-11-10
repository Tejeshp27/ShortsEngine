# =============================================================
# src/video/animation/icons.py — free emoji icons used as artwork
#
# The animated scenes need pictures: a courthouse, a ballot box,
# a flag. Instead of paying for an icon library, we use Google's
# Noto Emoji set, which has thousands of polished icons and an
# open licence (Apache 2.0) that allows commercial use.
#
# The AI storyboard picks an emoji character (for example "🏛️").
# This file turns that character into the matching image file
# name, downloads the image once, and keeps it in cache/icons/
# so it is never downloaded twice.
#
# How it fits in:
#   scenes.py → get_icon("🏛️", 300) → a picture ready to paste
# =============================================================

import os
import urllib.request

from PIL import Image


# Where downloaded icons are kept between runs.
ICON_CACHE_FOLDER = os.path.join("cache", "icons")

# Google's Noto Emoji repository on GitHub. The "3D" set looks the most
# polished; the flat "2D" set is the backup if an icon is missing there.
NOTO_EMOJI_BASE_URL = "https://raw.githubusercontent.com/googlefonts/noto-emoji/main"
NOTO_EMOJI_FOLDERS = ["3D/png/512", "2D/png/512"]

# Icons already loaded this run. Key: emoji character. Value: the image,
# or None if it could not be found (so we don't retry on every frame).
_loaded_icons = {}

# Icons already resized this run. Key: (emoji, size). Value: the image.
_resized_icons = {}


def emoji_to_filename(emoji):
    """
    Turn an emoji character into the file name Noto Emoji uses for it.

    Every emoji is made of one or more numbered "code points". Noto names
    each image after those numbers written in hexadecimal.

    Args:
        emoji (str): One emoji, e.g. "🏛️" or "🇺🇸".

    Returns:
        str: A file name such as "emoji_u1f3db.png", or "" for empty input.

    Example:
        emoji_to_filename("🏛️")   # → "emoji_u1f3db.png"
        emoji_to_filename("🇺🇸")   # → "emoji_u1f1fa_1f1f8.png"
    """
    parts = []
    for character in emoji.strip():
        code_point = ord(character)
        # 0xFE0F is an invisible "show this as a colour emoji" marker.
        # Noto leaves it out of its file names, so we skip it too.
        if code_point == 0xFE0F:
            continue
        parts.append(f"{code_point:04x}")

    if not parts:
        return ""
    return "emoji_u" + "_".join(parts) + ".png"


def _download_icon(filename, destination_path):
    """
    Download one icon image from the Noto Emoji repository.

    Args:
        filename (str): e.g. "emoji_u1f3db.png".
        destination_path (str): Where to save the file.

    Returns:
        bool: True if the icon was downloaded and saved.
    """
    for folder in NOTO_EMOJI_FOLDERS:
        url = f"{NOTO_EMOJI_BASE_URL}/{folder}/{filename}"
        try:
            request = urllib.request.Request(
                url, headers={"User-Agent": "ShortsEngine/0.1"}
            )
            with urllib.request.urlopen(request, timeout=15) as response:
                data = response.read()
        except Exception:
            # Not in this folder (or no internet) — try the next folder.
            continue

        os.makedirs(os.path.dirname(destination_path), exist_ok=True)
        with open(destination_path, "wb") as f:
            f.write(data)
        return True

    return False


def _load_icon(emoji):
    """
    Get the full-size (512 pixel) picture for an emoji.

    Looks in memory first, then in cache/icons/, then downloads it.

    Args:
        emoji (str): One emoji character.

    Returns:
        PIL.Image or None: The icon, or None if it is not available.
    """
    if emoji in _loaded_icons:
        return _loaded_icons[emoji]

    icon = None
    filename = emoji_to_filename(emoji)

    if filename:
        path = os.path.join(ICON_CACHE_FOLDER, filename)
        if not os.path.exists(path):
            _download_icon(filename, path)
        if os.path.exists(path):
            try:
                icon = Image.open(path).convert("RGBA")
            except OSError:
                icon = None

    # Remember the result, even a failure, so each icon is looked up once.
    _loaded_icons[emoji] = icon
    return icon


def get_icon(emoji, size):
    """
    Get an emoji icon as a square picture of the requested size.

    Scenes call this on every frame while an icon is growing or
    bouncing, so resized copies are remembered to keep rendering fast.

    Args:
        emoji (str): One emoji character, e.g. "🗳️".
        size (int): The width and height wanted, in pixels.

    Returns:
        PIL.Image or None: The icon, or None if the emoji is empty or
                           its picture could not be found. Scenes simply
                           skip the icon when they get None.

    Example:
        icon = get_icon("🏛️", 300)
    """
    if not emoji or size < 2:
        return None

    size = int(size)
    cache_key = (emoji, size)
    if cache_key in _resized_icons:
        return _resized_icons[cache_key]

    icon = _load_icon(emoji)
    if icon is None:
        return None

    resized = icon.resize((size, size), Image.BILINEAR)
    _resized_icons[cache_key] = resized
    return resized

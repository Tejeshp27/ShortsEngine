# =============================================================
# src/video/ai_image.py — generate custom images with Stability AI
#
# When stock photo services (Pexels, Pixabay, Wikimedia) can't find
# a good match for a visual query, this module asks Stability AI to
# generate a custom image instead.
#
# This means every segment can have a relevant, on-brand visual —
# even for abstract concepts like "inflation" or "senate vote" that
# stock photo searches struggle with.
#
# Cost: ~$0.03 per image (Stable Image Core model).
# Requires STABILITY_API_KEY in .env.
#
# Images are cached by query so the same search never costs twice.
#
# How it fits in:
#   stock.py → fetch_stock_photo() → generate_ai_image() (here, last resort)
# =============================================================

import hashlib
import json
import os
import urllib.request


STABILITY_API_URL = "https://api.stability.ai/v2beta/stable-image/generate/core"
AI_IMAGE_CACHE = os.path.join("cache", "ai_images")


def generate_ai_image(query):
    """
    Ask Stability AI to generate an image matching the query.

    Uses the "Stable Image Core" model, which costs about $0.03 per image
    and produces photorealistic results. We ask for 9:16 (portrait) aspect
    ratio to fit the vertical video frame.

    The result is cached as a .jpg file so the same query is never charged
    twice across multiple renders.

    Args:
        query (str): A short description of what the image should show,
                     e.g. "US Capitol building at night, dramatic lighting".

    Returns:
        str or None: Local path to the generated .jpg image, or None if
                     the key is missing or the request fails.

    Example:
        path = generate_ai_image("US Senate chamber vote in progress")
        # → "cache/ai_images/a3f7b1c2.jpg"  (or None if no key)
    """
    api_key = os.getenv("STABILITY_API_KEY", "")
    if not api_key:
        return None

    # Build a cache path based on the query so the same prompt reuses
    # the same file on the next render.
    img_path = _cache_path(query)
    if os.path.exists(img_path):
        print(f"  AI image loaded from cache: {os.path.basename(img_path)}")
        return img_path

    os.makedirs(AI_IMAGE_CACHE, exist_ok=True)

    # We add a negative prompt to steer away from common AI artifacts
    # and watermarked stock-photo styles.
    full_prompt = _build_prompt(query)
    negative_prompt = (
        "blurry, low quality, watermark, text overlay, logo, "
        "cartoon, anime, illustration, painting, oversaturated"
    )

    # Stability AI's v2beta endpoint accepts multipart/form-data.
    # We build the request body manually using Python's built-in tools.
    boundary = "----ShortsEngineBoundary"
    body = _build_multipart_body(boundary, {
        "prompt": full_prompt,
        "negative_prompt": negative_prompt,
        "aspect_ratio": "9:16",    # portrait orientation for 9:16 video
        "output_format": "jpeg",
    })

    req = urllib.request.Request(
        STABILITY_API_URL,
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            # Accept: image/* tells Stability to return raw image bytes
            # instead of a JSON wrapper — simpler to handle.
            "Accept": "image/*",
        },
        method="POST",
    )

    print(f"  Generating AI image for: '{query[:60]}...' " if len(query) > 60
          else f"  Generating AI image for: '{query}'")

    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            image_bytes = resp.read()
    except urllib.request.HTTPError as err:
        # Read the error body so we can show a helpful message.
        error_body = err.read().decode("utf-8", errors="replace")
        print(f"  Stability AI failed (HTTP {err.code}): {error_body[:200]}")
        return None
    except Exception as err:
        print(f"  Stability AI request failed: {err}")
        return None

    # A valid JPEG starts with the bytes FF D8 (the JPEG magic number).
    # If we got something else, the API returned an error in disguise.
    if len(image_bytes) < 1000 or image_bytes[:2] != b"\xff\xd8":
        print("  Stability AI returned an unexpected response (not a JPEG).")
        return None

    with open(img_path, "wb") as f:
        f.write(image_bytes)

    print(f"  AI image generated and cached: {os.path.basename(img_path)}")
    return img_path


def _build_prompt(query):
    """
    Turn a short visual query into a detailed Stability AI prompt.

    We add photographic style cues so the output looks like a
    professional news broadcast background rather than generic AI art.

    Args:
        query (str): The original search query, e.g. "Senate vote in progress".

    Returns:
        str: An expanded prompt suitable for Stability AI.
    """
    return (
        f"{query}, "
        "photorealistic, news broadcast quality, professional photography, "
        "sharp focus, cinematic lighting, 4K, high detail"
    )


def _cache_path(query):
    """
    Return the file path where this query's image should be cached.

    Args:
        query (str): The search query.

    Returns:
        str: Full file path, e.g. "cache/ai_images/a3f7b1c2.jpg".
    """
    fingerprint = hashlib.sha256(f"stability:{query}".lower().encode()).hexdigest()[:20]
    return os.path.join(AI_IMAGE_CACHE, f"{fingerprint}.jpg")


def _build_multipart_body(boundary, fields):
    """
    Manually build a multipart/form-data request body.

    Python's standard library doesn't include a multipart builder,
    so we assemble the bytes ourselves. Each field is a named text part.

    Args:
        boundary (str): The boundary string separating parts.
        fields (dict): Key-value pairs to include as form fields.

    Returns:
        bytes: The complete request body ready to send.
    """
    lines = []
    for name, value in fields.items():
        lines.append(f"--{boundary}".encode())
        lines.append(f'Content-Disposition: form-data; name="{name}"'.encode())
        lines.append(b"")
        lines.append(value.encode("utf-8"))
    lines.append(f"--{boundary}--".encode())
    # Join all parts with CRLF (the standard line ending for MIME).
    return b"\r\n".join(lines)

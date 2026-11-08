# =============================================================
# src/video/frames.py — generate PNG frames for the video
#
# Instead of relying on FFmpeg's optional text-rendering filters
# (drawtext, subtitles) which may not be compiled in, we use
# Python's Pillow library to draw each frame ourselves. This gives
# us full control over the design and works with any FFmpeg build.
#
# For efficiency, we generate ONE image per caption group (not one
# per video frame). FFmpeg then holds each image for the right
# duration using an "ffconcat" script. A 100-second video with
# 85 caption groups produces 85 PNGs, not 3000.
#
# Phase V.6 update: each frame can now have a background image
# (stock photo or chart PNG). Pass a list of image paths via the
# visual_frames parameter. If an entry is None, the solid background
# colour is used instead.
#
# Output files:
#   cache/frames/<fingerprint>/frame_001.png
#   cache/frames/<fingerprint>/frame_002.png
#   ...
#   cache/frames/<fingerprint>/concat.txt
# =============================================================

import os
import textwrap

from PIL import Image, ImageDraw, ImageFont

from src.video.captions import _group_words, WORDS_PER_CAPTION

# Video dimensions for 9:16 portrait (TikTok / Instagram Reels).
VIDEO_WIDTH = 1080
VIDEO_HEIGHT = 1920

FRAMES_FOLDER = os.path.join("cache", "frames")


def _load_font(size, bold=False):
    """
    Load a system font at the given pixel size, with a plain fallback.

    Args:
        size (int): Font size in pixels.
        bold (bool): Whether to try loading a bold variant.

    Returns:
        PIL.ImageFont: A font object ready to use with ImageDraw.
    """
    # Try a few common system font paths. If none are found, Pillow
    # falls back to its built-in bitmap font (ugly but always works).
    candidates = []
    if bold:
        candidates = [
            "/System/Library/Fonts/Helvetica.ttc",           # macOS
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",  # Linux
        ]
    else:
        candidates = [
            "/System/Library/Fonts/Helvetica.ttc",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        ]

    for path in candidates:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue

    # PIL's default font is small and fixed-size but always available.
    return ImageFont.load_default()


def _wrap_headline(draw, text, font, max_width):
    """
    Break a headline into lines that fit within max_width pixels.

    Args:
        draw (ImageDraw): Used to measure text width.
        text (str): The headline.
        font (PIL.ImageFont): The font to use for measuring.
        max_width (int): The maximum line width in pixels.

    Returns:
        list[str]: Lines of text, each no wider than max_width.
    """
    words = text.split()
    lines = []
    current_line = ""

    for word in words:
        trial = (current_line + " " + word).strip()
        bbox = draw.textbbox((0, 0), trial, font=font)
        line_width = bbox[2] - bbox[0]
        if line_width <= max_width:
            current_line = trial
        else:
            if current_line:
                lines.append(current_line)
            current_line = word

    if current_line:
        lines.append(current_line)

    return lines


def _apply_background_image(image, bg_image_path):
    """
    Replace the solid background with a stock photo or chart image.

    The source image is scaled to fill the full 1080×1920 frame
    (cropping the sides if needed). A dark semi-transparent overlay
    is then applied so white text remains readable.

    Args:
        image (PIL.Image): The RGBA canvas to modify in place.
        bg_image_path (str): Local path to the .jpg or .png image.
    """
    try:
        source = Image.open(bg_image_path).convert("RGBA")
    except (OSError, IOError):
        return  # Can't open the file — keep the solid background.

    # Scale to fill the frame while preserving aspect ratio (cover mode).
    src_w, src_h = source.size
    scale = max(VIDEO_WIDTH / src_w, VIDEO_HEIGHT / src_h)
    new_w = int(src_w * scale)
    new_h = int(src_h * scale)
    source = source.resize((new_w, new_h), Image.LANCZOS)

    # Crop to exactly 1080×1920, centred.
    left = (new_w - VIDEO_WIDTH) // 2
    top = (new_h - VIDEO_HEIGHT) // 2
    source = source.crop((left, top, left + VIDEO_WIDTH, top + VIDEO_HEIGHT))

    # Paste the photo onto the canvas.
    image.paste(source, (0, 0))

    # Dark overlay so text is always legible on top of any photo.
    overlay = Image.new("RGBA", (VIDEO_WIDTH, VIDEO_HEIGHT), (0, 0, 0, 160))
    image.paste(overlay, (0, 0), mask=overlay)


def _draw_frame(draw, background_color, headline, caption_text,
                headline_font, caption_font):
    """
    Draw one video frame on the given ImageDraw canvas.

    Layout (from top):
      - Coloured background (already filled by the caller)
      - Headline text block centred in the top third
      - Caption text centred near the bottom

    Args:
        draw (ImageDraw): The canvas to draw on.
        background_color (tuple): RGB tuple, e.g. (26, 26, 46).
        headline (str): The story headline.
        caption_text (str): The current 3-word caption.
        headline_font (PIL.ImageFont): Font for the headline.
        caption_font (PIL.ImageFont): Font for captions (larger, bold).
    """
    padding = 60   # horizontal padding in pixels

    # --- Headline block ---
    lines = _wrap_headline(draw, headline, headline_font,
                           VIDEO_WIDTH - padding * 2)
    line_height = headline_font.size + 12
    y = 180

    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=headline_font)
        text_width = bbox[2] - bbox[0]
        x = (VIDEO_WIDTH - text_width) // 2
        # Shadow
        draw.text((x + 3, y + 3), line, font=headline_font,
                  fill=(0, 0, 0, 160))
        draw.text((x, y), line, font=headline_font, fill=(255, 255, 255))
        y += line_height

    # --- Caption text near the bottom ---
    if caption_text:
        bbox = draw.textbbox((0, 0), caption_text, font=caption_font)
        text_width = bbox[2] - bbox[0]
        x = (VIDEO_WIDTH - text_width) // 2
        caption_y = VIDEO_HEIGHT - 300

        # Dark rounded-rectangle background behind caption for legibility.
        pad = 20
        draw.rectangle(
            [x - pad, caption_y - pad,
             x + text_width + pad, caption_y + caption_font.size + pad],
            fill=(0, 0, 0, 180),
        )
        # Shadow
        draw.text((x + 3, caption_y + 3), caption_text, font=caption_font,
                  fill=(0, 0, 0, 160))
        draw.text((x, caption_y), caption_text, font=caption_font,
                  fill=(255, 255, 255))


def build_frames(word_timings, story_title, background_color_hex,
                 output_folder, font_size=90, visual_frames=None):
    """
    Generate PNG frames and an FFmpeg concat script for the video.

    One PNG is created per caption group. The concat script tells
    FFmpeg how long to display each image.

    Phase V.6: if visual_frames is provided, each frame uses the
    matching image (stock photo or chart) as its background instead
    of the solid colour.

    Args:
        word_timings (list[dict]): Word timing data from tts.py.
        story_title (str): The headline to show at the top of every frame.
        background_color_hex (str): Fallback solid background colour,
                                    e.g. "0x1a1a2e". Used when no visual
                                    image is available for a frame.
        output_folder (str): Where to save the frame PNGs and concat.txt.
        font_size (int): Caption font size in pixels.
        visual_frames (list[str or None] or None): One entry per caption
            group. Each entry is either a local image path (stock photo
            or chart PNG) or None (use solid background). If this list
            is shorter than the number of caption groups, the last entry
            is repeated.

    Returns:
        str: Path to the FFmpeg concat script (concat.txt).
    """
    os.makedirs(output_folder, exist_ok=True)

    # Convert "0x1a1a2e" → (26, 26, 46) so Pillow can use it.
    hex_str = background_color_hex.replace("0x", "").replace("#", "")
    bg_color = tuple(int(hex_str[i:i+2], 16) for i in (0, 2, 4))

    caption_groups = _group_words(word_timings, WORDS_PER_CAPTION)
    if not caption_groups:
        # No captions — generate one silent background frame.
        caption_groups = [{"text": "", "start": 0.0, "end": 1.0}]

    # Also add one final blank frame so the video doesn't cut mid-word.
    last_end = caption_groups[-1]["end"]
    caption_groups.append({"text": "", "start": last_end, "end": last_end + 0.5})

    headline_font = _load_font(50)
    caption_font = _load_font(font_size, bold=True)

    concat_lines = ["ffconcat version 1.0"]
    frame_paths = []

    for index, group in enumerate(caption_groups):
        # Create a new RGBA image for each frame.
        # RGBA lets us draw semi-transparent rectangles (caption background).
        image = Image.new("RGBA", (VIDEO_WIDTH, VIDEO_HEIGHT), (*bg_color, 255))

        # Apply background image (stock photo or chart) if one is available.
        if visual_frames:
            # Clamp the index so we don't go out of bounds.
            vf_index = min(index, len(visual_frames) - 1)
            bg_image_path = visual_frames[vf_index]
            if bg_image_path and os.path.exists(bg_image_path):
                _apply_background_image(image, bg_image_path)

        draw = ImageDraw.Draw(image)
        _draw_frame(draw, bg_color, story_title, group["text"],
                    headline_font, caption_font)

        # Save as PNG (lossless, FFmpeg handles final compression).
        frame_path = os.path.abspath(
            os.path.join(output_folder, f"frame_{index:04d}.png")
        )

        # Convert RGBA → RGB before saving as PNG for FFmpeg compatibility.
        image.convert("RGB").save(frame_path)
        frame_paths.append(frame_path)

        duration = max(group["end"] - group["start"], 0.1)
        # Use absolute paths in the concat file so FFmpeg finds the frames
        # regardless of the working directory.
        concat_lines.append(f"file '{frame_path}'")
        concat_lines.append(f"duration {duration:.3f}")

    # FFmpeg needs the last file repeated to avoid a 0-duration final frame.
    if frame_paths:
        concat_lines.append(f"file '{frame_paths[-1]}'")

    concat_path = os.path.join(output_folder, "concat.txt")
    with open(concat_path, "w", encoding="utf-8") as f:
        f.write("\n".join(concat_lines))

    return concat_path

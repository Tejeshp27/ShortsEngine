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


# In-memory cache so each background image is scaled only once per render.
# Key: file path. Value: (pre_scaled_image, base_w, base_h)
_bg_image_cache = {}


def _clear_bg_cache():
    """Clear the in-memory background image cache between render jobs."""
    _bg_image_cache.clear()


def _apply_background_image(image, bg_image_path,
                              frame_in_segment=0, total_in_segment=1,
                              segment_index=0):
    """
    Replace the solid background with an image, animated with a Ken Burns effect.

    Ken Burns is the classic documentary technique of slowly panning and
    zooming across a still photo so it feels like video. Each segment
    (group of frames showing the same photo) gets a subtle motion:
      - Even segments: zoom in left-to-right
      - Odd segments:  zoom in right-to-left
      - Every 3rd:     zoom in top-to-bottom
      - Every 4th:     zoom in bottom-to-top

    Args:
        image (PIL.Image): The RGBA canvas to modify in place.
        bg_image_path (str): Local path to the background .jpg or .png.
        frame_in_segment (int): This frame's position within its segment (0-based).
        total_in_segment (int): Total frames that share this background image.
        segment_index (int): Index of this segment in the video (for direction).
    """
    # Load and pre-scale the image at max zoom (once per unique path).
    # Pre-scaling to 110% of fill size gives us room to pan without resizing
    # on every frame, keeping rendering fast.
    max_zoom = 1.10   # 10% — subtle motion, not distracting

    if bg_image_path not in _bg_image_cache:
        try:
            source = Image.open(bg_image_path).convert("RGBA")
        except (OSError, IOError):
            return  # Can't open — keep solid background.

        src_w, src_h = source.size
        # Scale to cover the frame at max zoom so we always have room to move.
        base_scale = max(VIDEO_WIDTH / src_w, VIDEO_HEIGHT / src_h) * max_zoom
        new_w = int(src_w * base_scale)
        new_h = int(src_h * base_scale)
        scaled = source.resize((new_w, new_h), Image.LANCZOS)
        _bg_image_cache[bg_image_path] = (scaled, new_w, new_h)

    cache_entry = _bg_image_cache.get(bg_image_path)
    if cache_entry is None:
        return

    scaled, pre_w, pre_h = cache_entry

    # Progress through the segment: 0.0 at the first frame, 1.0 at the last.
    t = frame_in_segment / max(1, total_in_segment - 1)

    # The crop window shrinks as we zoom in: at t=0 we see max area, at t=1 less.
    # zoom_factor tells us how zoomed in we currently are (1.0 = no zoom, 1.08 = 8%).
    zoom_start = 1.0
    zoom_end = max_zoom * 0.9   # zoom to about 1.0 × max_zoom * 0.9 ≈ 9%
    zoom = zoom_start + (zoom_end - zoom_start) * t

    # At this zoom level, the crop window in the pre-scaled image covers:
    crop_w = int(VIDEO_WIDTH * max_zoom / zoom)
    crop_h = int(VIDEO_HEIGHT * max_zoom / zoom)
    crop_w = min(crop_w, pre_w)
    crop_h = min(crop_h, pre_h)

    # Maximum pan travel for this image at this crop size.
    max_x = max(0, pre_w - crop_w)
    max_y = max(0, pre_h - crop_h)

    # Choose pan direction based on segment number for visual variety.
    direction = segment_index % 4
    if direction == 0:   # left → right
        x = int(max_x * t)
        y = max_y // 2
    elif direction == 1: # right → left
        x = int(max_x * (1.0 - t))
        y = max_y // 2
    elif direction == 2: # top → bottom
        x = max_x // 2
        y = int(max_y * t)
    else:                # bottom → top
        x = max_x // 2
        y = int(max_y * (1.0 - t))

    # Crop the pre-scaled image at the current pan/zoom position.
    crop = scaled.crop((x, y, x + crop_w, y + crop_h))

    # Resize the crop to the final frame size (this is the key render step).
    if crop.size != (VIDEO_WIDTH, VIDEO_HEIGHT):
        crop = crop.resize((VIDEO_WIDTH, VIDEO_HEIGHT), Image.LANCZOS)

    # Paste the cropped/zoomed photo onto the canvas.
    image.paste(crop, (0, 0))

    # Dark overlay so captions are always legible over any photo.
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

    # Pre-compute Ken Burns parameters: for each frame, we need to know
    # how many consecutive frames share the same background image (the
    # "segment"), and which position this frame is within that segment.
    # This lets us animate a smooth pan/zoom across each image.
    frame_kb_params = []   # list of (frame_in_segment, total_in_segment, segment_idx)
    if visual_frames:
        total_frames = len(caption_groups)
        segment_index = 0
        i = 0
        while i < total_frames:
            vf_i = min(i, len(visual_frames) - 1)
            current_path = visual_frames[vf_i]
            # Find how many consecutive frames share this same image.
            run_length = 1
            while (i + run_length < total_frames):
                vf_j = min(i + run_length, len(visual_frames) - 1)
                if visual_frames[vf_j] != current_path:
                    break
                run_length += 1
            for pos in range(run_length):
                frame_kb_params.append((pos, run_length, segment_index))
            if current_path is not None:
                segment_index += 1   # only advance segment counter for real images
            i += run_length

    _clear_bg_cache()   # start fresh for this render job

    concat_lines = ["ffconcat version 1.0"]
    frame_paths = []

    for index, group in enumerate(caption_groups):
        # Create a new RGBA image for each frame.
        # RGBA lets us draw semi-transparent rectangles (caption background).
        image = Image.new("RGBA", (VIDEO_WIDTH, VIDEO_HEIGHT), (*bg_color, 255))

        # Apply background image (stock photo, chart, or video/animation frame)
        # if one is available for this caption group.
        if visual_frames:
            # Clamp the index so we don't go out of bounds.
            vf_index = min(index, len(visual_frames) - 1)
            bg_image_path = visual_frames[vf_index]
            if bg_image_path and os.path.exists(bg_image_path):
                # Footage and animated frames come from cache/stock_video/ or
                # cache/animated/. They already change every caption group so we
                # skip the Ken Burns effect — applying pan/zoom on top of a
                # frame that's already moving would look jarring.
                is_motion_frame = (
                    "stock_video" in bg_image_path or
                    os.sep + "animated" + os.sep in bg_image_path
                )

                if is_motion_frame:
                    # Static placement — just fill the frame with the image.
                    _apply_background_image(
                        image, bg_image_path,
                        frame_in_segment=0,
                        total_in_segment=1,
                        segment_index=0,
                    )
                else:
                    # Still photo or chart — apply Ken Burns pan/zoom.
                    if index < len(frame_kb_params):
                        frame_in_seg, total_in_seg, seg_idx = frame_kb_params[index]
                    else:
                        frame_in_seg, total_in_seg, seg_idx = 0, 1, 0
                    _apply_background_image(
                        image, bg_image_path,
                        frame_in_segment=frame_in_seg,
                        total_in_segment=total_in_seg,
                        segment_index=seg_idx,
                    )

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

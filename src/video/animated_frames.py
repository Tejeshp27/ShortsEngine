# =============================================================
# src/video/animated_frames.py — Pillow-based animated graphics (M.4)
#
# Instead of a static background image, "animated" scene segments get
# a sequence of PNG frames drawn with Pillow that change across time —
# like a simple motion graphic. No external animation libraries are needed.
#
# Two animation types:
#   make_number_animation   — a large number counts up to its final value
#                             (e.g. "27,000 ft" for an altitude stat)
#   make_comparison_animation — two horizontal bars grow side by side
#                             (e.g. "Normal: 500 | Crash: 20,000")
#
# The frames go straight into build_frames() as background images, so
# the animation plays while captions change in the foreground. The
# dark background and large text are designed so captions remain legible
# even without the semi-transparent caption box.
#
# How it fits in:
#   render.py → _build_visual_frames() → render_animated_scene() (here)
# =============================================================

import hashlib
import os

from PIL import Image, ImageDraw, ImageFont


# Output dimensions: 9:16 portrait for TikTok / Instagram Reels.
VIDEO_WIDTH = 1080
VIDEO_HEIGHT = 1920

ANIMATED_CACHE = os.path.join("cache", "animated")

# Colour palette — dark background with bright accents.
DARK_BG = (15, 20, 35)       # near-black navy
ACCENT_GOLD = (255, 200, 60) # warm gold for numbers
ACCENT_BLUE = (60, 180, 255) # sky blue for labels
WHITE = (255, 255, 255)
MID_GRAY = (140, 150, 170)   # subtle grey for borders


def _load_font(size, bold=False):
    """
    Load a system font at the given pixel size.

    Falls back to Pillow's built-in bitmap font if no system font is found.

    Args:
        size (int): Font size in pixels.
        bold (bool): Try to load a bold variant (not all systems have one).

    Returns:
        PIL.ImageFont: Ready-to-use font object.
    """
    if bold:
        candidates = [
            "/System/Library/Fonts/Helvetica.ttc",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
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
    return ImageFont.load_default()


def _draw_centered(draw, text, font, y, color, shadow=True):
    """
    Draw text horizontally centred on the canvas.

    Args:
        draw (ImageDraw): Canvas to draw on.
        text (str): Text string to draw.
        font: Pillow font object.
        y (int): Vertical position for the top of the text.
        color (tuple): RGB colour tuple.
        shadow (bool): Add a dark drop shadow for legibility.
    """
    bbox = draw.textbbox((0, 0), text, font=font)
    text_w = bbox[2] - bbox[0]
    x = (VIDEO_WIDTH - text_w) // 2
    if shadow:
        draw.text((x + 3, y + 3), text, font=font, fill=(0, 0, 0, 180))
    draw.text((x, y), text, font=font, fill=color)


def _save_frame(image, path):
    """
    Convert an RGBA image to RGB and save it as PNG for the video pipeline.

    Args:
        image (PIL.Image): RGBA image.
        path (str): Output file path (should end in .png).
    """
    image.convert("RGB").save(path)


def make_number_animation(value_str, unit_str, context_str,
                           num_frames, output_folder, cache_key):
    """
    Create frames showing a large stat number counting up to its final value.

    The number starts at 0 and reaches its final value at the last frame.
    A gold accent line and progress bar tie the frames together visually.

    Args:
        value_str (str): The final value as a string, e.g. "27000" or "9".
        unit_str (str): The unit label, e.g. "feet" or "people dead".
        context_str (str): Short context, e.g. "descent rate" or "casualties".
        num_frames (int): How many PNG frames to create.
        output_folder (str): Where to save the frames.
        cache_key (str): Unique prefix for filenames (prevents collisions).

    Returns:
        list[str]: PNG paths, length = num_frames.

    Example:
        paths = make_number_animation("27000", "feet", "descent", 5, "cache/animated", "k1")
        # Creates 5 frames where the number counts: 5400, 10800, 16200, 21600, 27000
    """
    os.makedirs(output_folder, exist_ok=True)

    # Try to parse the value as a float for the counting animation.
    try:
        final_value = float(value_str.replace(",", "").replace("+", ""))
        is_numeric = True
    except ValueError:
        final_value = 0
        is_numeric = False

    frame_paths = []

    for i in range(num_frames):
        frame_path = os.path.join(output_folder, f"{cache_key}_num_{i}.png")

        if os.path.exists(frame_path):
            frame_paths.append(frame_path)
            continue

        # Progress goes from 1/N to N/N (so the first frame already shows something).
        progress = (i + 1) / num_frames

        image = Image.new("RGBA", (VIDEO_WIDTH, VIDEO_HEIGHT), (*DARK_BG, 255))
        draw = ImageDraw.Draw(image)

        # -- Gold accent line in the upper third --
        line_y = VIDEO_HEIGHT // 3
        draw.rectangle(
            [(80, line_y - 3), (VIDEO_WIDTH - 80, line_y + 3)],
            fill=ACCENT_GOLD,
        )

        # -- Context label above the number --
        label_font = _load_font(60)
        _draw_centered(draw, context_str.upper(), label_font,
                       line_y + 50, ACCENT_BLUE)

        # -- The main number, counting up --
        if is_numeric:
            current = final_value * progress
            if final_value == int(final_value):
                # Whole number — show with comma separators for readability.
                display = f"{int(current):,}"
            else:
                display = f"{current:.1f}"
        else:
            # Non-numeric value (e.g. "N/A") — show it from the first frame.
            display = value_str

        number_font = _load_font(220, bold=True)
        _draw_centered(draw, display, number_font,
                       VIDEO_HEIGHT // 3 + 120, ACCENT_GOLD)

        # -- Unit label below the number --
        unit_font = _load_font(80)
        _draw_centered(draw, unit_str.upper(), unit_font,
                       VIDEO_HEIGHT // 3 + 390, WHITE)

        # -- Progress bar along the bottom --
        bar_y = VIDEO_HEIGHT - 120
        bar_max = VIDEO_WIDTH - 160
        bar_w = int(bar_max * progress)
        # Background track
        draw.rectangle([(80, bar_y), (VIDEO_WIDTH - 80, bar_y + 8)],
                       outline=MID_GRAY, width=2)
        # Filled portion
        draw.rectangle([(80, bar_y), (80 + bar_w, bar_y + 8)], fill=ACCENT_GOLD)

        _save_frame(image, frame_path)
        frame_paths.append(frame_path)

    return frame_paths


def make_comparison_animation(pairs, num_frames, output_folder, cache_key):
    """
    Create frames showing two values side by side with animated growing bars.

    The bars start at zero and grow to their final widths across the frames.

    Args:
        pairs (list[tuple]): List of (label_str, value_float). Only the first
                             two pairs are used.
        num_frames (int): How many PNG frames to create.
        output_folder (str): Where to save the frames.
        cache_key (str): Unique prefix for filenames.

    Returns:
        list[str]: PNG paths, length = num_frames. Empty list if < 2 pairs.

    Example:
        paths = make_comparison_animation(
            [("Normal", 500.0), ("Crash descent", 20000.0)], 5, "cache/animated", "k2"
        )
    """
    os.makedirs(output_folder, exist_ok=True)

    if len(pairs) < 2:
        return []

    label_a, val_a = pairs[0]
    label_b, val_b = pairs[1]
    # Scale bars relative to the larger value.
    max_val = max(abs(val_a), abs(val_b), 1)

    frame_paths = []

    for i in range(num_frames):
        frame_path = os.path.join(output_folder, f"{cache_key}_cmp_{i}.png")

        if os.path.exists(frame_path):
            frame_paths.append(frame_path)
            continue

        progress = (i + 1) / num_frames

        image = Image.new("RGBA", (VIDEO_WIDTH, VIDEO_HEIGHT), (*DARK_BG, 255))
        draw = ImageDraw.Draw(image)

        # -- Header --
        header_font = _load_font(55)
        _draw_centered(draw, "COMPARISON", header_font, 160, ACCENT_BLUE)

        # Layout: two horizontal bars stacked vertically.
        bar_max_width = VIDEO_WIDTH - 200    # maximum bar width in pixels
        bar_height = 90

        # Format a number for display (adds commas, strips .0).
        def fmt(v):
            """Format a float for display: int if whole, 1 decimal otherwise."""
            return f"{int(v):,}" if v == int(v) else f"{v:.1f}"

        # -- Bar A --
        y_a = VIDEO_HEIGHT // 2 - 180
        label_font = _load_font(60, bold=True)
        val_font = _load_font(60)

        draw.text((100, y_a - 75), label_a.upper(), font=label_font, fill=WHITE)

        bar_a_width = int(bar_max_width * (abs(val_a) / max_val) * progress)
        draw.rectangle(
            [(100, y_a), (100 + bar_a_width, y_a + bar_height)],
            fill=ACCENT_BLUE,
        )
        # Show the final value at the right edge of the bar.
        draw.text(
            (100 + bar_a_width + 20, y_a + 20),
            fmt(val_a),
            font=val_font,
            fill=ACCENT_BLUE,
        )

        # -- Bar B --
        y_b = VIDEO_HEIGHT // 2 + 50
        draw.text((100, y_b - 75), label_b.upper(), font=label_font, fill=WHITE)

        bar_b_width = int(bar_max_width * (abs(val_b) / max_val) * progress)
        draw.rectangle(
            [(100, y_b), (100 + bar_b_width, y_b + bar_height)],
            fill=ACCENT_GOLD,
        )
        draw.text(
            (100 + bar_b_width + 20, y_b + 20),
            fmt(val_b),
            font=val_font,
            fill=ACCENT_GOLD,
        )

        _save_frame(image, frame_path)
        frame_paths.append(frame_path)

    return frame_paths


def _parse_animated_query(query):
    """
    Parse an animated scene's query string into animation parameters.

    Two formats are recognised:
      "value:unit"              → number callout
                                  e.g. "27000:feet" or "9:passengers"
      "label1:value1, label2:value2" → comparison bars
                                  e.g. "Normal:500, Crash descent:20000"

    The first format is detected when the left side of the first colon is
    a number. The second is detected when there are two or more "label:value"
    pairs where the right side is a number.

    Args:
        query (str): Raw query string from the scene plan.

    Returns:
        tuple: One of:
            ("number",     value_str, unit_str)       for a number callout
            ("comparison", [(label, value), ...], None) for comparison bars
            ("unknown",    None, None)                 if parsing fails
    """
    # --- Check for single "value:unit" format FIRST ---
    # We look at everything before the first colon as the candidate value.
    # This handles numbers with commas like "27,000:feet" because we do NOT
    # split on commas before checking — if the left side of the colon is a
    # number (after stripping commas), it's a number callout.
    first_colon = query.find(":")
    if first_colon != -1:
        potential_value = query[:first_colon].strip()
        potential_unit = query[first_colon + 1:].strip()
        # Only treat as "number:unit" if the unit side contains no colon
        # (which would mean it's a list of "label:value" pairs) and the
        # value side is a number after stripping commas/sign characters.
        value_clean = potential_value.replace(",", "").replace("+", "").replace("-", "")
        if ":" not in potential_unit and value_clean and value_clean.replace(".", "").isdigit():
            return "number", potential_value, potential_unit

    parts = [p.strip() for p in query.split(",") if p.strip()]

    # --- Check for multi-part "label:value, label:value" format ---
    if len(parts) >= 2:
        pairs = []
        for part in parts:
            colon_parts = part.split(":", 1)
            if len(colon_parts) == 2:
                label = colon_parts[0].strip()
                val_raw = colon_parts[1].strip().replace(",", "").replace("%", "")
                try:
                    val = float(val_raw)
                    pairs.append((label, val))
                except ValueError:
                    continue
        if len(pairs) >= 2:
            return "comparison", pairs, None

    return "unknown", None, None


def render_animated_scene(query, description, num_frames, output_folder):
    """
    Choose and render the right Pillow animation based on the scene plan's query.

    This is the main entry point called by render.py. It inspects the query
    string to decide which animation to use, then returns the PNG frame paths.

    If the query cannot be parsed (unexpected format), returns [] so the caller
    can fall back to a text card.

    Args:
        query (str): The query string from the scene plan
                     (e.g. "27000:feet" or "Before:30, After:70").
        description (str): Short human-readable label for context
                           (e.g. "altitude stat callout").
        num_frames (int): How many frames to create (typically 5 — one per
                          caption group in the segment).
        output_folder (str): Where to save the PNG frames.

    Returns:
        list[str]: PNG paths (length = num_frames), or [] if the query
                   could not be parsed.

    Example:
        paths = render_animated_scene("27000:feet", "descent rate", 5, "cache/animated")
        # → 5 PNG frames showing "27,000 FEET" counting up
    """
    # Use a hash of the query as the cache key so each unique query gets
    # its own set of frames and they never overwrite each other.
    cache_key = hashlib.sha256(query.encode()).hexdigest()[:12]
    os.makedirs(output_folder, exist_ok=True)

    anim_type, val_a, val_b = _parse_animated_query(query)

    if anim_type == "number":
        # val_a = number string (e.g. "27000"), val_b = unit string (e.g. "feet")
        context = description if description else "stat"
        return make_number_animation(
            value_str=val_a,
            unit_str=val_b,
            context_str=context,
            num_frames=num_frames,
            output_folder=output_folder,
            cache_key=cache_key,
        )

    elif anim_type == "comparison":
        # val_a = list of (label, value_float) pairs
        return make_comparison_animation(
            pairs=val_a,
            num_frames=num_frames,
            output_folder=output_folder,
            cache_key=cache_key,
        )

    else:
        print(f"  Animated scene: unrecognised query '{query[:40]}'. "
              "Falling back to text card.")
        return []

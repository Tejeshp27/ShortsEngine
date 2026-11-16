# =============================================================
# src/video/animation/drawing.py — helpers for drawing text and shapes
#
# Pillow (the image library) can draw text, rectangles and
# circles, but each call needs several lines of setup. The
# functions here wrap those calls so the scene code in scenes.py
# can say "draw this word, this big, centred here" in one line.
#
# Every function draws straight onto the current video frame,
# which is a Pillow image 1080 pixels wide and 1920 pixels tall.
# =============================================================

import os

from PIL import ImageDraw, ImageEnhance, ImageFont


# Video size for 9:16 portrait (TikTok / Instagram Reels).
VIDEO_WIDTH = 1080
VIDEO_HEIGHT = 1920

# The project root folder, worked out from this file's location
# (this file is at <root>/src/video/animation/drawing.py).
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))
)))

# Fonts to try for each weight, best first. Montserrat is bundled in
# assets/fonts (free, SIL Open Font License) so videos look the same on
# every computer. The others are fallbacks that ship with macOS / Linux.
FONT_CANDIDATES = {
    "black": [
        os.path.join(PROJECT_ROOT, "assets", "fonts", "Montserrat-Black.ttf"),
        "/System/Library/Fonts/Supplemental/Arial Black.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    ],
    "bold": [
        os.path.join(PROJECT_ROOT, "assets", "fonts", "Montserrat-Bold.ttf"),
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    ],
}

# Loading a font file is slow, and we need the same fonts thousands of
# times per video, so each (weight, size) pair is loaded once and kept here.
_font_cache = {}


def hex_to_rgb(hex_color):
    """
    Turn a colour written like "#FFD23F" into three numbers.

    Args:
        hex_color (str): A colour such as "#FFD23F" or "FFD23F".

    Returns:
        tuple: (red, green, blue), each from 0 to 255.

    Example:
        hex_to_rgb("#FF0000")   # → (255, 0, 0)
    """
    cleaned = hex_color.replace("#", "")
    red = int(cleaned[0:2], 16)
    green = int(cleaned[2:4], 16)
    blue = int(cleaned[4:6], 16)
    return (red, green, blue)


def get_font(size, weight="black"):
    """
    Load a font at a given size (or reuse it if already loaded).

    Args:
        size (int): The text height in pixels.
        weight (str): "black" (very thick, for headlines and captions)
                      or "bold" (for smaller supporting text).

    Returns:
        PIL.ImageFont: A font object that Pillow can draw with.
    """
    size = max(8, int(size))
    cache_key = (weight, size)
    if cache_key in _font_cache:
        return _font_cache[cache_key]

    font = None
    for path in FONT_CANDIDATES.get(weight, FONT_CANDIDATES["black"]):
        if os.path.exists(path):
            try:
                font = ImageFont.truetype(path, size)
                break
            except OSError:
                continue

    if font is None:
        # Last resort: Pillow's built-in font. Plain, but always available.
        font = ImageFont.load_default(size)

    _font_cache[cache_key] = font
    return font


def text_width(text, size, weight="black"):
    """
    Measure how many pixels wide a piece of text will be.

    Args:
        text (str): The text to measure.
        size (int): The font size in pixels.
        weight (str): "black" or "bold".

    Returns:
        float: The width in pixels.
    """
    return get_font(size, weight).getlength(text)


def fit_font_size(text, max_width, max_size, weight="black", min_size=36):
    """
    Find the biggest font size at which the text fits on one line.

    Args:
        text (str): The text that must fit.
        max_width (int): The widest the text may be, in pixels.
        max_size (int): The size to try first.
        weight (str): "black" or "bold".
        min_size (int): Never go smaller than this, even if it overflows.

    Returns:
        int: A font size between min_size and max_size.

    Example:
        fit_font_size("IMPEACHMENT", max_width=900, max_size=160)   # → 118
    """
    size = max_size
    while size > min_size and text_width(text, size, weight) > max_width:
        size -= 4
    return max(size, min_size)


def wrap_text(text, size, max_width, weight="black"):
    """
    Split text into lines that each fit within max_width.

    Args:
        text (str): The text to wrap.
        size (int): The font size in pixels.
        max_width (int): The widest a line may be, in pixels.
        weight (str): "black" or "bold".

    Returns:
        list[str]: The lines, top to bottom.

    Example:
        wrap_text("HOUSE OF REPRESENTATIVES", 100, 700)
        # → ["HOUSE OF", "REPRESENTATIVES"]
    """
    lines = []
    current_line = ""

    for word in text.split():
        if current_line:
            trial_line = current_line + " " + word
        else:
            trial_line = word

        if text_width(trial_line, size, weight) <= max_width or not current_line:
            current_line = trial_line
        else:
            lines.append(current_line)
            current_line = word

    if current_line:
        lines.append(current_line)
    return lines


def draw_text(frame, text, x, y, size, color, weight="black",
              alpha=1.0, outline=True, anchor="mm"):
    """
    Draw one line of text on the frame.

    Args:
        frame (PIL.Image): The video frame to draw on.
        text (str): The text to draw.
        x (float): Horizontal position in pixels.
        y (float): Vertical position in pixels.
        size (int): Font size in pixels.
        color (tuple): (red, green, blue).
        weight (str): "black" or "bold".
        alpha (float): 0.0 = invisible, 1.0 = solid. Used for fade-ins.
        outline (bool): Draw a dark edge around the letters so they stay
                        readable on top of bright video footage.
        anchor (str): Which point of the text sits at (x, y).
                      "mm" = the middle, "lm" = middle of the left edge,
                      "rm" = middle of the right edge.
    """
    if alpha <= 0.0 or not text:
        return

    # Opening the frame in "RGBA" mode lets us draw see-through colours.
    draw = ImageDraw.Draw(frame, "RGBA")
    font = get_font(size, weight)
    opacity = int(255 * min(alpha, 1.0))

    if outline:
        # The outline grows with the text so big headlines get a thicker edge.
        outline_width = max(3, int(size * 0.07))
        draw.text(
            (x, y), text, font=font, anchor=anchor,
            fill=(color[0], color[1], color[2], opacity),
            stroke_width=outline_width,
            stroke_fill=(0, 0, 0, opacity),
        )
    else:
        draw.text(
            (x, y), text, font=font, anchor=anchor,
            fill=(color[0], color[1], color[2], opacity),
        )


def draw_panel(frame, left, top, right, bottom, color, alpha=1.0, radius=40):
    """
    Draw a rectangle with rounded corners.

    Args:
        frame (PIL.Image): The video frame to draw on.
        left, top, right, bottom (float): The edges of the rectangle in pixels.
        color (tuple): (red, green, blue).
        alpha (float): 0.0 = invisible, 1.0 = solid.
        radius (int): How round the corners are, in pixels.
    """
    if alpha <= 0.0 or right <= left or bottom <= top:
        return

    draw = ImageDraw.Draw(frame, "RGBA")
    # The corner radius can never be more than half the shortest side.
    radius = int(min(radius, (right - left) / 2, (bottom - top) / 2))
    draw.rounded_rectangle(
        [left, top, right, bottom],
        radius=radius,
        fill=(color[0], color[1], color[2], int(255 * min(alpha, 1.0))),
    )


def draw_circle(frame, center_x, center_y, radius, color, alpha=1.0):
    """
    Draw a filled circle.

    Args:
        frame (PIL.Image): The video frame to draw on.
        center_x, center_y (float): The middle of the circle in pixels.
        radius (float): The circle's radius in pixels.
        color (tuple): (red, green, blue).
        alpha (float): 0.0 = invisible, 1.0 = solid.
    """
    if alpha <= 0.0 or radius <= 0:
        return

    draw = ImageDraw.Draw(frame, "RGBA")
    draw.ellipse(
        [center_x - radius, center_y - radius,
         center_x + radius, center_y + radius],
        fill=(color[0], color[1], color[2], int(255 * min(alpha, 1.0))),
    )


def paste_image(frame, picture, center_x, center_y, alpha=1.0):
    """
    Place a picture with see-through areas (like an icon) on the frame.

    Args:
        frame (PIL.Image): The video frame to draw on.
        picture (PIL.Image): An RGBA image, already at the size wanted.
        center_x, center_y (float): Where the middle of the picture goes.
        alpha (float): 0.0 = invisible, 1.0 = solid.
    """
    if picture is None or alpha <= 0.0:
        return

    left = int(center_x - picture.width / 2)
    top = int(center_y - picture.height / 2)

    # The picture's own alpha channel says which pixels are see-through.
    mask = picture.getchannel("A")
    if alpha < 1.0:
        # Fade the whole picture by making every pixel more see-through.
        # "Brightness" on a mask simply multiplies each value by alpha.
        mask = ImageEnhance.Brightness(mask).enhance(alpha)

    frame.paste(picture, (left, top), mask)

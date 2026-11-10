# =============================================================
# src/video/animation/scenes.py — the animated graphics
#
# A "scene" is the graphic shown on top of the footage while one
# sentence of the script is spoken. There are nine templates:
#
#   headline    — big words that slam in one at a time
#   icon_label  — a large icon on a badge with a title
#   person      — a name card with the person's initials
#   big_number  — a statistic that counts up
#   versus      — two sides, shown as two cards with "VS" between
#   bullet_list — a title and items that slide in one by one
#   timeline    — numbered steps joined by a line that draws itself
#   bar_chart   — bars that grow to compare numbers
#   stamp       — a word stamped onto the screen at an angle
#
# Every template is one function with the same inputs. The
# renderer calls it once per video frame, passing how many
# seconds have passed since the scene began. The function works
# out where everything should be at that moment and draws it.
# Calling it 30 times a second with a growing time is what makes
# the animation.
#
# How it fits in:
#   renderer.py → draw_scene() → the matching draw_... function
# =============================================================

import math

from PIL import Image, ImageDraw

from src.video.animation.drawing import (
    VIDEO_WIDTH,
    draw_circle,
    draw_panel,
    draw_text,
    fit_font_size,
    paste_image,
    text_width,
    wrap_text,
)
from src.video.animation.easing import (
    ease_in_cubic,
    ease_out_back,
    ease_out_cubic,
    lerp,
    progress,
)
from src.video.animation.icons import get_icon


# The graphics stay inside this area so the TikTok / Instagram buttons
# (top, bottom and right edge) never cover them.
CENTER_X = VIDEO_WIDTH // 2
CONTENT_CENTER_Y = 760
CONTENT_LEFT = 70
CONTENT_RIGHT = 1010
CONTENT_WIDTH = CONTENT_RIGHT - CONTENT_LEFT

# Colour of the see-through dark cards that sit behind text.
CARD_COLOR = (10, 12, 26)
CARD_ALPHA = 0.78


def _bob(time_in_scene):
    """
    A small up-and-down drift that keeps icons gently moving.

    Args:
        time_in_scene (float): Seconds since the scene began.

    Returns:
        float: A vertical offset in pixels, between -10 and 10.
    """
    return 10 * math.sin(time_in_scene * 2.4)


def _draw_icon(frame, emoji, center_x, center_y, size, time_in_scene, delay=0.0):
    """
    Draw an emoji icon that pops in with a bounce, then gently bobs.

    Args:
        frame (PIL.Image): The video frame to draw on.
        emoji (str): The emoji to show. Nothing is drawn if it is empty.
        center_x, center_y (float): Where the middle of the icon goes.
        size (int): The icon's final width and height in pixels.
        time_in_scene (float): Seconds since the scene began.
        delay (float): How long to wait before the icon appears.
    """
    scale = ease_out_back(progress(time_in_scene, delay, 0.45))
    if scale <= 0.0:
        return

    # Rounding the size to a multiple of 4 means fewer different sizes
    # have to be resized and remembered while the icon is growing.
    current_size = int(size * scale / 4) * 4
    icon = get_icon(emoji, current_size)
    paste_image(frame, icon, center_x, center_y + _bob(time_in_scene))


def _draw_lines(frame, lines, x, center_y, size, color, weight="black",
                alpha=1.0, anchor="mm", outline=True):
    """
    Draw several lines of text as one block, centred on center_y.

    Args:
        frame (PIL.Image): The video frame to draw on.
        lines (list[str]): The lines, top to bottom.
        x (float): Horizontal position (meaning depends on anchor).
        center_y (float): The vertical middle of the whole block.
        size (int): Font size in pixels.
        color (tuple): (red, green, blue).
        weight (str): "black" or "bold".
        alpha (float): 0.0 = invisible, 1.0 = solid.
        anchor (str): "mm" to centre each line on x, "lm" to start at x.
        outline (bool): Draw a dark edge round the letters. Turn this off
                        for text that already sits on a dark card.
    """
    line_height = int(size * 1.18)
    top = center_y - line_height * len(lines) / 2
    for line_number, line in enumerate(lines):
        y = top + line_height * (line_number + 0.5)
        draw_text(frame, line, x, y, size, color, weight=weight,
                  alpha=alpha, anchor=anchor, outline=outline)


def _fit_wrapped_text(text, max_width, max_lines, start_size, weight="black",
                      min_size=40):
    """
    Pick a font size so text fits in a box of max_width × max_lines.

    Args:
        text (str): The text to fit.
        max_width (int): The widest a line may be, in pixels.
        max_lines (int): The most lines allowed.
        start_size (int): The biggest font size to try.
        weight (str): "black" or "bold".
        min_size (int): The smallest font size allowed.

    Returns:
        tuple: (size, lines) — the font size chosen and the wrapped lines.
    """
    # First make sure the longest single word fits on a line by itself.
    longest_word = ""
    for word in text.split():
        if len(word) > len(longest_word):
            longest_word = word
    size = fit_font_size(longest_word, max_width, start_size, weight, min_size)

    # Then shrink until the whole text fits in the allowed number of lines.
    lines = wrap_text(text, size, max_width, weight)
    while len(lines) > max_lines and size > min_size:
        size -= 6
        lines = wrap_text(text, size, max_width, weight)
    return size, lines


def _draw_pill(frame, text, top, max_width, start_size, pill_color, pill_alpha,
               text_color, amount):
    """
    Draw short text inside a rounded "pill", sliding up as it appears.

    Args:
        frame (PIL.Image): The video frame to draw on.
        text (str): The text to show. Nothing is drawn if it is empty.
        top (float): The top edge of the pill's final position.
        max_width (int): The widest the text may be, in pixels.
        start_size (int): The biggest font size to try.
        pill_color (tuple): (red, green, blue) of the pill.
        pill_alpha (float): How solid the pill is (0.0 to 1.0).
        text_color (tuple): (red, green, blue) of the text.
        amount (float): Animation progress, 0.0 (hidden) to 1.0 (in place).
    """
    if not text or amount <= 0.0:
        return

    size, lines = _fit_wrapped_text(text, max_width, 2, start_size, weight="bold")
    pill_height = int(size * 1.18) * len(lines) + 44
    widest = 0
    for line in lines:
        widest = max(widest, text_width(line, size, "bold"))

    center_y = top + pill_height / 2 + 40 * (1 - amount)
    draw_panel(frame, CENTER_X - widest / 2 - 44, center_y - pill_height / 2,
               CENTER_X + widest / 2 + 44, center_y + pill_height / 2,
               pill_color, pill_alpha * amount, radius=40)
    _draw_lines(frame, lines, CENTER_X, center_y, size, text_color,
                weight="bold", alpha=amount, outline=False)


def _clean_word(word):
    """
    Lower-case a word and strip punctuation, for comparing words.

    Args:
        word (str): e.g. "Impeach," or "PARTY'S".

    Returns:
        str: e.g. "impeach" or "party's".
    """
    return word.lower().strip(".,;:!?\"'“”‘’()-—")


def _format_number(value, decimals):
    """
    Write a number the way people read it, with thousands separators.

    Args:
        value (float): The number to show.
        decimals (int): How many digits after the decimal point.

    Returns:
        str: e.g. "27,000" or "3.5".
    """
    if decimals == 0:
        return f"{int(round(value)):,}"
    return f"{value:,.{decimals}f}"


def _count_decimals(value):
    """
    Decide how many decimal places a number needs.

    Args:
        value (float): The final number a counter will reach.

    Returns:
        int: 0 for whole numbers such as 90, otherwise 1 (e.g. for 3.5).
    """
    if float(value) == int(value):
        return 0
    return 1


def _draw_title(frame, title, y, color, time_in_scene):
    """
    Draw a scene title that slides down into place at the top.

    Args:
        frame (PIL.Image): The video frame to draw on.
        title (str): The title text. Nothing is drawn if it is empty.
        y (float): The vertical middle of the title's final position.
        color (tuple): (red, green, blue).
        time_in_scene (float): Seconds since the scene began.
    """
    if not title:
        return
    amount = ease_out_cubic(progress(time_in_scene, 0.0, 0.3))
    size, lines = _fit_wrapped_text(title.upper(), CONTENT_WIDTH, 2, 84)
    _draw_lines(frame, lines, CENTER_X, y - 40 * (1 - amount), size, color,
                alpha=amount)


def draw_headline(frame, scene, time_in_scene, duration, colors):
    """
    Draw big headline words that slam onto the screen one at a time.

    Scene fields used:
        text (str): 2–7 words to show.
        highlight (str): Word(s) from the text to colour with the accent.
        emoji (str): Optional icon shown above the words.

    Args:
        frame (PIL.Image): The video frame to draw on.
        scene (dict): The scene from the storyboard.
        time_in_scene (float): Seconds since the scene began.
        duration (float): How long the scene lasts, in seconds.
        colors (dict): "accent", "second" and "text" colours.
    """
    text = scene.get("text", "").upper()
    emoji = scene.get("emoji", "")
    words = text.split()
    if not words:
        return

    # Only one word is highlighted, so the accent colour stays special.
    highlight_words = []
    for word in scene.get("highlight", "").split()[:1]:
        highlight_words.append(_clean_word(word))

    size, lines = _fit_wrapped_text(text, 940, 3, 150, min_size=70)
    line_height = int(size * 1.15)
    block_height = line_height * len(lines)

    # Stack the icon (if any) above the words, centred as one group.
    icon_size = 0
    icon_gap = 0
    if emoji:
        icon_size = 280
        icon_gap = 50
    top = CONTENT_CENTER_Y - (icon_size + icon_gap + block_height) / 2

    first_word_delay = 0.0
    if emoji:
        _draw_icon(frame, emoji, CENTER_X, top + icon_size / 2, icon_size,
                   time_in_scene)
        first_word_delay = 0.15

    # Each word appears a moment after the one before. Short scenes use a
    # shorter gap so all the words are on screen by the halfway point.
    stagger = min(0.16, duration * 0.5 / len(words))
    space_width = text_width(" ", size)
    text_top = top + icon_size + icon_gap
    word_number = 0

    for line_number, line in enumerate(lines):
        x = (VIDEO_WIDTH - text_width(line, size)) / 2
        y = text_top + line_height * (line_number + 0.5)

        for word in line.split():
            word_width = text_width(word, size)
            delay = first_word_delay + word_number * stagger
            amount = progress(time_in_scene, delay, 0.22)

            if amount > 0.0:
                # Start 60% too big and shrink to normal: the "slam".
                scale = lerp(1.6, 1.0, ease_out_cubic(amount))
                color = colors["text"]
                if _clean_word(word) in highlight_words:
                    color = colors["accent"]
                    # Highlighted words keep pulsing slightly.
                    scale = scale * (1.0 + 0.03 * math.sin(time_in_scene * 5.0))
                draw_text(frame, word, x + word_width / 2, y,
                          int(size * scale), color, alpha=amount)

            x += word_width + space_width
            word_number += 1

    # An accent line grows out from the centre once the words have landed.
    line_delay = first_word_delay + len(words) * stagger + 0.1
    line_amount = ease_out_cubic(progress(time_in_scene, line_delay, 0.35))
    if line_amount > 0.0:
        half_width = 240 * line_amount
        line_y = text_top + block_height + 26
        draw_panel(frame, CENTER_X - half_width, line_y,
                   CENTER_X + half_width, line_y + 16,
                   colors["accent"], radius=8)


def draw_icon_label(frame, scene, time_in_scene, duration, colors):
    """
    Draw a large icon on a coloured badge, with a title underneath.

    Scene fields used:
        emoji (str): The icon.
        title (str): 1–4 words shown in big letters.
        subtitle (str): Optional short line shown in a pill below.

    Args:
        frame (PIL.Image): The video frame to draw on.
        scene (dict): The scene from the storyboard.
        time_in_scene (float): Seconds since the scene began.
        duration (float): How long the scene lasts, in seconds.
        colors (dict): "accent", "second" and "text" colours.
    """
    badge_y = 590
    badge_scale = ease_out_back(progress(time_in_scene, 0.0, 0.4))

    # If the icon's picture could not be loaded (no internet, or an emoji
    # Noto does not have), skip the badge rather than show an empty circle.
    if get_icon(scene.get("emoji", ""), 290) is None:
        badge_scale = 0.0

    if badge_scale > 0.0:
        # A faint outer ring that slowly "breathes" behind the badge.
        ring_radius = (250 + 14 * math.sin(time_in_scene * 3.0)) * badge_scale
        draw_circle(frame, CENTER_X, badge_y, ring_radius, colors["accent"], 0.3)
        draw_circle(frame, CENTER_X, badge_y, 205 * badge_scale, colors["accent"], 0.95)

    _draw_icon(frame, scene.get("emoji", ""), CENTER_X, badge_y, 290,
               time_in_scene, delay=0.1)

    # The title slides up into place just after the icon lands. Its height
    # depends on how many lines it needs, so the subtitle is placed below
    # wherever the title actually ends.
    title_top = 850
    title_height = 0
    title = scene.get("title", "").upper()
    if title:
        amount = ease_out_cubic(progress(time_in_scene, 0.2, 0.3))
        size, lines = _fit_wrapped_text(title, 940, 2, 116)
        title_height = int(size * 1.18) * len(lines)
        _draw_lines(frame, lines, CENTER_X,
                    title_top + title_height / 2 + 50 * (1 - amount), size,
                    colors["text"], alpha=amount)

    subtitle_amount = ease_out_cubic(progress(time_in_scene, 0.4, 0.3))
    _draw_pill(frame, scene.get("subtitle", ""), title_top + title_height + 30,
               860, 54, CARD_COLOR, CARD_ALPHA, colors["text"], subtitle_amount)


def _get_initials(name):
    """
    Get up to two initials from a person's name.

    Args:
        name (str): e.g. "Thomas Massie".

    Returns:
        str: e.g. "TM".
    """
    words = name.split()
    if not words:
        return ""
    if len(words) == 1:
        return words[0][0].upper()
    return (words[0][0] + words[-1][0]).upper()


def draw_person(frame, scene, time_in_scene, duration, colors):
    """
    Draw a name card: a circle with the person's initials, name and role.

    We show initials rather than a photo so we never misuse anyone's
    picture.

    Scene fields used:
        name (str): The person's name, as written in the script.
        role (str): A short description, e.g. "Republican, Kentucky".

    Args:
        frame (PIL.Image): The video frame to draw on.
        scene (dict): The scene from the storyboard.
        time_in_scene (float): Seconds since the scene began.
        duration (float): How long the scene lasts, in seconds.
        colors (dict): "accent", "second" and "text" colours.
    """
    name = scene.get("name", "")
    circle_y = 570
    scale = ease_out_back(progress(time_in_scene, 0.0, 0.4))

    if scale > 0.0:
        # Accent ring, dark centre, then the initials on top.
        draw_circle(frame, CENTER_X, circle_y, 210 * scale, colors["accent"])
        draw_circle(frame, CENTER_X, circle_y, 186 * scale, CARD_COLOR)
        draw_text(frame, _get_initials(name), CENTER_X, circle_y,
                  int(150 * scale), colors["text"], outline=False)

        # A small dot circles the ring forever, so the card never sits still.
        angle = time_in_scene * 2.0
        dot_x = CENTER_X + 240 * scale * math.cos(angle)
        dot_y = circle_y + 240 * scale * math.sin(angle)
        draw_circle(frame, dot_x, dot_y, 14, colors["text"])

    # The name's height depends on how many lines it needs, so the role
    # is placed below wherever the name actually ends.
    name_top = 830
    name_height = 0
    if name:
        amount = ease_out_cubic(progress(time_in_scene, 0.2, 0.3))
        size, lines = _fit_wrapped_text(name.upper(), 940, 2, 108)
        name_height = int(size * 1.18) * len(lines)
        _draw_lines(frame, lines, CENTER_X,
                    name_top + name_height / 2 + 50 * (1 - amount), size,
                    colors["text"], alpha=amount)

    # The role sits in an accent-coloured pill with dark text.
    role_amount = ease_out_cubic(progress(time_in_scene, 0.4, 0.3))
    _draw_pill(frame, scene.get("role", "").upper(), name_top + name_height + 30,
               820, 50, colors["accent"], 1.0, CARD_COLOR, role_amount)


def draw_big_number(frame, scene, time_in_scene, duration, colors):
    """
    Draw a statistic that counts up from zero to its final value.

    Scene fields used:
        value (float): The number to count up to.
        prefix (str): Text before the number, e.g. "$".
        suffix (str): Text after the number, e.g. "%" or "+".
        label (str): What the number means, e.g. "days without approval".
        emoji (str): Optional icon shown above the number.

    Args:
        frame (PIL.Image): The video frame to draw on.
        scene (dict): The scene from the storyboard.
        time_in_scene (float): Seconds since the scene began.
        duration (float): How long the scene lasts, in seconds.
        colors (dict): "accent", "second" and "text" colours.
    """
    final_value = float(scene.get("value", 0))
    prefix = scene.get("prefix", "")
    suffix = scene.get("suffix", "")
    decimals = _count_decimals(final_value)

    # Choose the font size from the FINAL text, so the number does not
    # change size while it is counting.
    final_text = prefix + _format_number(final_value, decimals) + suffix
    size = fit_font_size(final_text, 920, 340)

    emoji = scene.get("emoji", "")
    number_y = CONTENT_CENTER_Y
    if emoji:
        _draw_icon(frame, emoji, CENTER_X, number_y - size * 0.5 - 150, 220,
                   time_in_scene)
        number_y += 60

    # Count for about a second, or less if the scene is short.
    count_duration = min(1.1, duration * 0.5)
    count_amount = ease_out_cubic(progress(time_in_scene, 0.1, count_duration))
    current_text = prefix + _format_number(final_value * count_amount, decimals) + suffix

    appear = progress(time_in_scene, 0.0, 0.2)
    # A quick "punch" bigger at the moment the count finishes.
    punch = 1.0 + 0.08 * math.sin(math.pi * progress(time_in_scene, 0.1 + count_duration, 0.25))
    draw_text(frame, current_text, CENTER_X, number_y, int(size * punch),
              colors["accent"], alpha=appear)

    label_amount = ease_out_cubic(progress(time_in_scene, 0.25, 0.3))
    _draw_pill(frame, scene.get("label", "").upper(), number_y + size * 0.5 + 50,
               860, 62, CARD_COLOR, CARD_ALPHA, colors["text"], label_amount)


def _draw_versus_card(frame, emoji, label, top, slide_x, stripe_color,
                      text_color, time_in_scene, delay):
    """
    Draw one of the two cards in a "versus" scene.

    Args:
        frame (PIL.Image): The video frame to draw on.
        emoji (str): Icon for this side.
        label (str): Short name of this side.
        top (float): The top edge of the card in pixels.
        slide_x (float): Sideways offset while the card slides in.
        stripe_color (tuple): Colour of the stripe on the card's left edge.
        text_color (tuple): Colour of the label.
        time_in_scene (float): Seconds since the scene began.
        delay (float): When this card starts to appear.
    """
    card_height = 340
    left = CONTENT_LEFT + slide_x
    right = CONTENT_RIGHT + slide_x

    draw_panel(frame, left, top, right, top + card_height, CARD_COLOR, CARD_ALPHA,
               radius=48)
    draw_panel(frame, left, top, left + 26, top + card_height, stripe_color,
               radius=13)

    text_left = left + 70
    if emoji:
        _draw_icon(frame, emoji, left + 180, top + card_height / 2, 210,
                   time_in_scene, delay=delay)
        text_left = left + 330

    size, lines = _fit_wrapped_text(label.upper(), right - text_left - 40, 3, 76)
    _draw_lines(frame, lines, text_left, top + card_height / 2, size, text_color,
                anchor="lm", outline=False)


def draw_versus(frame, scene, time_in_scene, duration, colors):
    """
    Draw two sides of a story as two cards with a "VS" badge between.

    The top card slides in from the left, the bottom one from the right.

    Scene fields used:
        left_emoji, left_label (str): Icon and short name for side one.
        right_emoji, right_label (str): Icon and short name for side two.

    Args:
        frame (PIL.Image): The video frame to draw on.
        scene (dict): The scene from the storyboard.
        time_in_scene (float): Seconds since the scene began.
        duration (float): How long the scene lasts, in seconds.
        colors (dict): "accent", "second" and "text" colours.
    """
    top_amount = ease_out_cubic(progress(time_in_scene, 0.0, 0.4))
    bottom_amount = ease_out_cubic(progress(time_in_scene, 0.15, 0.4))

    # Each card starts fully off screen and slides to its place.
    _draw_versus_card(
        frame, scene.get("left_emoji", ""), scene.get("left_label", ""),
        top=370, slide_x=-VIDEO_WIDTH * (1 - top_amount),
        stripe_color=colors["accent"], text_color=colors["text"],
        time_in_scene=time_in_scene, delay=0.2,
    )
    _draw_versus_card(
        frame, scene.get("right_emoji", ""), scene.get("right_label", ""),
        top=810, slide_x=VIDEO_WIDTH * (1 - bottom_amount),
        stripe_color=colors["second"], text_color=colors["text"],
        time_in_scene=time_in_scene, delay=0.35,
    )

    # The "VS" badge pops in between the cards and keeps pulsing.
    badge_scale = ease_out_back(progress(time_in_scene, 0.45, 0.35))
    if badge_scale > 0.0:
        badge_scale = badge_scale * (1.0 + 0.04 * math.sin(time_in_scene * 6.0))
        draw_circle(frame, CENTER_X, 760, 92 * badge_scale, colors["text"])
        draw_text(frame, "VS", CENTER_X, 760, int(76 * badge_scale), CARD_COLOR,
                  outline=False)


def draw_bullet_list(frame, scene, time_in_scene, duration, colors):
    """
    Draw a title and a list of items that slide in one after another.

    Scene fields used:
        title (str): Short heading for the list.
        items (list[dict]): 2–4 items, each {"emoji": str, "text": str}.

    Args:
        frame (PIL.Image): The video frame to draw on.
        scene (dict): The scene from the storyboard.
        time_in_scene (float): Seconds since the scene began.
        duration (float): How long the scene lasts, in seconds.
        colors (dict): "accent", "second" and "text" colours.
    """
    items = scene.get("items", [])
    if not items:
        return

    row_height = 190
    row_gap = 26
    list_height = len(items) * row_height + (len(items) - 1) * row_gap
    list_top = CONTENT_CENTER_Y - list_height / 2 + 70
    _draw_title(frame, scene.get("title", ""), list_top - 110, colors["accent"],
                time_in_scene)

    # Spread the items across the scene so one appears every beat of speech.
    stagger = min(1.2, duration * 0.7 / len(items))

    for item_number, item in enumerate(items):
        delay = 0.2 + item_number * stagger
        amount = ease_out_cubic(progress(time_in_scene, delay, 0.35))
        if amount <= 0.0:
            continue

        slide_x = 260 * (1 - amount)
        top = list_top + item_number * (row_height + row_gap)
        left = CONTENT_LEFT + slide_x

        draw_panel(frame, left, top, CONTENT_RIGHT + slide_x, top + row_height,
                   CARD_COLOR, CARD_ALPHA * amount, radius=44)

        text_left = left + 60
        if item.get("emoji", ""):
            _draw_icon(frame, item["emoji"], left + 110, top + row_height / 2,
                       130, time_in_scene, delay=delay + 0.05)
            text_left = left + 210

        size, lines = _fit_wrapped_text(item.get("text", ""),
                                        CONTENT_RIGHT - text_left - 40 + slide_x,
                                        2, 60, weight="bold")
        _draw_lines(frame, lines, text_left, top + row_height / 2, size,
                    colors["text"], weight="bold", alpha=amount, anchor="lm",
                    outline=False)


def draw_timeline(frame, scene, time_in_scene, duration, colors):
    """
    Draw numbered steps joined by a line that draws itself downwards.

    Scene fields used:
        title (str): Short heading, e.g. "What happens next".
        steps (list[str]): 2–4 short step descriptions, in order.

    Args:
        frame (PIL.Image): The video frame to draw on.
        scene (dict): The scene from the storyboard.
        time_in_scene (float): Seconds since the scene began.
        duration (float): How long the scene lasts, in seconds.
        colors (dict): "accent", "second" and "text" colours.
    """
    steps = scene.get("steps", [])
    if not steps:
        return

    step_gap = 230
    line_x = 160
    first_y = CONTENT_CENTER_Y - (len(steps) - 1) * step_gap / 2 + 70
    _draw_title(frame, scene.get("title", ""), first_y - 190, colors["accent"],
                time_in_scene)

    stagger = min(1.2, duration * 0.7 / len(steps))

    # How many steps the line has reached so far (can be a fraction,
    # e.g. 1.5 means the line is halfway between step 1 and step 2).
    steps_reached = (time_in_scene - 0.2) / stagger
    steps_reached = max(0.0, min(steps_reached, len(steps) - 1))
    line_bottom = first_y + steps_reached * step_gap

    draw_panel(frame, line_x - 7, first_y, line_x + 7,
               first_y + (len(steps) - 1) * step_gap, CARD_COLOR, 0.6, radius=7)
    draw_panel(frame, line_x - 7, first_y, line_x + 7, line_bottom,
               colors["accent"], radius=7)

    newest_step = int(steps_reached)

    for step_number, step_text in enumerate(steps):
        delay = 0.2 + step_number * stagger
        scale = ease_out_back(progress(time_in_scene, delay, 0.35))
        if scale <= 0.0:
            continue

        y = first_y + step_number * step_gap

        # The step most recently reached is filled with the accent colour.
        circle_color = colors["text"]
        if step_number == newest_step:
            circle_color = colors["accent"]
        draw_circle(frame, line_x, y, 50 * scale, circle_color)
        draw_text(frame, str(step_number + 1), line_x, y, int(54 * scale),
                  CARD_COLOR, outline=False)

        # The step's text slides in on a dark card beside its number.
        amount = ease_out_cubic(progress(time_in_scene, delay + 0.05, 0.3))
        if amount > 0.0:
            slide_x = 60 * (1 - amount)
            card_left = line_x + 80 + slide_x
            draw_panel(frame, card_left, y - 80, CONTENT_RIGHT + slide_x, y + 80,
                       CARD_COLOR, CARD_ALPHA * amount, radius=40)
            size, lines = _fit_wrapped_text(step_text, CONTENT_RIGHT - card_left - 80 + slide_x,
                                            2, 58, weight="bold")
            _draw_lines(frame, lines, card_left + 40, y, size, colors["text"],
                        weight="bold", alpha=amount, anchor="lm", outline=False)


def draw_bar_chart(frame, scene, time_in_scene, duration, colors):
    """
    Draw horizontal bars that grow to compare two to four numbers.

    Scene fields used:
        title (str): Short heading for the chart.
        bars (list[dict]): 2–4 bars, each {"label": str, "value": float}.
        unit (str): Optional text after each value, e.g. "%".

    Args:
        frame (PIL.Image): The video frame to draw on.
        scene (dict): The scene from the storyboard.
        time_in_scene (float): Seconds since the scene began.
        duration (float): How long the scene lasts, in seconds.
        colors (dict): "accent", "second" and "text" colours.
    """
    bars = scene.get("bars", [])
    if not bars:
        return

    unit = scene.get("unit", "")
    row_height = 210
    chart_top = CONTENT_CENTER_Y - len(bars) * row_height / 2 + 70
    _draw_title(frame, scene.get("title", ""), chart_top - 110, colors["text"],
                time_in_scene)

    # The longest bar fills the full width; the others are scaled to it.
    largest_value = 1.0
    for bar in bars:
        largest_value = max(largest_value, abs(float(bar.get("value", 0))))

    bar_colors = [colors["accent"], colors["second"]]

    # One dark card sits behind the whole chart so it reads clearly.
    card_amount = ease_out_cubic(progress(time_in_scene, 0.0, 0.3))
    draw_panel(frame, CONTENT_LEFT - 30, chart_top - 20, CONTENT_RIGHT + 30,
               chart_top + len(bars) * row_height + 10, CARD_COLOR,
               CARD_ALPHA * card_amount, radius=48)

    for bar_number, bar in enumerate(bars):
        value = float(bar.get("value", 0))
        delay = 0.2 + bar_number * 0.2
        amount = ease_out_cubic(progress(time_in_scene, delay, 0.8))
        appear = progress(time_in_scene, delay, 0.2)
        if appear <= 0.0:
            continue

        top = chart_top + bar_number * row_height
        bar_color = bar_colors[bar_number % len(bar_colors)]

        draw_text(frame, bar.get("label", "").upper(), CONTENT_LEFT + 10, top + 40, 50,
                  colors["text"], weight="bold", alpha=appear, anchor="lm",
                  outline=False)

        # The value counts up while its bar grows.
        value_text = _format_number(value * amount, _count_decimals(value)) + unit
        draw_text(frame, value_text, CONTENT_RIGHT - 10, top + 40, 64, bar_color,
                  alpha=appear, anchor="rm", outline=False)

        track_top = top + 90
        draw_panel(frame, CONTENT_LEFT, track_top, CONTENT_RIGHT, track_top + 76,
                   (255, 255, 255), 0.14 * appear, radius=38)
        fill_width = CONTENT_WIDTH * (abs(value) / largest_value) * amount
        # Never draw the bar narrower than its rounded ends.
        fill_width = max(fill_width, 76)
        draw_panel(frame, CONTENT_LEFT, track_top, CONTENT_LEFT + fill_width,
                   track_top + 76, bar_color, appear, radius=38)


def _make_stamp_picture(text, color):
    """
    Build the picture of a rubber stamp: tilted text in a bordered box.

    Args:
        text (str): 1–3 words to stamp.
        color (tuple): (red, green, blue) of the text and border.

    Returns:
        PIL.Image: An RGBA picture of the stamp, already tilted.
    """
    size, lines = _fit_wrapped_text(text.upper(), 760, 2, 170)
    line_height = int(size * 1.15)
    padding = 60

    widest = 0
    for line in lines:
        widest = max(widest, text_width(line, size))
    width = int(widest + padding * 2)
    height = int(line_height * len(lines) + padding * 2)

    picture = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(picture)
    draw.rounded_rectangle(
        [0, 0, width - 1, height - 1], radius=44,
        fill=(CARD_COLOR[0], CARD_COLOR[1], CARD_COLOR[2], 215),
        outline=(color[0], color[1], color[2], 255), width=16,
    )
    for line_number, line in enumerate(lines):
        y = padding + line_height * (line_number + 0.5)
        draw_text(picture, line, width / 2, y, size, color, outline=False)

    # Tilt the stamp a few degrees, like a real one pressed by hand.
    return picture.rotate(6, expand=True, resample=Image.BICUBIC)


def draw_stamp(frame, scene, time_in_scene, duration, colors):
    """
    Slam a word onto the screen like a rubber stamp, with a shake.

    Scene fields used:
        text (str): 1–4 words, e.g. "EXTREMELY RARE".
        emoji (str): Optional icon shown above the stamp.

    Args:
        frame (PIL.Image): The video frame to draw on.
        scene (dict): The scene from the storyboard.
        time_in_scene (float): Seconds since the scene began.
        duration (float): How long the scene lasts, in seconds.
        colors (dict): "accent", "second" and "text" colours.
    """
    # Building the stamp picture is slow, so do it once per scene and
    # keep it in the scene itself for the following frames.
    if "_stamp_picture" not in scene:
        scene["_stamp_picture"] = _make_stamp_picture(scene.get("text", ""),
                                                     colors["accent"])
    stamp = scene["_stamp_picture"]

    slam_start = 0.05
    slam_duration = 0.2
    amount = progress(time_in_scene, slam_start, slam_duration)
    if amount <= 0.0:
        return

    # The stamp starts huge and drops down to its real size.
    scale = lerp(2.4, 1.0, ease_in_cubic(amount))

    # After it lands, shake left-right quickly, fading out over ~0.4s.
    shake_x = 0.0
    time_since_landing = time_in_scene - (slam_start + slam_duration)
    if time_since_landing > 0.0:
        shake_strength = 18 * math.exp(-8.0 * time_since_landing)
        shake_x = shake_strength * math.sin(time_since_landing * 60.0)

    picture = stamp
    if scale > 1.01:
        new_width = int(stamp.width * scale)
        new_height = int(stamp.height * scale)
        picture = stamp.resize((new_width, new_height), Image.BILINEAR)

    stamp_y = CONTENT_CENTER_Y + 40
    emoji = scene.get("emoji", "")
    if emoji:
        stamp_y += 110
        _draw_icon(frame, emoji, CENTER_X, stamp_y - stamp.height / 2 - 170, 250,
                   time_in_scene, delay=0.3)

    paste_image(frame, picture, CENTER_X + shake_x, stamp_y, alpha=amount)


# Which function draws each template. The storyboard stores the template
# name as text; this table turns that name into the function to call.
SCENE_DRAWERS = {
    "headline": draw_headline,
    "icon_label": draw_icon_label,
    "person": draw_person,
    "big_number": draw_big_number,
    "versus": draw_versus,
    "bullet_list": draw_bullet_list,
    "timeline": draw_timeline,
    "bar_chart": draw_bar_chart,
    "stamp": draw_stamp,
}


def draw_scene(frame, scene, time_in_scene, duration, colors):
    """
    Draw the animated graphic for one scene at one moment in time.

    Args:
        frame (PIL.Image): The video frame to draw on (already holding
                           the footage background).
        scene (dict): The scene from the storyboard. Its "template" key
                      picks which graphic to draw.
        time_in_scene (float): Seconds since this scene began.
        duration (float): How long this scene lasts, in seconds.
        colors (dict): The colours to use:
                       "accent" — main highlight colour for this scene,
                       "second" — a contrasting highlight colour,
                       "text"   — the normal text colour.

    Example:
        draw_scene(frame, {"template": "stamp", "text": "RARE"}, 0.5, 3.0, colors)
    """
    drawer = SCENE_DRAWERS.get(scene.get("template", ""), draw_headline)
    drawer(frame, scene, time_in_scene, duration, colors)

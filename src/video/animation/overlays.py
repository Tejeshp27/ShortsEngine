# =============================================================
# src/video/animation/overlays.py — things drawn on top of every scene
#
# These are the parts of the video that stay on screen the whole
# time, whichever scene is playing:
#
#   captions      — the spoken words, a few at a time, with the
#                   word being said right now highlighted
#   progress bar  — a thin bar at the top that fills as the video
#                   plays (viewers stay longer when they can see
#                   how much is left)
#   section chip  — a small label such as "WHY IT MATTERS"
#   wipe          — a coloured panel that sweeps across the screen
#                   when the video moves to a new section
#
# How it fits in:
#   renderer.py calls these after draw_scene() on every frame.
# =============================================================

from src.video.animation.drawing import (
    VIDEO_HEIGHT,
    VIDEO_WIDTH,
    draw_circle,
    draw_panel,
    draw_text,
    text_width,
)
from src.video.animation.easing import (
    ease_in_cubic,
    ease_out_cubic,
    lerp,
    progress,
)


# Vertical position of the captions. This is low on the screen but above
# the area that TikTok and Instagram cover with the username and buttons.
CAPTION_CENTER_Y = 1400

# The widest a caption line may be, in pixels.
CAPTION_MAX_WIDTH = 960

# Space between caption words, as a fraction of the font size.
CAPTION_GAP = 0.34

# How long a caption may stay on screen after its last word is spoken,
# if the next caption has not started yet (for example, during a pause).
CAPTION_HOLD_SECONDS = 0.8

# How long each half of the section wipe lasts, in seconds.
WIPE_HALF_SECONDS = 0.2


def _clean_caption_word(word):
    """
    Prepare a spoken word for display in a caption.

    Captions are shown in capitals without commas or full stops, which
    is the standard look for short-form video.

    Args:
        word (str): A word from the voiceover timing, e.g. "cabinet."

    Returns:
        str: e.g. "CABINET". May be "" if the word was only punctuation.
    """
    return word.strip(".,;:!?\"“”()—-").upper()


def build_caption_groups(scenes, word_timings, words_per_caption):
    """
    Group the spoken words into short captions.

    Captions never run across two scenes: each scene's words are grouped
    separately, so the caption changes at the same moment as the graphic.

    Args:
        scenes (list[dict]): Storyboard scenes, each with "word_start"
                             (index of its first word) and "word_count".
        word_timings (list[dict]): One {"word", "start", "end"} per spoken
                                   word, from tts.py.
        words_per_caption (int): How many words to show at once (usually 3).

    Returns:
        list[dict]: One dict per caption:
            {"words": [{"text", "start", "end"}, ...], "start": float, "end": float}

    Example:
        groups = build_caption_groups(scenes, word_timings, 3)
        groups[0]["words"][0]["text"]   # → "A"
    """
    groups = []

    for scene in scenes:
        index = scene["word_start"]
        last = min(scene["word_start"] + scene["word_count"], len(word_timings))

        while index < last:
            group_size = words_per_caption
            # Avoid leaving one lonely word as the scene's final caption:
            # if exactly one word would be left over, include it here.
            if last - index == words_per_caption + 1:
                group_size = words_per_caption + 1

            words = []
            for timing in word_timings[index:min(index + group_size, last)]:
                text = _clean_caption_word(timing["word"])
                if text:
                    words.append({
                        "text": text,
                        "start": timing["start"],
                        "end": timing["end"],
                    })

            if words:
                groups.append({
                    "words": words,
                    "start": words[0]["start"],
                    "end": words[-1]["end"],
                })
            index += group_size

    # Keep each caption on screen until the next one starts, so the
    # captions don't flicker off between words — but not for too long.
    for group_number, group in enumerate(groups):
        longest_end = group["end"] + CAPTION_HOLD_SECONDS
        if group_number + 1 < len(groups):
            next_start = groups[group_number + 1]["start"]
            group["end"] = min(next_start, longest_end)
        else:
            group["end"] = longest_end

    return groups


def find_caption_group(groups, now):
    """
    Find the caption that should be on screen at a given time.

    Args:
        groups (list[dict]): Output of build_caption_groups().
        now (float): The current time in the video, in seconds.

    Returns:
        dict or None: The caption to show, or None during silence.
    """
    for group in groups:
        if group["start"] <= now < group["end"]:
            return group
    return None


def draw_captions(frame, group, now, font_size, text_color, highlight_color):
    """
    Draw the current caption, highlighting the word being spoken.

    Args:
        frame (PIL.Image): The video frame to draw on.
        group (dict or None): The caption from find_caption_group().
        now (float): The current time in the video, in seconds.
        font_size (int): Caption font size from the channel config.
        text_color (tuple): (red, green, blue) for ordinary words.
        highlight_color (tuple): (red, green, blue) for the spoken word.
    """
    if group is None:
        return
    words = group["words"]

    # Measure the whole line, and shrink the font until it fits.
    size = font_size
    while size > 48:
        total_width = 0
        for word in words:
            total_width += text_width(word["text"], size)
        total_width += size * CAPTION_GAP * (len(words) - 1)
        if total_width <= CAPTION_MAX_WIDTH:
            break
        size -= 4

    gap = size * CAPTION_GAP
    total_width = gap * (len(words) - 1)
    for word in words:
        total_width += text_width(word["text"], size)

    # The word being spoken is the last one whose start time has passed.
    active_number = 0
    for word_number, word in enumerate(words):
        if word["start"] <= now:
            active_number = word_number

    # First pass: work out where each word sits, and draw the quiet ones.
    x = (VIDEO_WIDTH - total_width) / 2
    active_center_x = 0
    for word_number, word in enumerate(words):
        word_width = text_width(word["text"], size)
        center_x = x + word_width / 2
        if word_number == active_number:
            active_center_x = center_x
        else:
            draw_text(frame, word["text"], center_x, CAPTION_CENTER_Y, size,
                      text_color)
        x += word_width + gap

    # Second pass: draw the spoken word in the highlight colour. It hops
    # up as it is spoken and settles back, which draws the eye to it
    # without changing its width (so it never bumps into its neighbours).
    active_word = words[active_number]
    hop = ease_out_cubic(progress(now, active_word["start"], 0.18))
    hop_height = lerp(-22, 0, hop)
    draw_text(frame, active_word["text"], active_center_x,
              CAPTION_CENTER_Y + hop_height, size, highlight_color)


def draw_progress_bar(frame, fraction, color):
    """
    Draw the thin bar at the top that fills as the video plays.

    Args:
        frame (PIL.Image): The video frame to draw on.
        fraction (float): How much of the video has played, 0.0 to 1.0.
        color (tuple): (red, green, blue) of the filled part.
    """
    left = 60
    right = VIDEO_WIDTH - 60
    top = 150
    bottom = top + 12

    draw_panel(frame, left, top, right, bottom, (255, 255, 255), 0.28, radius=6)
    filled_right = left + (right - left) * fraction
    # Wait until the bar is at least as wide as it is tall, so the
    # rounded ends draw correctly.
    if filled_right - left >= 12:
        draw_panel(frame, left, top, filled_right, bottom, color, radius=6)


def draw_section_chip(frame, label, time_in_section, color):
    """
    Draw a small label near the top showing which part of the video this is.

    The chip drops into place each time a new section starts.

    Args:
        frame (PIL.Image): The video frame to draw on.
        label (str): e.g. "WHY IT MATTERS". Nothing is drawn if empty.
        time_in_section (float): Seconds since this section began.
        color (tuple): (red, green, blue) of the dot beside the label.
    """
    if not label:
        return

    amount = ease_out_cubic(progress(time_in_section, 0.15, 0.35))
    if amount <= 0.0:
        return

    size = 38
    label_width = text_width(label, size, "bold")
    chip_width = label_width + 110
    center_y = 236 - 40 * (1 - amount)
    left = (VIDEO_WIDTH - chip_width) / 2

    draw_panel(frame, left, center_y - 36, left + chip_width, center_y + 36,
               (10, 12, 26), 0.75 * amount, radius=36)
    draw_circle(frame, left + 42, center_y, 11, color, amount)
    draw_text(frame, label, left + 70, center_y, size, (255, 255, 255),
              weight="bold", alpha=amount, outline=False, anchor="lm")


def draw_wipe(frame, now, cut_time, color):
    """
    Draw a coloured panel sweeping across the screen around a section change.

    Just before the cut the panel grows from the left until it covers the
    screen; just after the cut it slides away to the right, revealing the
    new section. The switch happens while the screen is fully covered.

    Args:
        frame (PIL.Image): The video frame to draw on.
        now (float): The current time in the video, in seconds.
        cut_time (float): The moment the new section starts.
        color (tuple): (red, green, blue) of the panel.
    """
    if cut_time - WIPE_HALF_SECONDS <= now < cut_time:
        # Covering: the panel's right edge moves from the left side across.
        amount = ease_in_cubic(progress(now, cut_time - WIPE_HALF_SECONDS,
                                        WIPE_HALF_SECONDS))
        draw_panel(frame, 0, 0, VIDEO_WIDTH * amount, VIDEO_HEIGHT, color, radius=0)

    elif cut_time <= now < cut_time + WIPE_HALF_SECONDS:
        # Uncovering: the panel's left edge moves across to the right side.
        amount = ease_out_cubic(progress(now, cut_time, WIPE_HALF_SECONDS))
        draw_panel(frame, VIDEO_WIDTH * amount, 0, VIDEO_WIDTH, VIDEO_HEIGHT,
                   color, radius=0)

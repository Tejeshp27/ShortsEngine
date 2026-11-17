# =============================================================
# src/video/animation/overlays.py — things drawn on top of every scene
#
# These are the parts of the video that stay on screen the whole
# time, whichever scene is playing:
#
#   captions      — the spoken words, 1-3 at a time, with the key
#                   word in the accent colour and the word being said
#                   right now hopping up; always inside the safe zone
#   brand bug     — the channel's name in a small pill, top left
#   end card      — the channel's name and slogan at the very end
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
    SAFE_LEFT,
    SAFE_RIGHT,
    SAFE_TOP,
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


# Vertical position of the captions: low on the screen, but above the
# area the apps cover with the username and description (SAFE_BOTTOM).
CAPTION_CENTER_Y = 1370
# Captions are centred inside the safe zone, which is slightly left of
# the screen's middle because the like/comment buttons sit on the right.
CAPTION_CENTER_X = (SAFE_LEFT + SAFE_RIGHT) / 2
# The widest a caption line may be, in pixels (the safe zone, less a margin).
CAPTION_MAX_WIDTH = SAFE_RIGHT - SAFE_LEFT - 20
# The smallest font a long caption may shrink to.
CAPTION_MIN_SIZE = 56
# A caption is ended early if its words would need a font smaller than
# this fraction of the channel's size to fit (long words get fewer per line).
CAPTION_COMFORT_FRACTION = 0.8
# Space between caption words, as a fraction of the font size.
CAPTION_GAP = 0.30
# How long a caption may stay on screen after its last word is spoken,
# if the next caption has not started yet (for example, during a pause).
CAPTION_HOLD_SECONDS = 0.6
# How long each half of the section wipe lasts, in seconds.
WIPE_HALF_SECONDS = 0.2
# Height of the top row (brand bug on the left, section label on the
# right), just below the apps' own top bar.
TOP_ROW_Y = SAFE_TOP + 50

# Small words that are never the caption's key word.
CAPTION_STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "of", "to", "in", "on", "at", "for",
    "with", "from", "by", "is", "are", "was", "were", "be", "been", "it", "its",
    "that", "this", "these", "those", "they", "their", "them", "he", "she", "his",
    "her", "we", "you", "your", "our", "i", "as", "so", "if", "than", "then",
    "what", "which", "who", "how", "when", "where", "why", "about", "into",
    "just", "also", "has", "have", "had", "will", "would", "could", "can",
    "not", "no", "now", "here", "there", "that's", "it's", "here's",
}

# Spoken number words count as key words, like digits do.
CAPTION_NUMBER_WORDS = {
    "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
    "ten", "twenty", "thirty", "forty", "fifty", "hundred", "thousand",
    "million", "billion", "trillion", "percent", "half",
}


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


def ends_phrase(word):
    """
    Check whether a spoken word ends a phrase (a natural caption break).

    Args:
        word (str): The word as spoken, with its punctuation.

    Returns:
        bool: True if it ends with . , ? ! ; : or a dash.
    """
    return word.rstrip("\"”)").endswith((".", ",", "?", "!", ";", ":", "—"))


def choose_keyword(raw_words):
    """
    Pick the most important word of a caption, to show in the accent colour.

    Order of preference: a number, then a name (a capitalised word), then
    the longest ordinary word. Small words like "the" are never chosen.

    Args:
        raw_words (list[str]): The caption's words as written in the script
                               (with capitals and punctuation).

    Returns:
        int: The position of the key word in the list, or -1 for none.

    Example:
        choose_keyword(["Seventeen", "control", "centers"])   # → 0
        choose_keyword(["the", "FAA", "said"])                # → 1
    """
    cleaned = []
    for word in raw_words:
        cleaned.append(word.strip(".,;:!?\"'“”()—-"))

    for index, word in enumerate(cleaned):
        has_digit = any(character.isdigit() for character in word)
        if has_digit or word.lower() in CAPTION_NUMBER_WORDS:
            return index

    best_index = -1
    best_length = 0
    for index, word in enumerate(cleaned):
        if word.lower() in CAPTION_STOPWORDS or not word:
            continue
        if word[0].isupper() and len(word) > best_length:
            best_index = index
            best_length = len(word)
    if best_index != -1:
        return best_index

    for index, word in enumerate(cleaned):
        if word.lower() in CAPTION_STOPWORDS:
            continue
        if len(word) >= 4 and len(word) > best_length:
            best_index = index
            best_length = len(word)
    return best_index


def line_width(texts, size):
    """
    Measure how wide a line of caption words is at a font size.

    Args:
        texts (list[str]): The words, already in capitals.
        size (int): Font size in pixels.

    Returns:
        float: The width in pixels, gaps included.
    """
    width = size * CAPTION_GAP * (len(texts) - 1)
    for text in texts:
        width += text_width(text, size)
    return width


def build_caption_groups(scenes, word_timings, words_per_caption, font_size=None):
    """
    Group the spoken words into short captions of 1 to words_per_caption words.

    A caption ends early at a comma or full stop, so captions follow the
    natural phrases of the speech. Captions never run across two scenes,
    so the caption changes at the same moment as the graphic.

    Args:
        scenes (list[dict]): Storyboard scenes, each with "word_start"
                             (index of its first word) and "word_count".
        word_timings (list[dict]): One {"word", "start", "end"} per spoken
                                   word, from tts.py.
        words_per_caption (int): The most words shown at once (3).
        font_size (int or None): The channel's caption size. When given, a
            caption also ends early if one more word would not fit the safe
            zone at a comfortable size ("AFFECTING TRAVELERS NATIONWIDE"
            becomes two captions).

    Returns:
        list[dict]: One dict per caption:
            {"words": [{"text", "start", "end"}, ...], "keyword": int,
             "start": float, "end": float}

    Example:
        groups = build_caption_groups(scenes, word_timings, 3)
        groups[0]["words"][0]["text"]   # → "ONE"
    """
    groups = []
    for scene in scenes:
        index = scene["word_start"]
        last = min(scene["word_start"] + scene["word_count"], len(word_timings))
        current = []
        raw = []
        limit = words_per_caption
        while index < last:
            if not current:
                # Avoid a lonely last word: 4 words left become 2 + 2, not 3 + 1.
                limit = words_per_caption
                if last - index == words_per_caption + 1 and words_per_caption > 2:
                    limit = 2
            timing = word_timings[index]
            text = _clean_caption_word(timing["word"])
            if text and current and font_size:
                # Would this word make the line too wide? Then close the
                # caption first and start a new one with this word.
                texts = []
                for word in current:
                    texts.append(word["text"])
                texts.append(text)
                if line_width(texts, int(font_size * CAPTION_COMFORT_FRACTION)) > CAPTION_MAX_WIDTH:
                    groups.append({"words": current, "keyword": choose_keyword(raw),
                                   "start": current[0]["start"], "end": current[-1]["end"]})
                    current = []
                    raw = []
            if text:
                current.append({"text": text, "start": timing["start"], "end": timing["end"]})
                raw.append(timing["word"])
            full = len(current) >= limit
            if current and (full or ends_phrase(timing["word"]) or index + 1 == last):
                groups.append({"words": current, "keyword": choose_keyword(raw),
                               "start": current[0]["start"], "end": current[-1]["end"]})
                current = []
                raw = []
            index += 1

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


def caption_layout(group, font_size):
    """
    Work out the size and position of every word in a caption.

    The font shrinks until the whole line fits inside the safe zone.

    Args:
        group (dict): One caption from build_caption_groups().
        font_size (int): The channel's caption font size.

    Returns:
        tuple: (size, positions) where positions is a list of
               {"text", "center_x", "width"} — one per word.
    """
    words = group["words"]
    texts = []
    for word in words:
        texts.append(word["text"])
    size = font_size
    total_width = line_width(texts, size)
    while total_width > CAPTION_MAX_WIDTH and size > CAPTION_MIN_SIZE:
        size -= 4
        total_width = line_width(texts, size)

    positions = []
    x = CAPTION_CENTER_X - total_width / 2
    for word in words:
        width = text_width(word["text"], size)
        positions.append({"text": word["text"], "center_x": x + width / 2, "width": width})
        x += width + size * CAPTION_GAP
    return size, positions


def caption_box(group, font_size):
    """
    Get the rectangle a caption covers, including its outline and hop.

    The quality gate uses this to prove captions stay in the safe zone.

    Args:
        group (dict): One caption from build_caption_groups().
        font_size (int): The channel's caption font size.

    Returns:
        tuple: (left, top, right, bottom) in pixels.
    """
    size, positions = caption_layout(group, font_size)
    outline = max(3, int(size * 0.07))
    left = positions[0]["center_x"] - positions[0]["width"] / 2 - outline
    right = positions[-1]["center_x"] + positions[-1]["width"] / 2 + outline
    # Capital letters are about 0.72 of the font size tall; the spoken
    # word hops up by up to 22 pixels.
    top = CAPTION_CENTER_Y - size * 0.36 - outline - 22
    bottom = CAPTION_CENTER_Y + size * 0.36 + outline
    return left, top, right, bottom


def draw_captions(frame, group, now, font_size, text_color, highlight_color):
    """
    Draw the current caption: the key word in the accent colour, and the
    word being spoken hopping up.

    Args:
        frame (PIL.Image): The video frame to draw on.
        group (dict or None): The caption from find_caption_group().
        now (float): The current time in the video, in seconds.
        font_size (int): Caption font size from the channel config.
        text_color (tuple): (red, green, blue) for ordinary words.
        highlight_color (tuple): (red, green, blue) for the key word.
    """
    if group is None:
        return
    size, positions = caption_layout(group, font_size)

    # The word being spoken is the last one whose start time has passed.
    active_number = 0
    for word_number, word in enumerate(group["words"]):
        if word["start"] <= now:
            active_number = word_number

    for word_number, position in enumerate(positions):
        color = text_color
        if word_number == group.get("keyword", -1):
            color = highlight_color
        lift = 0
        if word_number == active_number:
            # The spoken word hops up and settles back. Its width never
            # changes, so it never bumps into its neighbours.
            word = group["words"][word_number]
            hop = ease_out_cubic(progress(now, word["start"], 0.18))
            lift = lerp(-22, 0, hop)
        draw_text(frame, position["text"], position["center_x"], CAPTION_CENTER_Y + lift,
                  size, color)


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

    size = 34
    label_width = text_width(label, size, "bold")
    chip_width = label_width + 100
    center_y = TOP_ROW_Y - 40 * (1 - amount)
    # Right-aligned, so the channel's brand bug fits on the left.
    left = VIDEO_WIDTH - 60 - chip_width

    draw_panel(frame, left, center_y - 36, left + chip_width, center_y + 36,
               (10, 12, 26), 0.75 * amount, radius=36)
    draw_circle(frame, left + 40, center_y, 10, color, amount)
    draw_text(frame, label, left + 64, center_y, size, (255, 255, 255),
              weight="bold", alpha=amount, outline=False, anchor="lm")


def draw_brand_bug(frame, brand_name, color):
    """
    Draw the channel's name in a small pill at the top left, on every frame.

    A consistent "bug" like this is how viewers recognise a channel's
    videos at a glance.

    Args:
        frame (PIL.Image): The video frame to draw on.
        brand_name (str): e.g. "NEWS MADE SIMPLE". Nothing is drawn if empty.
        color (tuple): (red, green, blue) of the dot.
    """
    if not brand_name:
        return
    size = 30
    width = text_width(brand_name, size, "bold") + 84
    left = 60
    draw_panel(frame, left, TOP_ROW_Y - 32, left + width, TOP_ROW_Y + 32,
               (10, 12, 26), 0.6, radius=32)
    draw_circle(frame, left + 34, TOP_ROW_Y, 9, color)
    draw_text(frame, brand_name, left + 56, TOP_ROW_Y, size, (255, 255, 255),
              weight="bold", outline=False, anchor="lm")


def draw_end_card(frame, now, start, brand_name, slogan, accent, text_color):
    """
    Draw the closing card: the channel's name and slogan over a dark panel.

    Args:
        frame (PIL.Image): The video frame to draw on.
        now (float): The current time in the video, in seconds.
        start (float): When the end card begins.
        brand_name (str): e.g. "NEWS MADE SIMPLE".
        slogan (str): e.g. "That's the news, made simple."
        accent (tuple): (red, green, blue) for the last word of the name.
        text_color (tuple): (red, green, blue) for the rest.
    """
    if now < start or not brand_name:
        return
    fade = ease_out_cubic(progress(now, start, 0.35))
    draw_panel(frame, 0, 0, VIDEO_WIDTH, VIDEO_HEIGHT, (10, 12, 26), 0.82 * fade, radius=0)

    # The name on two lines: everything but the last word, then the last
    # word big in the accent colour ("NEWS MADE" / "SIMPLE").
    words = brand_name.upper().split()
    first_line = " ".join(words[:-1])
    last_word = words[-1]
    rise = 60 * (1 - ease_out_cubic(progress(now, start + 0.1, 0.4)))
    name_alpha = progress(now, start + 0.1, 0.3)
    if first_line:
        draw_text(frame, first_line, VIDEO_WIDTH / 2, 760 + rise, 96, text_color, alpha=name_alpha)
    draw_text(frame, last_word, VIDEO_WIDTH / 2, 880 + rise, 150, accent, alpha=name_alpha)

    # An underline grows from the middle, then the slogan fades in.
    grow = ease_out_cubic(progress(now, start + 0.35, 0.4))
    half = 260 * grow
    draw_panel(frame, VIDEO_WIDTH / 2 - half, 975, VIDEO_WIDTH / 2 + half, 987, accent, radius=6)
    if slogan:
        draw_text(frame, slogan, VIDEO_WIDTH / 2, 1060, 46, text_color, weight="bold",
                  alpha=progress(now, start + 0.5, 0.35))


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

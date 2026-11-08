# =============================================================
# src/video/captions.py — build a subtitle file from word timings
#
# Takes the list of word-level timestamps from tts.py and groups
# them into short caption lines (2-4 words each). The result is
# an ASS subtitle file (.ass), which lets FFmpeg burn styled
# captions into the video — big, centered, with a shadow.
#
# ASS (Advanced SubStation Alpha) is a subtitle format that
# supports colours, fonts and positioning. It is what most
# short-form creators use because it looks far better than
# plain SRT captions.
# =============================================================

import os


# How many words to show on screen at once. 3 is the standard for
# fast-paced short-form content — it matches how the words are spoken.
WORDS_PER_CAPTION = 3

# Where caption files are saved before being passed to FFmpeg.
CAPTIONS_FOLDER = os.path.join("cache", "captions")


def _group_words(word_timings, words_per_caption):
    """
    Split a flat list of word timings into short caption groups.

    Each group covers WORDS_PER_CAPTION consecutive words and spans
    from the start of its first word to the end of its last.

    Args:
        word_timings (list[dict]): [{"word": str, "start": float, "end": float}, ...]
        words_per_caption (int): How many words per caption line.

    Returns:
        list[dict]: [{"text": str, "start": float, "end": float}, ...]

    Example:
        words = [
            {"word": "Big",   "start": 0.0, "end": 0.3},
            {"word": "news",  "start": 0.3, "end": 0.6},
            {"word": "today", "start": 0.6, "end": 0.9},
        ]
        _group_words(words, 2)
        # → [{"text": "Big news", "start": 0.0, "end": 0.6},
        #    {"text": "today",    "start": 0.6, "end": 0.9}]
    """
    groups = []
    index = 0

    while index < len(word_timings):
        chunk = word_timings[index : index + words_per_caption]
        text = " ".join(item["word"] for item in chunk)
        start = chunk[0]["start"]
        end = chunk[-1]["end"]
        groups.append({"text": text, "start": start, "end": end})
        index += words_per_caption

    return groups


def _seconds_to_ass_time(seconds):
    """
    Convert a number of seconds to the ASS time format: H:MM:SS.cs

    ASS uses centiseconds (hundredths of a second) not milliseconds.

    Args:
        seconds (float): Time in seconds, e.g. 73.456.

    Returns:
        str: ASS time string, e.g. "0:01:13.46".

    Example:
        _seconds_to_ass_time(73.456)   # → "0:01:13.46"
    """
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    centiseconds = int((seconds - int(seconds)) * 100)
    return f"{hours}:{minutes:02d}:{secs:02d}.{centiseconds:02d}"


def _build_ass_header(font_name, font_size, video_width, video_height):
    """
    Return the header section of an ASS subtitle file.

    The header sets the canvas size, default font, colour and
    position. All the styling values are defined here so there
    is one place to tweak the look.

    Args:
        font_name (str): Font family, e.g. "Arial".
        font_size (int): Font size in pixels.
        video_width (int): Video width, e.g. 1080.
        video_height (int): Video height, e.g. 1920.

    Returns:
        str: The ASS header block.
    """
    # Colour format in ASS is &HAABBGGRR (alpha, blue, green, red).
    # &H00FFFFFF = fully opaque white.
    # &H80000000 = semi-transparent black shadow.
    return f"""[Script Info]
ScriptType: v4.00+
PlayResX: {video_width}
PlayResY: {video_height}
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font_name},{font_size},&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,3,2,2,10,10,80,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def build_subtitle_file(word_timings, output_path,
                        font_name="Arial", font_size=90,
                        video_width=1080, video_height=1920):
    """
    Turn word timings into an ASS subtitle file and save it to disk.

    Args:
        word_timings (list[dict]): Word timing data from tts.py.
        output_path (str): Where to save the .ass file.
        font_name (str): Caption font. Arial is universally available.
        font_size (int): Font size in pixels on a 1080×1920 canvas.
                         90 is readable on mobile screens.
        video_width (int): Width of the video (1080 for 9:16).
        video_height (int): Height of the video (1920 for 9:16).

    Returns:
        str: The path of the saved .ass file.

    Example:
        path = build_subtitle_file(timings, "cache/captions/abc.ass")
    """
    if not word_timings:
        raise ValueError("No word timings provided — cannot build captions.")

    groups = _group_words(word_timings, WORDS_PER_CAPTION)
    header = _build_ass_header(font_name, font_size, video_width, video_height)

    lines = [header]
    for group in groups:
        start = _seconds_to_ass_time(group["start"])
        end = _seconds_to_ass_time(group["end"])
        # {\an2} positions the text at the bottom-centre (alignment 2).
        # {\b1} makes it bold. These are inline ASS override tags.
        text = r"{\an2\b1}" + group["text"]
        lines.append(f"Dialogue: 0,{start},{end},Default,,0,0,0,,{text}")

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    return output_path

# =============================================================
# src/video/clip_render.py — turn a stream clip into a vertical
#                            1080x1920 MP4 (step L.5)
#
# The video is ONLY the real clip: no animations, no stock footage,
# no channel logo. On top of it we draw three things:
#   1. the hook title, during the first few seconds
#   2. word-by-word captions from the Whisper transcript
#      (the word being spoken is highlighted)
#   3. a small streamer credit tag in the top-left corner for the
#      first few seconds (post captions always credit the streamer too)
#
# How it works (two FFmpeg programs with Python in the middle):
#   FFmpeg #1 ("decoder") cuts the chosen moment and does ALL picture
#     work in one filter graph: layout, lanczos resizing, sharpening
#     of enlarged panels (see clip_layout.py). It sends plain,
#     uncompressed RGB pictures to Python through a pipe.
#   Python draws the text on each picture with Pillow.
#   FFmpeg #2 ("encoder") receives the pictures, adds the cleaned and
#     bleeped sound (clip_audio.py), applies the final loudness pass
#     and compresses everything ONCE into the MP4 (H.264 + AAC).
#   Nothing is compressed twice: the pipe and the WAV files in between
#   are uncompressed, so no quality is lost before the final encode.
#
# Why draw text in Python instead of FFmpeg's subtitle/text filters:
# the FFmpeg installed on this Mac was built without them, and the
# animated news videos already draw their text with Pillow, so both
# video types share one text style and one set of fonts.
# =============================================================

import json
import logging
import os
import subprocess

from PIL import Image, ImageDraw, ImageFont

from src.video.animation.drawing import (
    SAFE_LEFT,
    SAFE_RIGHT,
    draw_text,
    get_font,
    hex_to_rgb,
    text_width,
    wrap_text,
)
from src.video.clip_layout import (
    OUTPUT_HEIGHT,
    OUTPUT_WIDTH,
    build_video_filter,
    plan_layout,
    text_positions,
)

logger = logging.getLogger(__name__)

# Used when FFprobe cannot tell a video's frame rate.
DEFAULT_FRAMES_PER_SECOND = 30

# A caption group ends at a pause longer than this (seconds), so a
# new sentence after a pause starts on a fresh caption.
CAPTION_PAUSE_SECONDS = 0.7
# Keep a finished caption on screen this much longer, so short words
# don't flash by.
CAPTION_LINGER_SECONDS = 0.4

# Where the credit tag sits: the top-left corner, below the apps' top
# tabs ("Following | For You") and inside the side margin.
CREDIT_LEFT = SAFE_LEFT
CREDIT_CENTER_Y = 250

# Hook title box colours: black text on a white box reads on any video.
HOOK_BOX_COLOR = (255, 255, 255, 240)
HOOK_TEXT_COLOR = (15, 15, 15)
# The credit sits on a see-through dark pill.
CREDIT_BOX_COLOR = (0, 0, 0, 150)
_caption_fonts = {}


# ---------------------------------------------------------------------------
# Reading the clip
# ---------------------------------------------------------------------------

def probe_video(path):
    """
    Ask FFprobe for a video's size, frame rate, length and whether it has sound.

    Args:
        path (str): The video file.

    Returns:
        dict: {"width": 1920, "height": 1080, "fps": 60.0, "duration": 31.5, "has_audio": True}

    Raises:
        ValueError: If the file has no video picture.
    """
    result = subprocess.run([
        "ffprobe", "-v", "error",
        "-show_entries", "stream=codec_type,width,height,r_frame_rate,avg_frame_rate:format=duration",
        "-of", "json", path,
    ], capture_output=True, text=True, check=True)
    data = json.loads(result.stdout)

    width = 0
    height = 0
    fps = 0.0
    has_audio = False
    for stream in data.get("streams", []):
        if stream.get("codec_type") == "video" and not width:
            width = int(stream.get("width", 0))
            height = int(stream.get("height", 0))
            fps = frame_rate_value(stream.get("r_frame_rate")) or frame_rate_value(stream.get("avg_frame_rate"))
        if stream.get("codec_type") == "audio":
            has_audio = True
    if not width or not height:
        raise ValueError(f"{path} has no video picture.")

    duration = float(data.get("format", {}).get("duration", 0) or 0)
    return {"width": width, "height": height, "fps": fps, "duration": duration, "has_audio": has_audio}


def frame_rate_value(text):
    """
    Turn FFprobe's frame-rate text into a number.

    Args:
        text (str or None): For example "60/1" or "30000/1001".

    Returns:
        float: For example 60.0 or 29.97; 0.0 when unknown.
    """
    if not text or "/" not in text:
        return 0.0
    top, bottom = text.split("/", 1)
    try:
        if float(bottom) == 0:
            return 0.0
        return float(top) / float(bottom)
    except ValueError:
        return 0.0


def output_frame_rate(source_fps, encoding):
    """
    Choose the finished video's frame rate.

    Args:
        source_fps (float): The source's frame rate (0 when unknown).
        encoding (ClipEncodingSettings): frame_rate ("source" or a number)
                                         and max_frame_rate.

    Returns:
        int: For example 60 for a 60 fps stream with frame_rate "source".
    """
    if encoding.frame_rate != "source":
        return encoding.frame_rate
    if source_fps <= 0:
        return DEFAULT_FRAMES_PER_SECOND
    # 59.94 becomes 60 and 29.97 becomes 30; never above the maximum.
    return max(24, min(round(source_fps), encoding.max_frame_rate))


def words_in_moment(words, start, end):
    """
    Keep the words spoken inside the moment and make their times start at 0.

    Args:
        words (list[dict]): The whole clip's timed words.
        start (float): The moment's start in the clip, in seconds.
        end (float): The moment's end.

    Returns:
        list[dict]: The moment's words, with times counted from its start.

    Example:
        words_in_moment([{"word": "hi", "start": 10.0, "end": 10.4}], 9.0, 20.0)
        # → [{"word": "hi", "start": 1.0, "end": 1.4}]
    """
    kept = []
    for word in words:
        if word["end"] <= start or word["start"] >= end:
            continue
        # Copy the word (including any bleep flags), then move its times.
        moved = dict(word)
        moved["start"] = round(max(word["start"], start) - start, 3)
        moved["end"] = round(min(word["end"], end) - start, 3)
        kept.append(moved)
    return kept


# ---------------------------------------------------------------------------
# Captions
# ---------------------------------------------------------------------------

def ends_sentence(word):
    """
    Check whether a word ends a sentence (so the caption should break after it).

    Args:
        word (str): One word, maybe with punctuation.

    Returns:
        bool: True for words ending in . ! or ?
    """
    return word.endswith(".") or word.endswith("!") or word.endswith("?")


def group_caption_words(words, words_per_caption):
    """
    Split timed words into short caption groups.

    A group ends after `words_per_caption` words, at the end of a
    sentence, or before a long pause.

    Args:
        words (list[dict]): Timed words, times counted from the moment's start.
        words_per_caption (int): The most words on screen at once.

    Returns:
        list[dict]: [{"words": [...timed words...], "start": s, "end": s}, ...]
    """
    groups = []
    current = []
    for index, word in enumerate(words):
        current.append(word)
        is_last = index == len(words) - 1
        pause_follows = not is_last and words[index + 1]["start"] - word["end"] > CAPTION_PAUSE_SECONDS
        if is_last or len(current) >= words_per_caption or ends_sentence(word["word"]) or pause_follows:
            groups.append({"words": current, "start": current[0]["start"], "end": current[-1]["end"]})
            current = []
    return groups


def caption_at(groups, now):
    """
    Find which caption group is on screen at a moment, and its spoken word.

    Args:
        groups (list[dict]): From group_caption_words().
        now (float): Seconds since the start of the video.

    Returns:
        tuple: (group number, word number) or (None, None) when no
               caption should show (before the first word, or in a pause).
    """
    for group_number, group in enumerate(groups):
        # Groups are in time order, so if this one hasn't started yet,
        # no later one has either.
        if now < group["start"]:
            return None, None
        # A group stays up a little after its last word, but never
        # past the moment the next group starts.
        hide_at = group["end"] + CAPTION_LINGER_SECONDS
        if group_number + 1 < len(groups):
            hide_at = min(hide_at, groups[group_number + 1]["start"])
        if now < hide_at:
            return group_number, spoken_word_number(group, now)
    return None, None


def spoken_word_number(group, now):
    """
    Find which word of a caption group is being spoken.

    Args:
        group (dict): One caption group.
        now (float): Seconds since the start of the video.

    Returns:
        int: The number (0, 1, 2...) of the latest word that has started.
    """
    current = 0
    for word_number, word in enumerate(group["words"]):
        if word["start"] <= now:
            current = word_number
    return current


def draw_caption(layer, group, active_word, settings, center_y):
    """
    Draw one caption line, with the spoken word in the highlight colour.

    The line stays between SAFE_LEFT and SAFE_RIGHT, so it never reaches
    the like/comment/share buttons on the right edge of the apps.

    Args:
        layer (PIL.Image): A transparent picture to draw on.
        group (dict): The caption group.
        active_word (int): Which word to highlight.
        settings (CaptionSettings): Size and colours.
        center_y (int): The height of the line's middle (from clip_layout.text_positions).
    """
    texts = []
    for word in group["words"]:
        texts.append(word["word"])
    line = " ".join(texts)
    size = settings.font_size
    font = caption_font(settings.font_path, size)
    max_width = SAFE_RIGHT - SAFE_LEFT - settings.outline_width * 2
    while font.getlength(line) > max_width and size > 24:
        size -= 4
        font = caption_font(settings.font_path, size)
    if font.getlength(line) > max_width:
        raise ValueError("A caption is too wide for the safe area. Use fewer words per caption or shorten the transcript word.")

    # Draw the words one by one from left to right, so each word can
    # have its own colour. The line as a whole is centred.
    space = font.getlength(" ")
    x = (SAFE_LEFT + SAFE_RIGHT - font.getlength(line)) / 2
    draw = ImageDraw.Draw(layer)
    for word_number, text in enumerate(texts):
        color = hex_to_rgb(settings.text_color)
        if word_number == active_word:
            color = hex_to_rgb(settings.highlight_color)
        draw.text((x, center_y), text, font=font, fill=color, anchor="lm",
                  stroke_width=settings.outline_width, stroke_fill=hex_to_rgb(settings.outline_color))
        x += font.getlength(text) + space


def caption_font(path, size):
    """
    Load a configured local TTF/OTF font path at a size; return a cached font or the bundled Montserrat default.

    Args:
        path: The local file path to read or create.
        size: A font size in pixels, or a video size in bytes in transfer tests.

    Returns:
        A reusable Pillow font at the requested size.
    """
    if not path:
        return get_font(size)
    key = (path, size)
    if key not in _caption_fonts:
        try:
            _caption_fonts[key] = ImageFont.truetype(path, size)
        except OSError as error:
            raise ValueError("Caption font could not be loaded. Check captions.font_path in the channel YAML.") from error
    return _caption_fonts[key]


# ---------------------------------------------------------------------------
# Hook title and credit
# ---------------------------------------------------------------------------

def draw_hook_title(layer, title, center_y, settings):
    """
    Draw the hook title: dark words on a white rounded box.

    Args:
        layer (PIL.Image): A transparent picture to draw on.
        title (str): The hook title (at most a few words).
        center_y (int): Where the middle of the box goes.
        settings (HookTitleSettings): The font size.
    """
    max_width = SAFE_RIGHT - SAFE_LEFT - 60
    size = settings.font_size
    lines = wrap_text(title, size, max_width)
    # Never more than two lines: shrink the text until it fits in two.
    while len(lines) > 2 and size > 44:
        size -= 6
        lines = wrap_text(title, size, max_width)

    line_height = int(size * 1.15)
    widest = 0
    for line in lines:
        widest = max(widest, text_width(line, size))
    box_width = widest + 70
    box_height = line_height * len(lines) + 50
    left = OUTPUT_WIDTH / 2 - box_width / 2
    top = center_y - box_height / 2

    draw = ImageDraw.Draw(layer)
    draw.rounded_rectangle([left, top, left + box_width, top + box_height],
                           radius=28, fill=HOOK_BOX_COLOR)
    y = top + 25 + line_height / 2
    for line in lines:
        draw_text(layer, line, OUTPUT_WIDTH / 2, y, size, HOOK_TEXT_COLOR, outline=False)
        y += line_height


def credit_icon(size):
    """
    Get the camera icon shown before the credit (🎥), if available.

    Args:
        size (int): Icon size in pixels.

    Returns:
        PIL.Image or None: The icon, or None when it can't be loaded
                           (the credit then shows text only).
    """
    from src.video.animation.icons import get_icon
    try:
        return get_icon("🎥", size)
    except OSError:
        return None


def draw_credit(layer, credit_text, settings):
    """
    Draw the small streamer credit tag in the top-left corner, e.g. "🎥 twitch.tv/streamer".

    Args:
        layer (PIL.Image): A transparent picture to draw on.
        credit_text (str): For example "twitch.tv/streamer".
        settings (CreditSettings): Whether the overlay is enabled and its font size.
    """
    if not settings.enabled:
        return
    size = settings.font_size
    icon = credit_icon(int(size * 1.1))
    icon_space = 0
    if icon is not None:
        icon_space = icon.width + 14

    content_width = icon_space + text_width(credit_text, size, "bold")
    pill_width = content_width + 48
    pill_height = size + 34
    left = CREDIT_LEFT
    top = CREDIT_CENTER_Y - pill_height / 2

    draw = ImageDraw.Draw(layer)
    draw.rounded_rectangle([left, top, left + pill_width, top + pill_height],
                           radius=pill_height / 2, fill=CREDIT_BOX_COLOR)
    x = left + 24
    if icon is not None:
        layer.alpha_composite(icon, (int(x), int(CREDIT_CENTER_Y - icon.height / 2)))
        x += icon_space
    draw_text(layer, credit_text, x, CREDIT_CENTER_Y, size, (255, 255, 255),
              weight="bold", outline=False, anchor="lm")


def cut_to_content(layer):
    """
    Trim a transparent layer to the part that has something drawn on it.

    Pasting a small piece is much faster than pasting a whole
    1080x1920 picture onto every frame.

    Args:
        layer (PIL.Image): A transparent picture.

    Returns:
        tuple or None: (piece, (left, top)), or None if nothing was drawn.
    """
    box = layer.getbbox()
    if box is None:
        return None
    return layer.crop(box), (box[0], box[1])


def make_layer(draw_function, *arguments):
    """
    Draw something on a new transparent layer and trim it.

    Args:
        draw_function: One of the draw_... functions above.
        *arguments: The values to pass to it after the layer.
                    ("*arguments" collects any number of values.)

    Returns:
        tuple or None: From cut_to_content().
    """
    layer = Image.new("RGBA", (OUTPUT_WIDTH, OUTPUT_HEIGHT), (0, 0, 0, 0))
    draw_function(layer, *arguments)
    return cut_to_content(layer)


def paste_layer(frame, piece):
    """
    Put a trimmed layer on top of a video frame.

    Args:
        frame (PIL.Image): The video frame (RGB).
        piece (tuple or None): From make_layer(); None draws nothing.
    """
    if piece is None:
        return
    image, position = piece
    # The layer's own see-through-ness (alpha) is used as the mask.
    frame.paste(image, position, image)


# ---------------------------------------------------------------------------
# FFmpeg commands
# ---------------------------------------------------------------------------

def decoder_command(clip_path, start, duration, video_filter):
    """
    Build the FFmpeg command that cuts the moment and sends raw frames to Python.

    Args:
        clip_path (str): The clip's video file.
        start (float): Where the moment starts in the clip, in seconds.
        duration (float): How long the moment is.
        video_filter (str): From clip_layout.build_video_filter().

    Returns:
        list[str]: The command.
    """
    return [
        "ffmpeg", "-nostdin", "-v", "error",
        "-ss", f"{start:.3f}",          # jump to the moment's start (before -i = fast seek)
        "-t", f"{duration:.3f}",        # read only the moment's length
        "-i", clip_path,
        "-filter_complex", video_filter,
        "-map", "[out]",                # take the stacked 1080x1920 picture
        "-f", "rawvideo",               # send plain, uncompressed pictures...
        "-pix_fmt", "rgb24",            # ...as 3 bytes (red, green, blue) per pixel...
        "-",                            # ...to the pipe, where Python reads them
    ]


def encoder_command(audio_path, duration, output_path, frame_rate, encoding, audio_filter=None):
    """
    Build the FFmpeg command that turns Python's frames + the sound into an MP4.

    This is the ONE place the video and sound are compressed.

    Args:
        audio_path (str or None): The cleaned sound (WAV), or None for silence.
        duration (float): The video's length in seconds.
        output_path (str): Where to write the MP4.
        frame_rate (int): Frames per second (the same as the decoder's).
        encoding (ClipEncodingSettings): crf, preset and profile.
        audio_filter (str or None): The final loudness pass (loudnorm pass 2).

    Returns:
        list[str]: The command.
    """
    command = [
        "ffmpeg", "-nostdin", "-y",
        "-f", "rawvideo", "-pix_fmt", "rgb24",       # frames arrive as raw RGB...
        "-s", f"{OUTPUT_WIDTH}x{OUTPUT_HEIGHT}",     # ...1080x1920 each...
        "-framerate", str(frame_rate),               # ...at the decoder's frame rate...
        "-i", "-",                                   # ...through the pipe
    ]
    if audio_path:
        command += ["-i", audio_path]
    else:
        # A clip without sound gets silence, because TikTok expects an audio track.
        command += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
    command += [
        "-map", "0:v", "-map", "1:a",
        "-c:v", "libx264",              # H.264: the video format every phone and app plays
        "-preset", encoding.preset,     # slower = smaller file at the same quality
        "-crf", str(encoding.crf),      # quality: lower = more detail and a bigger file (18 is near-transparent)
        "-profile:v", encoding.profile,  # "high": the H.264 feature level platforms expect
        "-pix_fmt", "yuv420p",          # the colour format phones expect
    ]
    if audio_filter:
        command += ["-af", audio_filter]
    command += [
        "-c:a", "aac",                  # AAC: the standard sound format inside MP4 files
        "-b:a", "192k",                 # sound quality (192 kilobits per second)
        "-ar", "48000",                 # 48 kHz, the standard for video
        "-t", f"{duration:.3f}",        # stop exactly at the moment's length
        "-movflags", "+faststart",      # put the index at the front so it plays while loading
        output_path,
    ]
    return command


def remove_files(paths):
    """
    Delete temporary files that exist (and ignore ones that don't).

    Args:
        paths (list[str]): File paths.
    """
    for path in paths:
        if path and os.path.exists(path):
            os.remove(path)


def prepare_audio(clip_path, start, duration, work_base, audio_settings, bleep_settings=None, spans=None):
    """
    Cut, clean, bleep and measure the moment's sound (see clip_audio.py).

    Args:
        clip_path (str): The source video.
        start (float): The moment's start, in seconds.
        duration (float): The moment's length.
        work_base (str): Path prefix for temporary files.
        audio_settings (ClipAudioSettings): Cleaning and loudness settings.
        bleep_settings (BleepSettings or None): Tone and volume for bleeps.
        spans (list[dict] or None): Time ranges to bleep (from profanity.bleep_spans).

    Returns:
        dict: {"path": the WAV for the encoder,
               "filter": the final loudness filter, or None for silent sound,
               "loudness": pass-1 measurements or None,
               "beep_levels_db": the level of each beep,
               "temporary": files to delete after the encode,
               "warnings": problems that were worked around}.
    """
    from src.video import clip_audio

    clean_path = work_base + "_audio_clean.wav"
    bleeped_path = work_base + "_audio_bleeped.wav"
    limited_path = work_base + "_audio_limited.wav"
    temporary = [clean_path, bleeped_path] + clip_audio.limited_file_names(limited_path)
    result = {"filter": None, "loudness": None, "beep_levels_db": [], "warnings": [],
              "temporary": temporary}

    try:
        clip_audio.extract_clean_audio(clip_path, start, duration, clean_path, audio_settings)
    except subprocess.CalledProcessError:
        # A build of FFmpeg without one of the cleaning filters still
        # makes a playable draft, with a clear warning in the report.
        message = "Audio cleanup failed with this FFmpeg; used the original sound (still bleeped and levelled)."
        logger.warning(message)
        result["warnings"].append(message)
        clip_audio.extract_clean_audio(clip_path, start, duration, clean_path, audio_settings, clean=False)
    current = clean_path

    if spans:
        result["beep_levels_db"] = clip_audio.bleep_audio_file(current, bleeped_path, spans, bleep_settings)
        current = bleeped_path

    try:
        current, result["filter"], result["loudness"] = clip_audio.prepare_loudness(
            current, limited_path, audio_settings)
    except (subprocess.CalledProcessError, ValueError, KeyError):
        # Silent or very short sound has no loudness to measure; use it as it is.
        result["warnings"].append("Sound could not be measured (silent?); loudness was not adjusted.")
    result["path"] = current
    return result


# ---------------------------------------------------------------------------
# The whole render
# ---------------------------------------------------------------------------

def check_source_resolution(source, quality_settings):
    """
    Flag (or refuse) a source that is too low-resolution for a sharp Short.

    Args:
        source (dict): From probe_video().
        quality_settings (SourceQualitySettings): min_height and reject_below_min.

    Returns:
        str or None: A warning for the quality report, or None when the source is fine.

    Raises:
        ValueError: When the source is too small and the channel refuses those.
    """
    if source["height"] >= quality_settings.min_height:
        return None
    message = (f"Source is only {source['width']}x{source['height']} (below {quality_settings.min_height}p); "
               "the video will look soft. Find a higher-resolution copy if one exists.")
    if quality_settings.reject_below_min:
        raise ValueError(message)
    return message


def layout_for_source(source, facecam, settings, layout_mode=None):
    """
    Plan the picture layout for one source and build its FFmpeg filter.

    Args:
        source (dict): From probe_video() (width, height, fps).
        facecam (FacecamBox or None): The streamer's webcam position.
        settings (ClipChannelSettings): Layout and encoding settings.
        layout_mode (str or None): A per-streamer layout; None uses layout.mode.

    Returns:
        tuple: (plan, video filter text, output frame rate).
    """
    layout = settings.layout
    mode = layout_mode or layout.mode
    plan = plan_layout(source["width"], source["height"], mode, facecam,
                       layout.split_gameplay_share, layout.gameplay_focus_x, layout.foreground_center)
    frame_rate = output_frame_rate(source.get("fps", 0), settings.encoding)
    video_filter = build_video_filter(plan, frame_rate, layout.background_blur, layout.background_darken)
    return plan, video_filter, frame_rate


def is_upscaled(plan):
    """
    Check whether any part of the picture is enlarged beyond its real size.

    Args:
        plan (dict): From plan_layout().

    Returns:
        bool: True when at least one panel is enlarged (and so sharpened).
    """
    if plan["mode"] == "fit-blur":
        return plan["foreground"]["enlarged"]
    for panel in plan["panels"]:
        if panel["enlarged"]:
            return True
    return False


def render_clip_video(clip_path, start, end, words, hook_title, credit_text,
                      facecam, settings, output_path, layout_mode=None, bleep_spans=None):
    """
    Make the finished vertical video for one clip.

    Args:
        clip_path (str): The source video.
        start (float): The chosen moment's start in the clip, in seconds.
        end (float): The chosen moment's end.
        words (list[dict]): The WHOLE clip's timed words (from Whisper),
                            already masked where profanity is bleeped.
        hook_title (str): Shown during the first seconds.
        credit_text (str): For example "twitch.tv/streamer".
        facecam (FacecamBox or None): The streamer's webcam position.
        settings (ClipChannelSettings): Layout, caption, title, credit, audio settings.
        output_path (str): Where to save the MP4.
        layout_mode (str or None): A per-streamer layout; None uses layout.mode.
        bleep_spans (list[dict] or None): Time ranges (from the moment's start) to bleep.

    Returns:
        dict: What was done, for the quality report: {"path", "frame_rate",
              "layout", "source", "upscaled", "loudness", "beep_levels_db", "warnings"}.

    Raises:
        RuntimeError: If FFmpeg fails (the reason is in <output>_ffmpeg.log).
        ValueError: If the source is below the channel's minimum resolution
                    and the channel refuses such sources.
    """
    source = probe_video(clip_path)
    warnings = []
    resolution_warning = check_source_resolution(source, settings.source)
    if resolution_warning:
        warnings.append(resolution_warning)
    duration = round(end - start, 3)
    plan, video_filter, frame_rate = layout_for_source(source, facecam, settings, layout_mode)
    if plan.get("note"):
        warnings.append(plan["note"])

    folder = os.path.dirname(output_path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    work_base = os.path.splitext(output_path)[0]
    audio = {"path": None, "filter": None, "loudness": None, "beep_levels_db": [], "warnings": [], "temporary": []}
    if source["has_audio"]:
        audio = prepare_audio(clip_path, start, duration, work_base, settings.audio,
                              settings.bleep, bleep_spans or [])
    warnings += audio["warnings"]

    # Layers that never change are drawn once.
    positions = text_positions(plan)
    caption_y = positions["caption_y"]
    if settings.captions.center_y is not None:
        caption_y = settings.captions.center_y
    hook_layer = make_layer(draw_hook_title, hook_title, positions["hook_y"], settings.hook_title)
    credit_layer = make_layer(draw_credit, credit_text, settings.credit)
    credit_seconds = settings.credit.seconds
    if credit_seconds <= 0:
        credit_seconds = duration   # 0 means "show it for the whole video"
    groups = group_caption_words(words_in_moment(words, start, end), settings.captions.words_per_caption)

    log_path = work_base + "_ffmpeg.log"
    log_file = open(log_path, "w", encoding="utf-8")
    decoder = subprocess.Popen(decoder_command(clip_path, start, duration, video_filter),
                               stdout=subprocess.PIPE, stderr=log_file)
    encoder = subprocess.Popen(encoder_command(audio["path"], duration, output_path, frame_rate,
                                               settings.encoding, audio["filter"]),
                               stdin=subprocess.PIPE, stdout=log_file, stderr=log_file)

    frame_bytes = OUTPUT_WIDTH * OUTPUT_HEIGHT * 3
    frame_number = 0
    # The caption layer only changes when the highlighted word changes,
    # so the last one drawn is kept and reused until then.
    last_caption_key = None
    caption_layer = None
    try:
        while True:
            raw = decoder.stdout.read(frame_bytes)
            if len(raw) < frame_bytes:
                break
            now = frame_number / frame_rate
            frame = Image.frombytes("RGB", (OUTPUT_WIDTH, OUTPUT_HEIGHT), raw)

            group_number, word_number = caption_at(groups, now)
            caption_key = (group_number, word_number)
            if caption_key != last_caption_key:
                caption_layer = None
                if group_number is not None:
                    caption_layer = make_layer(draw_caption, groups[group_number], word_number,
                                               settings.captions, caption_y)
                last_caption_key = caption_key

            paste_layer(frame, caption_layer)
            if now < credit_seconds:
                paste_layer(frame, credit_layer)
            if now < settings.hook_title.seconds:
                paste_layer(frame, hook_layer)

            encoder.stdin.write(frame.tobytes())
            frame_number += 1
        encoder.stdin.close()
    except BrokenPipeError:
        pass  # the encoder stopped early; its log explains why (checked below)
    except BaseException:
        # Time limits and Ctrl-C must stop both FFmpeg children too.
        decoder.terminate()
        encoder.terminate()
        raise
    finally:
        if not encoder.stdin.closed:
            try:
                encoder.stdin.close()
            except (BrokenPipeError, OSError):
                pass
        decoder.stdout.close()
        for process in (decoder, encoder):
            try:
                process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        log_file.close()
        remove_files(audio["temporary"])

    if frame_number == 0 or encoder.returncode != 0 or decoder.returncode != 0:
        raise RuntimeError(f"FFmpeg could not render the clip. See {log_path}")
    os.remove(log_path)
    return {"path": output_path, "frame_rate": frame_rate, "layout": plan["mode"],
            "source": f"{source['width']}x{source['height']} @ {source['fps']:.2f} fps",
            "upscaled": is_upscaled(plan), "loudness": audio["loudness"],
            "beep_levels_db": audio["beep_levels_db"], "warnings": warnings}

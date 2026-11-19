# =============================================================
# src/video/clip_render.py — turn a stream clip into a vertical
#                            1080x1920 MP4 (step L.5)
#
# The video is ONLY the real clip: no animations, no stock footage,
# no channel logo. On top of it we draw three things:
#   1. the hook title, during the first few seconds
#   2. word-by-word captions from the Whisper transcript
#      (the word being spoken is highlighted)
#   3. optional on-video credit; post captions retain attribution independently
#
# How it works (two FFmpeg programs with Python in the middle):
#   FFmpeg #1 ("decoder") cuts the chosen moment, crops and stacks
#     the webcam and gameplay panels (see clip_layout.py) and sends
#     plain RGB pictures to Python through a pipe.
#   Python draws the text on each picture with Pillow.
#   FFmpeg #2 ("encoder") receives the pictures, adds the clip's own
#     sound (loudness-levelled for phones) and writes the MP4.
#
# Why draw text in Python instead of FFmpeg's subtitle/text filters:
# the FFmpeg installed on this Mac was built without them, and the
# animated news videos already draw their text with Pillow, so both
# video types share one text style and one set of fonts.
# =============================================================

import json
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
from src.video.clip_layout import OUTPUT_HEIGHT, OUTPUT_WIDTH, build_video_filter, plan_layout
from src.video.sound_design import normalize_loudness

FRAMES_PER_SECOND = 30

# Loudness targets used by TikTok/Reels/Shorts-style mixes:
# -14 LUFS overall, peaks no higher than -1.5 dB.
LOUDNESS_SETTINGS = {"target_lufs": -14, "true_peak": -1.5}

# A caption group ends at a pause longer than this (seconds), so a
# new sentence after a pause starts on a fresh caption.
CAPTION_PAUSE_SECONDS = 0.7
# Keep a finished caption on screen this much longer, so short words
# don't flash by.
CAPTION_LINGER_SECONDS = 0.4

# Where the credit sits: centred, just above TikTok's bottom text area.
CREDIT_CENTER_Y = 1440
# Where the hook title sits when there is no split (full layout).
FULL_LAYOUT_HOOK_Y = 520

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
    Ask FFprobe for a video's size, length and whether it has sound.

    Args:
        path (str): The video file.

    Returns:
        dict: {"width": 1920, "height": 1080, "duration": 31.5, "has_audio": True}

    Raises:
        ValueError: If the file has no video picture.
    """
    result = subprocess.run([
        "ffprobe", "-v", "error",
        "-show_entries", "stream=codec_type,width,height:format=duration",
        "-of", "json", path,
    ], capture_output=True, text=True, check=True)
    data = json.loads(result.stdout)

    width = 0
    height = 0
    has_audio = False
    for stream in data.get("streams", []):
        if stream.get("codec_type") == "video" and not width:
            width = int(stream.get("width", 0))
            height = int(stream.get("height", 0))
        if stream.get("codec_type") == "audio":
            has_audio = True
    if not width or not height:
        raise ValueError(f"{path} has no video picture.")

    duration = float(data.get("format", {}).get("duration", 0) or 0)
    return {"width": width, "height": height, "duration": duration, "has_audio": has_audio}


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
        kept.append({
            "word": word["word"],
            "start": round(max(word["start"], start) - start, 3),
            "end": round(min(word["end"], end) - start, 3),
        })
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


def draw_caption(layer, group, active_word, settings):
    """
    Draw one caption line, with the spoken word in the highlight colour.

    Args:
        layer (PIL.Image): A transparent picture to draw on.
        group (dict): The caption group.
        active_word (int): Which word to highlight.
        settings (CaptionSettings): Size, colours and height.
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
        draw.text((x, settings.center_y), text, font=font, fill=color, anchor="lm",
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
    Draw the streamer credit on a dark pill, e.g. "🎥 twitch.tv/streamer".

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
    left = OUTPUT_WIDTH / 2 - pill_width / 2
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


def encoder_command(audio_path, duration, output_path, encoding=None):
    """
    Build the FFmpeg command that turns Python's frames + the sound into an MP4.

    Args:
        audio_path (str or None): The levelled sound, or None for silence.
        duration (float): The video's length in seconds.
        output_path (str): Where to write the MP4.
        encoding: Optional checked frame rate, quality and speed; None keeps the older export defaults.

    Returns:
        list[str]: The command.
    """
    frame_rate = FRAMES_PER_SECOND
    crf = 20
    preset = "fast"
    if encoding is not None:
        frame_rate = encoding.frame_rate
        crf = encoding.crf
        preset = encoding.preset
    command = [
        "ffmpeg", "-nostdin", "-y",
        "-f", "rawvideo", "-pix_fmt", "rgb24",       # frames arrive as raw RGB...
        "-s", f"{OUTPUT_WIDTH}x{OUTPUT_HEIGHT}",     # ...1080x1920 each...
        "-framerate", str(frame_rate),              # match the decoder and caption clock
        "-i", "-",                                   # ...through the pipe
    ]
    if audio_path:
        command += ["-i", audio_path]
    else:
        # A clip without sound gets silence, because TikTok expects an audio track.
        command += ["-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo"]
    command += [
        "-map", "0:v", "-map", "1:a",
        "-c:v", "libx264",       # H.264: the video format every phone and app plays
        "-preset", preset,       # trade encoding time against compression efficiency
        "-crf", str(crf),        # lower values retain more source detail and use more disk
        "-pix_fmt", "yuv420p",   # the colour format phones expect
        "-c:a", "aac",           # AAC: the standard sound format inside MP4 files
        "-b:a", "192k",          # sound quality (192 kilobits per second)
        "-t", f"{duration:.3f}",  # stop exactly at the moment's length
        "-movflags", "+faststart",  # put the index at the front so it plays while loading
        output_path,
    ]
    return command


def prepare_audio(clip_path, start, duration, work_base):
    """
    Cut the moment's sound and level its loudness for phones.

    Args:
        clip_path (str): The clip.
        start (float): The moment's start, in seconds.
        duration (float): The moment's length.
        work_base (str): Path prefix for temporary files.

    Returns:
        str: The levelled WAV file (or the unlevelled one if levelling fails).
    """
    raw_path = work_base + "_audio_raw.wav"
    levelled_path = work_base + "_audio.wav"
    subprocess.run([
        "ffmpeg", "-nostdin", "-v", "error", "-y",
        "-ss", f"{start:.3f}", "-t", f"{duration:.3f}", "-i", clip_path,
        "-vn", "-ac", "2", "-ar", "44100",   # sound only, stereo, 44.1 kHz
        raw_path,
    ], check=True)
    try:
        normalize_loudness(raw_path, levelled_path, LOUDNESS_SETTINGS)
    except (subprocess.CalledProcessError, ValueError, KeyError):
        # Very short or silent sound can't be measured; use it as it is.
        return raw_path
    os.remove(raw_path)
    return levelled_path


# ---------------------------------------------------------------------------
# The whole render
# ---------------------------------------------------------------------------

def layout_for_source(source, facecam, settings):
    """
    Choose a facecam-safe picture layout for every template.

    Args:
        source: Probed source width and height in pixels.
        facecam: A measured camera box, or None when its position is unknown.
        settings: Checked channel settings, including panel height and gameplay focus.

    Returns:
        The panel plan and FFmpeg filter. Known cameras get a top panel; unknown
        positions keep the entire source visible so an embedded camera is not lost.
    """
    plan = plan_layout(source["width"], source["height"], facecam,
                       settings.layout.facecam_panel_height, settings.layout.gameplay_focus_x)
    if facecam is not None:
        return plan, build_video_filter(plan, settings.encoding.frame_rate)
    # This fallback preserves every corner until a creator's camera box is set.
    video_filter = (
        "[0:v]split=2[bg][fg];"
        "[bg]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=20:2[back];"
        "[fg]scale=1080:1920:force_original_aspect_ratio=decrease:flags=lanczos[front];"
        "[back][front]overlay=(W-w)/2:(H-h)/2,setsar=1,"
        f"fps={settings.encoding.frame_rate},format=rgb24[out]"
    )
    return plan, video_filter


def render_clip_video(clip_path, start, end, words, hook_title, credit_text,
                      facecam, settings, output_path):
    """
    Make the finished vertical video for one clip.

    Args:
        clip_path (str): The downloaded clip.
        start (float): The chosen moment's start in the clip, in seconds.
        end (float): The chosen moment's end.
        words (list[dict]): The WHOLE clip's timed words (from Whisper).
        hook_title (str): Shown during the first seconds.
        credit_text (str): For example "twitch.tv/streamer".
        facecam (FacecamBox or None): The streamer's webcam position.
        settings (ClipChannelSettings): Layout, caption, title and credit settings.
        output_path (str): Where to save the MP4.

    Returns:
        str: output_path.

    Raises:
        RuntimeError: If FFmpeg fails (the reason is in <output>_ffmpeg.log).
    """
    source = probe_video(clip_path)
    duration = round(end - start, 3)
    plan, video_filter = layout_for_source(source, facecam, settings)

    folder = os.path.dirname(output_path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    work_base = os.path.splitext(output_path)[0]
    audio_path = None
    if source["has_audio"]:
        audio_path = prepare_audio(clip_path, start, duration, work_base)

    # Layers that never change are drawn once.
    hook_y = FULL_LAYOUT_HOOK_Y
    if plan["mode"] == "split":
        # Keep the title below the facecam instead of covering its lower edge.
        hook_y = plan["panels"][0]["height"] + 150
    hook_layer = make_layer(draw_hook_title, hook_title, hook_y, settings.hook_title)
    credit_layer = make_layer(draw_credit, credit_text, settings.credit)
    groups = group_caption_words(words_in_moment(words, start, end), settings.captions.words_per_caption)

    log_path = work_base + "_ffmpeg.log"
    log_file = open(log_path, "w", encoding="utf-8")
    decoder = subprocess.Popen(decoder_command(clip_path, start, duration, video_filter),
                               stdout=subprocess.PIPE, stderr=log_file)
    encoder = subprocess.Popen(encoder_command(audio_path, duration, output_path, settings.encoding),
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
            now = frame_number / settings.encoding.frame_rate
            frame = Image.frombytes("RGB", (OUTPUT_WIDTH, OUTPUT_HEIGHT), raw)

            group_number, word_number = caption_at(groups, now)
            caption_key = (group_number, word_number)
            if caption_key != last_caption_key:
                caption_layer = None
                if group_number is not None:
                    caption_layer = make_layer(draw_caption, groups[group_number], word_number,
                                               settings.captions)
                last_caption_key = caption_key

            paste_layer(frame, caption_layer)
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
        if audio_path and os.path.exists(audio_path):
            os.remove(audio_path)

    if frame_number == 0 or encoder.returncode != 0 or decoder.returncode != 0:
        raise RuntimeError(f"FFmpeg could not render the clip. See {log_path}")
    os.remove(log_path)
    return output_path

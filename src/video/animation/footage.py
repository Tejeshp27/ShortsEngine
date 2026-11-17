# =============================================================
# src/video/animation/footage.py — the moving video behind everything
#
# Every frame of an animated video starts with real stock video
# footage filling the whole screen, so there is never a blank or
# black background. This file does three jobs:
#
#   1. find_footage_for_scenes() — picks a stock clip for every
#      scene from the channel's hand-checked list of footage topics,
#      with backups so that every scene always has one.
#   2. FootageReader — plays a clip one frame at a time, already
#      cropped to 9:16 and slightly darkened so text is readable.
#   3. draw_fallback_background() — a moving colour gradient used
#      only if no clip could be downloaded at all (for example,
#      when there is no internet or no stock video API key).
#
# How it fits in:
#   renderer.py → find_footage_for_scenes() → stock_video.py
#   renderer.py → FootageReader.read_frame() once per video frame
# =============================================================

import math
import subprocess

from PIL import Image

from src.video.animation.drawing import VIDEO_HEIGHT, VIDEO_WIDTH, paste_image


# A clip whose average brightness is below this (0 = black, 255 = white)
# is rejected, because after darkening it would look like a black screen.
MINIMUM_CLIP_BRIGHTNESS = 45

# The first few topics in a channel's footage list are its general-purpose
# ones. A scene whose own topic is not usable takes one of these, in turn.
GENERAL_TOPIC_COUNT = 3

# Searches already tried this run. Key: query. Value: clip path or None.
# This stops us asking the stock video site the same question twice.
_clip_search_results = {}

# Pictures for the fallback background, built once and reused.
_fallback_pictures = {}


# Footage is decoded this much bigger than the screen, so the visible
# window can slowly pan across it (the picture is never still).
FOOTAGE_OVERSIZE = 1.1


class FootageReader:
    """
    Plays one stock video clip, handing out one frame at a time.

    FFmpeg does the heavy lifting in a separate process: it decodes the
    clip, scales it to fill a frame slightly bigger than 9:16, darkens it,
    and loops it forever. This class reads those frames one by one and
    cuts out a slowly moving 1080×1920 window (a gentle pan).

    Example:
        reader = FootageReader("cache/stock_video/abc.mp4", fps=30, brightness=0.6,
                               start_seconds=4.0)
        frame = reader.read_frame(0.5, direction=1)   # halfway through the shot
        reader.close()
    """

    def __init__(self, clip_path, fps, brightness, start_seconds=0.0):
        """
        Start FFmpeg playing the clip.

        Args:
            clip_path (str): Path to the downloaded .mp4 clip.
            fps (int): Frames per second of the final video.
            brightness (float): 1.0 keeps the clip as it is; 0.6 darkens it
                                to 60% so white text stands out on top.
            start_seconds (float): Where in the clip to start. Starting the
                                   same clip at different points makes each
                                   reuse look like a new shot.
        """
        self.width = int(VIDEO_WIDTH * FOOTAGE_OVERSIZE)
        self.height = int(VIDEO_HEIGHT * FOOTAGE_OVERSIZE)
        # Each frame is width × height pixels, and each pixel is 3 bytes
        # (one each for red, green and blue).
        self.bytes_per_frame = self.width * self.height * 3

        # The filter chain, in order:
        #   scale + crop  → fill the (slightly oversized) tall frame
        #   fps           → match the final video's frame rate
        #   colorchannelmixer → multiply red, green and blue to darken
        video_filter = (
            f"scale={self.width}:{self.height}:force_original_aspect_ratio=increase,"
            f"crop={self.width}:{self.height},"
            f"fps={fps},"
            f"colorchannelmixer=rr={brightness}:gg={brightness}:bb={brightness}"
        )
        command = [
            "ffmpeg", "-loglevel", "error",
            "-stream_loop", "-1",        # loop the clip forever
            "-ss", f"{start_seconds:.2f}",
            "-i", clip_path,
            "-vf", video_filter,
            "-an",                       # ignore the clip's own sound
            "-f", "rawvideo", "-pix_fmt", "rgb24",
            "-",                         # "-" means: write to this program
        ]
        self.process = subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
        )

    def read_frame(self, shot_progress=0.0, direction=1):
        """
        Get the next frame of the clip, as a slowly panning 1080×1920 window.

        Args:
            shot_progress (float): How far through the shot we are, 0.0 to 1.0.
            direction (int): 1 pans left-to-right, -1 right-to-left.

        Returns:
            PIL.Image or None: A 1080×1920 image, or None if FFmpeg could
                               not read the clip (the renderer then uses
                               the fallback background for that frame).
        """
        data = self.process.stdout.read(self.bytes_per_frame)
        if len(data) < self.bytes_per_frame:
            return None
        picture = Image.frombytes("RGB", (self.width, self.height), data)
        amount = shot_progress if direction > 0 else 1.0 - shot_progress
        left = int((self.width - VIDEO_WIDTH) * amount)
        top = (self.height - VIDEO_HEIGHT) // 2
        return picture.crop((left, top, left + VIDEO_WIDTH, top + VIDEO_HEIGHT))

    def close(self):
        """Stop FFmpeg and free the clip."""
        self.process.kill()
        self.process.stdout.close()
        self.process.wait()


def measure_clip_brightness(clip_path):
    """
    Measure how bright a clip is, by looking at one frame from it.

    Args:
        clip_path (str): Path to the .mp4 clip.

    Returns:
        float: Average brightness from 0 (black) to 255 (white).
               Returns 0 if the clip could not be read.
    """
    # Ask FFmpeg for a single tiny grey frame from one second in.
    # 32×32 pixels is plenty to judge overall brightness.
    command = [
        "ffmpeg", "-loglevel", "error",
        "-ss", "1", "-i", clip_path,
        "-frames:v", "1",
        "-vf", "scale=32:32",
        "-f", "rawvideo", "-pix_fmt", "gray",
        "-",
    ]
    result = subprocess.run(command, capture_output=True)
    pixels = result.stdout
    if not pixels:
        return 0.0

    total = 0
    for value in pixels:
        total += value
    return total / len(pixels)


def _search_for_clip(query, variant=0):
    """
    Look for one usable stock clip for a search phrase.

    Args:
        query (str): Search words, e.g. "capitol building".
        variant (int): Which search result to use (0 = the top result).

    Returns:
        str or None: Path to a downloaded clip, or None if nothing
                     suitable was found.
    """
    memory_key = (query, variant)
    if memory_key in _clip_search_results:
        return _clip_search_results[memory_key]

    # Imported here so tests can run without touching the network module.
    from src.video.stock_video import get_video_clip

    clip_path, _is_portrait = get_video_clip(query, variant)

    if clip_path is not None:
        brightness = measure_clip_brightness(clip_path)
        if brightness < MINIMUM_CLIP_BRIGHTNESS:
            print(f"  Footage '{query}' (result {variant + 1}) is too dark — skipping it.")
            clip_path = None

    _clip_search_results[memory_key] = clip_path
    return clip_path


def find_clip(query):
    """
    Find a stock clip for a search phrase, trying a simpler phrase if needed.

    Stock video sites match words literally, so "empty senate chamber
    desks" may find nothing while "empty senate" finds plenty.

    Args:
        query (str): Search words from the storyboard.

    Returns:
        str or None: Path to a downloaded clip, or None.
    """
    query = query.strip()
    if not query:
        return None

    clip_path = _search_for_clip(query)
    if clip_path is not None:
        return clip_path

    words = query.split()
    if len(words) > 2:
        shorter_query = " ".join(words[:2])
        return _search_for_clip(shorter_query)
    return None


def _next_clip_for_topic(topic, footage_topics, times_used):
    """
    Get the next unused clip for one of the channel's footage topics.

    Each topic has a number of search results that were checked by hand
    (set in the channel config). We walk through them in turn, so a topic
    used three times in one video shows three different clips.

    Args:
        topic (str): The topic, e.g. "american flag".
        footage_topics (dict): Topic → how many of its results are usable.
        times_used (dict): Topic → how many times this video has used it.
                           Updated by this function.

    Returns:
        str or None: Path to a downloaded clip, or None if none is usable.
    """
    usable_results = max(1, int(footage_topics[topic]))
    already_used = times_used.get(topic, 0)

    for attempt in range(usable_results):
        variant = (already_used + attempt) % usable_results
        clip_path = _search_for_clip(topic, variant)
        if clip_path is not None:
            times_used[topic] = already_used + attempt + 1
            return clip_path
    return None


def find_footage_for_scenes(scenes, footage_topics):
    """
    Give every scene a stock video clip to play in the background.

    If the channel config lists footage topics (searches whose results
    were checked by hand), every clip comes from that list: the topic the
    storyboard chose if it is on the list, otherwise one of the first
    three topics (the channel's general-purpose ones) in turn. This keeps
    unsuitable clips out of the video.

    If the channel has no list, the storyboard's own search words are used.

    Either way, a scene that still has no clip reuses the previous scene's
    clip, and only if that fails too does it get None — the renderer then
    draws the moving gradient, so the screen is still never blank.

    Args:
        scenes (list[dict]): The storyboard scenes. Each one gains a
                             "clip_path" key (a file path or None).
        footage_topics (dict): Topic → number of usable search results,
                               e.g. {"american flag": 4}. May be empty.

    Returns:
        int: How many scenes ended up with a real clip.
    """
    topic_names = list(footage_topics.keys())
    times_used = {}
    scenes_with_footage = 0
    previous_clip_path = None

    for scene_number, scene in enumerate(scenes):
        clip_path = None

        if topic_names:
            # Start with the storyboard's topic if it is an approved one,
            # then work through the other topics until one gives a clip.
            candidates = []
            wanted_topic = scene.get("footage", "").lower()
            if wanted_topic in footage_topics:
                candidates.append(wanted_topic)
            general_topics = topic_names[:GENERAL_TOPIC_COUNT]
            for offset in range(len(general_topics)):
                topic = general_topics[(scene_number + offset) % len(general_topics)]
                if topic not in candidates:
                    candidates.append(topic)
            for topic in topic_names[GENERAL_TOPIC_COUNT:]:
                if topic not in candidates:
                    candidates.append(topic)

            for topic in candidates:
                clip_path = _next_clip_for_topic(topic, footage_topics, times_used)
                if clip_path is not None:
                    break
        else:
            clip_path = find_clip(scene.get("footage", ""))

        if clip_path is None:
            clip_path = previous_clip_path

        scene["clip_path"] = clip_path
        scene["clip_credit"] = None
        if clip_path is not None:
            # Imported here so tests can run without the network module.
            from src.video.stock_video import get_clip_credit
            scene["clip_credit"] = get_clip_credit(clip_path)
            scenes_with_footage += 1
            previous_clip_path = clip_path

    return scenes_with_footage


def _make_gradient(top_color, bottom_color):
    """
    Build a full-frame picture that blends from one colour to another.

    Args:
        top_color (tuple): (red, green, blue) at the top of the frame.
        bottom_color (tuple): (red, green, blue) at the bottom.

    Returns:
        PIL.Image: A 1080×1920 gradient image.
    """
    # Build a strip one pixel wide (one colour per row), then stretch it
    # sideways. This is much faster than colouring two million pixels.
    strip = Image.new("RGB", (1, VIDEO_HEIGHT))
    for row in range(VIDEO_HEIGHT):
        amount = row / (VIDEO_HEIGHT - 1)
        red = int(top_color[0] + (bottom_color[0] - top_color[0]) * amount)
        green = int(top_color[1] + (bottom_color[1] - top_color[1]) * amount)
        blue = int(top_color[2] + (bottom_color[2] - top_color[2]) * amount)
        strip.putpixel((0, row), (red, green, blue))
    return strip.resize((VIDEO_WIDTH, VIDEO_HEIGHT))


def _make_glow(color, size=1100):
    """
    Build a soft glowing blob: solid in the middle, fading to nothing.

    Args:
        color (tuple): (red, green, blue) of the glow.
        size (int): Width and height of the blob in pixels.

    Returns:
        PIL.Image: An RGBA image of the glow.
    """
    # Pillow's radial_gradient is dark in the middle and light at the
    # edges. This table flips it and makes it fade out fully before the
    # edge, so the blob has no visible square outline.
    fade_table = []
    for value in range(256):
        strength = max(0, 150 - value * 1.3)   # 150 = see-through even at centre
        fade_table.append(int(strength))

    alpha = Image.radial_gradient("L").point(fade_table).resize((size, size))
    glow = Image.new("RGBA", (size, size), (color[0], color[1], color[2], 0))
    glow.putalpha(alpha)
    return glow


def draw_fallback_background(now, top_color, bottom_color, glow_color):
    """
    Draw a moving colour background for frames that have no footage.

    Two soft glowing blobs drift slowly across a gradient, so even this
    emergency background is never a flat, still colour.

    Args:
        now (float): The current time in the video, in seconds.
        top_color (tuple): (red, green, blue) for the top of the frame.
        bottom_color (tuple): (red, green, blue) for the bottom.
        glow_color (tuple): (red, green, blue) for the drifting blobs.

    Returns:
        PIL.Image: A new 1080×1920 frame to draw the scene on.
    """
    gradient_key = ("gradient", top_color, bottom_color)
    if gradient_key not in _fallback_pictures:
        _fallback_pictures[gradient_key] = _make_gradient(top_color, bottom_color)

    glow_key = ("glow", glow_color)
    if glow_key not in _fallback_pictures:
        _fallback_pictures[glow_key] = _make_glow(glow_color)

    frame = _fallback_pictures[gradient_key].copy()
    glow = _fallback_pictures[glow_key]

    # sin and cos trace slow loops, so each blob wanders around smoothly.
    first_x = VIDEO_WIDTH * 0.5 + 380 * math.sin(now * 0.45)
    first_y = VIDEO_HEIGHT * 0.3 + 260 * math.cos(now * 0.35)
    second_x = VIDEO_WIDTH * 0.5 + 380 * math.sin(now * 0.3 + 2.0)
    second_y = VIDEO_HEIGHT * 0.72 + 260 * math.cos(now * 0.5 + 1.0)

    paste_image(frame, glow, first_x, first_y)
    paste_image(frame, glow, second_x, second_y)
    return frame

# =============================================================
# src/video/animation/shots.py — when the background picture changes
#
# Professional short-form editors change the picture every 1-3
# seconds. This file turns the storyboard into a list of "shots":
# pieces of the video, each with one background (a footage clip, a
# photo, or the moving gradient), none longer than the channel's
# max_shot_seconds.
#
#   - A scene shorter than the limit is one shot.
#   - A longer scene is cut into equal shots. Each new shot starts the
#     clip at a different point (or swaps the photo for footage), so
#     the cut is visible.
#   - The same clip used again later starts somewhere new, so it looks
#     like a new shot rather than a repeat.
#   - Shots alternate their pan direction, so movement keeps changing.
#
# The renderer draws from this list, and the quality gate checks it,
# so both always agree on what is on screen.
#
# How it fits in:
#   renderer.py → plan_shots() (here) → ShotPlayer.frame_at()
# =============================================================

import math
import subprocess

from src.video.animation.footage import FootageReader
from src.video.animation.photos import PhotoBackground

# Used when the channel YAML does not set animation.max_shot_seconds.
DEFAULT_MAX_SHOT_SECONDS = 2.8

# How far apart (in seconds) the starting points of a reused clip are.
CLIP_START_STEP = 3.3

# Clip lengths already measured this run (path → seconds).
_clip_lengths = {}


def clip_length(clip_path):
    """
    Measure how long a footage clip is, in seconds (remembered per run).

    Args:
        clip_path (str): The .mp4 clip.

    Returns:
        float: Its length, or 10.0 if it could not be measured.
    """
    if clip_path in _clip_lengths:
        return _clip_lengths[clip_path]
    length = 10.0
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", clip_path],
            capture_output=True, text=True, timeout=10, check=True,
        )
        length = float(result.stdout.strip())
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    _clip_lengths[clip_path] = length
    return length


def background_for_piece(scene, piece_number):
    """
    Decide what fills the screen behind one piece of a scene.

    A photo is shown for the first piece of its scene; later pieces of a
    long scene cut to footage. Person scenes show their photo inside the
    name card instead, so their background is footage.

    Args:
        scene (dict): The storyboard scene (with clip_path/photo_path).
        piece_number (int): 0 for the first shot of the scene, 1 for the next...

    Returns:
        tuple: (kind, path, credit) — kind is "photo", "footage" or "gradient".
    """
    photo_path = scene.get("photo_path")
    if photo_path and scene.get("template") != "person" and piece_number == 0:
        return "photo", photo_path, scene.get("photo_credit")
    if scene.get("clip_path"):
        return "footage", scene["clip_path"], scene.get("clip_credit")
    if photo_path and scene.get("template") != "person":
        return "photo", photo_path, scene.get("photo_credit")
    return "gradient", None, None


def plan_shots(scenes, total_duration, max_shot_seconds=DEFAULT_MAX_SHOT_SECONDS):
    """
    Split the video into background shots no longer than max_shot_seconds.

    Args:
        scenes (list[dict]): Storyboard scenes with "start"/"end" times and
                             backgrounds ("clip_path", "photo_path", credits).
        total_duration (float): The whole video's length in seconds.
        max_shot_seconds (float): The longest any one shot may last.

    Returns:
        list[dict]: Shots in time order. Each has "start", "end",
                    "scene_index", "kind", "path", "credit", "offset"
                    (where to start a clip) and "direction" (pan way).

    Example:
        shots = plan_shots(scenes, 75.0, 2.8)
        shots[0]["kind"]   # → "footage"
    """
    shots = []
    times_clip_used = {}

    for scene_index, scene in enumerate(scenes):
        start = scene["start"]
        end = scene["end"]
        if scene_index == len(scenes) - 1:
            end = max(end, total_duration)
        if end <= start:
            continue

        # Round up: a 6-second scene with a 2.8-second limit needs 3 shots.
        piece_count = max(1, math.ceil((end - start) / max_shot_seconds - 0.001))
        piece_length = (end - start) / piece_count

        for piece_number in range(piece_count):
            kind, path, credit = background_for_piece(scene, piece_number)
            offset = 0.0
            if kind == "footage":
                uses = times_clip_used.get(path, 0)
                times_clip_used[path] = uses + 1
                offset = (uses * CLIP_START_STEP) % max(clip_length(path) - 1.0, 1.0)
            shots.append({
                "start": start + piece_number * piece_length,
                "end": start + (piece_number + 1) * piece_length,
                "scene_index": scene_index,
                "kind": kind,
                "path": path,
                "credit": credit,
                "offset": round(offset, 2),
                "direction": 1 if len(shots) % 2 == 0 else -1,
            })
    return shots


class ShotPlayer:
    """
    Hands out the background picture for any moment, following the shot list.

    It keeps only the current shot's clip or photo open, and opens the next
    one when a shot ends.

    Example:
        player = ShotPlayer(shots, fps=30, brightness=0.6)
        background = player.frame_at(12.4)   # None means "draw the gradient"
        player.close()
    """

    def __init__(self, shots, fps, brightness):
        """
        Args:
            shots (list[dict]): Output of plan_shots().
            fps (int): Frames per second of the video.
            brightness (float): How much to darken footage and photos.
        """
        self.shots = shots
        self.fps = fps
        self.brightness = brightness
        self.shot_number = -1
        self.reader = None
        self.photo = None

    def open_shot(self, shot_number):
        """
        Close the previous shot's clip or photo and open this one's.

        Args:
            shot_number (int): Index into self.shots.
        """
        self.close()
        self.shot_number = shot_number
        shot = self.shots[shot_number]
        if shot["kind"] == "footage":
            self.reader = FootageReader(shot["path"], self.fps, self.brightness,
                                        start_seconds=shot["offset"])
        elif shot["kind"] == "photo":
            self.photo = PhotoBackground(shot["path"], self.brightness, shot["direction"])

    def frame_at(self, now):
        """
        Get the background picture for one moment of the video.

        Must be called with times that only move forward (frame by frame).

        Args:
            now (float): Seconds from the start of the video.

        Returns:
            PIL.Image or None: The 1080×1920 background, or None when the
                               shot is the gradient or a clip failed.
        """
        if not self.shots:
            return None
        wanted = self.shot_number
        if wanted < 0:
            wanted = 0
        while wanted + 1 < len(self.shots) and now >= self.shots[wanted + 1]["start"]:
            wanted += 1
        if wanted != self.shot_number:
            self.open_shot(wanted)

        shot = self.shots[self.shot_number]
        length = max(shot["end"] - shot["start"], 0.1)
        shot_progress = min(1.0, max(0.0, (now - shot["start"]) / length))
        if self.reader is not None:
            return self.reader.read_frame(shot_progress, shot["direction"])
        if self.photo is not None:
            return self.photo.frame_at(now - shot["start"], length)
        return None

    def close(self):
        """Free the current clip or photo."""
        if self.reader is not None:
            self.reader.close()
            self.reader = None
        self.photo = None

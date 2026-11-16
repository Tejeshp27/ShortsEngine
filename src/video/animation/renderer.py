# =============================================================
# src/video/animation/renderer.py — build the animated video
#
# This is the main loop of the animation engine. A video is just
# many pictures shown quickly (30 per second), so to make one we:
#
#   1. Get the storyboard (what to show for each sentence).
#   2. Download a stock video clip for every scene.
#   3. For every frame of the video, in order:
#        a. take the next picture from the scene's footage clip,
#        b. draw the scene's animated graphic on top,
#        c. draw the captions, progress bar and section label,
#        d. hand the finished picture to FFmpeg.
#   4. FFmpeg squeezes all the pictures into an MP4 and adds the
#      voiceover (plus the hook sound and music, if any).
#
# The pictures go to FFmpeg through a "pipe" — a direct in-memory
# connection between two programs — so thousands of frames never
# have to be saved to disk as separate image files.
#
# How it fits in:
#   render.py → render_animated_video() (here) → FFmpeg → MP4
# =============================================================

import math
import os
import subprocess

from src.video.animation.drawing import VIDEO_HEIGHT, VIDEO_WIDTH, hex_to_rgb
from src.video.animation.footage import (
    FootageReader,
    draw_fallback_background,
    find_footage_for_scenes,
)
from src.video.animation.overlays import (
    build_caption_groups,
    draw_captions,
    draw_progress_bar,
    draw_section_chip,
    draw_wipe,
    find_caption_group,
)
from src.video.animation.scenes import draw_scene
from src.video.animation.storyboard import add_scene_times, get_storyboard


# The look of the animation when a channel config does not set its own.
# Every value can be overridden under video.animation in the channel YAML.
DEFAULT_ANIMATION_SETTINGS = {
    "fps": 30,                       # frames (pictures) per second
    "footage_brightness": 0.6,       # 1.0 = footage as-is, lower = darker
    "accent_colors": ["#FFD23F", "#3BE8B0", "#FF6B8B", "#5CC8FF"],
    "text_color": "#FFFFFF",
    "fallback_background": ["#101A3A", "#3A1050"],   # gradient, top → bottom
    "footage_topics": {},            # approved stock video searches (see YAML)
}


def load_animation_settings(channel_config):
    """
    Read the channel's animation settings, filling gaps with defaults.

    Args:
        channel_config (dict or None): The channel config. Settings are
                                       read from its video.animation block.

    Returns:
        dict: Every key in DEFAULT_ANIMATION_SETTINGS, using the channel's
              value where it set one.

    Example:
        settings = load_animation_settings(config)
        settings["fps"]   # → 30
    """
    settings = dict(DEFAULT_ANIMATION_SETTINGS)
    if channel_config:
        channel_settings = channel_config.get("video", {}).get("animation", {})
        for key in settings:
            if key in channel_settings:
                settings[key] = channel_settings[key]
    return settings


def get_scene_colors(scene_number, settings):
    """
    Choose the colours for a scene.

    Each scene takes the next accent colour from the channel's list, so
    the colour changes from scene to scene and the video feels lively.

    Args:
        scene_number (int): The scene's position in the video (0 = first).
        settings (dict): Output of load_animation_settings().

    Returns:
        dict: {"accent", "second", "text"}, each a (red, green, blue) tuple.
    """
    accents = settings["accent_colors"]
    accent = accents[scene_number % len(accents)]
    second = accents[(scene_number + 1) % len(accents)]
    return {
        "accent": hex_to_rgb(accent),
        "second": hex_to_rgb(second),
        "text": hex_to_rgb(settings["text_color"]),
    }


def add_section_details(scenes, channel_name):
    """
    Mark where each section of the script begins, and name it for the screen.

    Adds three keys to every scene:
        "starts_section" — True for the first scene of a new section,
        "section_start"  — the time that scene's section began,
        "section_label"  — the text for the small label at the top.

    Args:
        scenes (list[dict]): Storyboard scenes that already have "start".
        channel_name (str): The channel's display name, shown during the hook.
    """
    section_start = 0.0
    previous_section = None

    for scene in scenes:
        scene["starts_section"] = scene["section"] != previous_section
        if scene["starts_section"]:
            section_start = scene["start"]
        scene["section_start"] = section_start

        # During the hook, show the channel's name instead of "HOOK".
        if scene["section"] == "Hook":
            scene["section_label"] = channel_name.upper()
        else:
            scene["section_label"] = scene["section"].upper()

        previous_section = scene["section"]


def draw_frame(frame, now, scenes, scene_number, caption_groups, settings,
               font_size, total_duration):
    """
    Draw everything that goes on top of the footage for one video frame.

    Args:
        frame (PIL.Image): The frame, already holding the footage picture.
        now (float): The current time in the video, in seconds.
        scenes (list[dict]): All storyboard scenes, with their times.
        scene_number (int): Which scene is playing right now.
        caption_groups (list[dict]): Output of build_caption_groups().
        settings (dict): Output of load_animation_settings().
        font_size (int): Caption font size from the channel config.
        total_duration (float): Length of the whole video in seconds.

    Returns:
        PIL.Image: The same frame, now complete.
    """
    scene = scenes[scene_number]
    colors = get_scene_colors(scene_number, settings)

    # Layer 2: the animated graphic for this sentence.
    draw_scene(frame, scene, now - scene["start"], scene["end"] - scene["start"],
               colors)

    # Layer 3: the things that are always on screen.
    draw_progress_bar(frame, now / max(total_duration, 0.1), colors["accent"])
    draw_section_chip(frame, scene["section_label"], now - scene["section_start"],
                      colors["accent"])
    draw_captions(frame, find_caption_group(caption_groups, now), now, font_size,
                  colors["text"], hex_to_rgb(settings["accent_colors"][0]))

    # A coloured wipe covers the screen at each change of section. Check
    # both the section we just entered and the one we are about to enter.
    if scene["starts_section"] and scene_number > 0:
        draw_wipe(frame, now, scene["start"], colors["accent"])
    if scene_number + 1 < len(scenes):
        next_scene = scenes[scene_number + 1]
        if next_scene["starts_section"]:
            next_colors = get_scene_colors(scene_number + 1, settings)
            draw_wipe(frame, now, next_scene["start"], next_colors["accent"])

    return frame


def render_animated_video(script_path, channel_config, audio_path, word_timings,
                          video_path, llm=None, font_size=90, sfx_path=None,
                          music_path=None, music_volume=0.12, use_footage=True):
    """
    Turn a script and its voiceover into a fully animated MP4.

    Args:
        script_path (str): Path to the approved .md script.
        channel_config (dict): The channel config (look, models, name).
        audio_path (str): Path to the voiceover audio file.
        word_timings (list[dict]): One {"word", "start", "end"} per spoken word.
        video_path (str): Where to save the finished .mp4.
        llm (LLMClient or None): AI client used to plan the storyboard. If
                                 None, a plain storyboard is made for free.
        font_size (int): Caption font size from the channel config.
        sfx_path (str or None): Optional hook sound effect to mix in.
        music_path (str or None): Optional background music to mix in.
        music_volume (float): Music loudness compared with the voice (0–1).
        use_footage (bool): If False, skip stock footage downloads and use
                            the moving gradient background instead.

    Returns:
        str: video_path, once the file has been written.

    Example:
        render_animated_video("output/news/story.md", config, "cache/audio/a.mp3",
                              word_timings, "output/videos/news/story.mp4", llm=llm)
    """
    # Imported here because render.py also imports this file.
    from src.video.render import _build_ffmpeg_command, _get_audio_duration

    settings = load_animation_settings(channel_config)
    fps = settings["fps"]
    duration = _get_audio_duration(audio_path)

    # --- Step 1: The plan ---
    scenes = get_storyboard(script_path, channel_config, llm)
    if not scenes:
        raise RuntimeError(f"No spoken text found in {script_path} — nothing to animate.")
    add_scene_times(scenes, word_timings, duration)
    add_section_details(scenes, channel_config.get("name", ""))

    words_per_caption = channel_config.get("video", {}).get("words_per_caption", 3)
    caption_groups = build_caption_groups(scenes, word_timings, words_per_caption)

    # --- Step 2: Footage for every scene ---
    if use_footage:
        print("  Finding stock footage for each scene...")
        found = find_footage_for_scenes(scenes, settings["footage_topics"])
        print(f"  Footage ready: {found}/{len(scenes)} scenes have a video clip.")
        if found < len(scenes):
            print("  Scenes without a clip will use the moving gradient background.")
    else:
        for scene in scenes:
            scene["clip_path"] = None

    # --- Step 3: Start FFmpeg, ready to receive frames ---
    # "-f rawvideo ... -i -" tells FFmpeg to expect plain, uncompressed
    # pictures of a known size arriving through the pipe.
    video_input_flags = [
        "-f", "rawvideo", "-pix_fmt", "rgb24",
        "-s", f"{VIDEO_WIDTH}x{VIDEO_HEIGHT}",
        "-framerate", str(fps),
        "-i", "-",
    ]
    command = _build_ffmpeg_command(
        concat_path=None,
        audio_path=audio_path,
        video_path=video_path,
        duration=duration,
        sfx_path=sfx_path,
        music_path=music_path,
        music_volume=music_volume,
        video_input_flags=video_input_flags,
    )

    # FFmpeg's messages go to a log file so they cannot clog the pipe.
    log_path = os.path.splitext(video_path)[0] + "_ffmpeg.log"
    log_file = open(log_path, "w", encoding="utf-8")
    encoder = subprocess.Popen(command, stdin=subprocess.PIPE,
                               stdout=log_file, stderr=log_file)

    gradient_top = hex_to_rgb(settings["fallback_background"][0])
    gradient_bottom = hex_to_rgb(settings["fallback_background"][1])

    total_frames = math.ceil(duration * fps)
    scene_number = 0
    reader = None            # plays the current scene's footage clip
    reader_clip_path = None  # which clip that reader is playing
    next_report_percent = 20

    print(f"  Drawing {total_frames} frames ({duration:.1f}s at {fps} fps)...")

    # --- Step 4: Draw and send every frame ---
    try:
        for frame_number in range(total_frames):
            now = frame_number / fps

            # Move to the next scene once its start time has arrived.
            while scene_number + 1 < len(scenes) and now >= scenes[scene_number + 1]["start"]:
                scene_number += 1
            scene = scenes[scene_number]

            # Switch footage when the new scene uses a different clip.
            # (If two scenes share a clip, it simply keeps playing.)
            if scene["clip_path"] != reader_clip_path:
                if reader is not None:
                    reader.close()
                    reader = None
                if scene["clip_path"] is not None:
                    reader = FootageReader(scene["clip_path"], fps,
                                           settings["footage_brightness"])
                reader_clip_path = scene["clip_path"]

            # Layer 1: the footage. If there is none, or the clip cannot
            # be read, draw the moving gradient so the screen is never blank.
            frame = None
            if reader is not None:
                frame = reader.read_frame()
            if frame is None:
                colors = get_scene_colors(scene_number, settings)
                frame = draw_fallback_background(now, gradient_top, gradient_bottom,
                                                 colors["accent"])

            draw_frame(frame, now, scenes, scene_number, caption_groups, settings,
                       font_size, duration)
            encoder.stdin.write(frame.tobytes())

            if (frame_number + 1) * 100 >= next_report_percent * total_frames:
                print(f"    {next_report_percent}% drawn")
                next_report_percent += 20

        encoder.stdin.close()
        encoder.wait()

    except BrokenPipeError:
        # FFmpeg stopped early. The reason is in its log, reported below.
        encoder.wait()

    finally:
        # A browser job can time out or fail while drawing. Close the pipe and
        # stop FFmpeg too, so a failed job does not leave a hidden encoder alive.
        if encoder.poll() is None:
            encoder.terminate()
            try:
                encoder.wait(timeout=5)
            except subprocess.TimeoutExpired:
                encoder.kill()
                encoder.wait()
        if reader is not None:
            reader.close()
        log_file.close()

    if encoder.returncode != 0:
        with open(log_path, "r", encoding="utf-8") as f:
            log_text = f.read()
        raise RuntimeError(
            f"FFmpeg failed (exit code {encoder.returncode}).\n"
            f"Log:\n{log_text[-2000:]}"
        )

    # The log is only useful when something went wrong.
    os.remove(log_path)
    return video_path

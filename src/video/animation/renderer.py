# =============================================================
# src/video/animation/renderer.py — build the animated video
#
# This is the main loop of the animation engine. A video is just
# many pictures shown quickly (30 per second), so to make one we:
#
#   1. Get the storyboard (what to show for each sentence).
#   2. Find a background for every scene: a real, freely licensed photo
#      of what the sentence names, or a stock footage clip.
#   3. Plan the "shots" so the picture changes every 1-3 seconds.
#   4. Build the soundtrack: voice, quiet ducking music, whooshes and
#      pops, all brought to -14 LUFS (social-media loudness).
#   5. Run the quality gate. If the plan has a serious problem (a missing
#      licence, captions under the app's buttons), stop here.
#   6. For every frame of the video, in order:
#        a. take the background picture for this moment,
#        b. draw the scene's animated graphic on top,
#        c. draw the captions, brand bug, progress bar, section label,
#           and at the end the end card,
#        d. hand the finished picture to FFmpeg.
#   7. FFmpeg squeezes all the pictures into an MP4 with the soundtrack.
#   8. Save a credits file listing every clip, photo and sound used.
#
# The pictures go to FFmpeg through a "pipe" — a direct in-memory
# connection between two programs — so thousands of frames never
# have to be saved to disk as separate image files.
#
# How it fits in:
#   render.py → render_animated_video() (here) → FFmpeg → MP4
# =============================================================

import json
import math
import os
import subprocess

from src.video.animation.drawing import VIDEO_HEIGHT, VIDEO_WIDTH, hex_to_rgb
from src.video.animation.footage import draw_fallback_background, find_footage_for_scenes
from src.video.animation.overlays import (
    build_caption_groups,
    draw_brand_bug,
    draw_captions,
    draw_end_card,
    draw_progress_bar,
    draw_section_chip,
    draw_wipe,
    find_caption_group,
)
from src.video.animation.photos import find_photos_for_scenes
from src.video.animation.quality_gate import run_quality_gate
from src.video.animation.scenes import draw_scene
from src.video.animation.shots import ShotPlayer, plan_shots
from src.video.animation.storyboard import add_scene_times, get_storyboard
from src.video.sound_design import build_soundtrack


# The look of the animation when a channel config does not set its own.
# Every value can be overridden under video.animation in the channel YAML.
DEFAULT_ANIMATION_SETTINGS = {
    "fps": 30,                       # frames (pictures) per second
    "footage_brightness": 0.6,       # 1.0 = footage as-is, lower = darker
    "accent_colors": ["#FFD23F", "#3BE8B0", "#FF6B8B", "#5CC8FF"],
    "text_color": "#FFFFFF",
    "fallback_background": ["#101A3A", "#3A1050"],   # gradient, top → bottom
    "footage_topics": {},            # approved stock video searches (see YAML)
    "max_shot_seconds": 2.8,         # the background changes at least this often
    "photos": True,                  # use freely licensed photos of named subjects
    "brand_bug": True,               # channel name in a small pill, top left
    "end_card_seconds": 2.0,         # how long the end card stays after the voice
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
        channel_name (str): The channel's display name (kept for older callers;
                            the brand bug now shows it instead of the hook label).
    """
    section_start = 0.0
    previous_section = None

    for scene in scenes:
        scene["starts_section"] = scene["section"] != previous_section
        if scene["starts_section"]:
            section_start = scene["start"]
        scene["section_start"] = section_start

        # No label during the hook: the brand bug already shows the
        # channel's name, and the opening should be as clean as possible.
        if scene["section"] == "Hook":
            scene["section_label"] = ""
        else:
            scene["section_label"] = scene["section"].upper()

        previous_section = scene["section"]


def draw_frame(frame, now, scenes, scene_number, caption_groups, settings,
               font_size, total_duration, branding=None):
    """
    Draw everything that goes on top of the background for one video frame.

    Args:
        frame (PIL.Image): The frame, already holding the background picture.
        now (float): The current time in the video, in seconds.
        scenes (list[dict]): All storyboard scenes, with their times.
        scene_number (int): Which scene is playing right now.
        caption_groups (list[dict]): Output of build_caption_groups().
        settings (dict): Output of load_animation_settings().
        font_size (int): Caption font size from the channel config.
        total_duration (float): Length of the whole video in seconds.
        branding (dict or None): {"name", "slogan", "end_card_start"} —
            the brand bug and end card. None draws neither.

    Returns:
        PIL.Image: The same frame, now complete.
    """
    scene = scenes[scene_number]
    colors = get_scene_colors(scene_number, settings)
    brand_color = hex_to_rgb(settings["accent_colors"][0])
    end_card_showing = branding is not None and now >= branding["end_card_start"]

    # Layer 2: the animated graphic for this sentence.
    draw_scene(frame, scene, now - scene["start"], scene["end"] - scene["start"],
               colors)

    # Layer 3: the things that are always on screen.
    draw_progress_bar(frame, now / max(total_duration, 0.1), colors["accent"])
    if branding is not None and settings["brand_bug"]:
        draw_brand_bug(frame, branding["name"].upper(), brand_color)
    draw_section_chip(frame, scene["section_label"], now - scene["section_start"],
                      colors["accent"])
    if not end_card_showing:
        draw_captions(frame, find_caption_group(caption_groups, now), now, font_size,
                      colors["text"], brand_color)

    # A coloured wipe covers the screen at each change of section. Check
    # both the section we just entered and the one we are about to enter.
    if scene["starts_section"] and scene_number > 0:
        draw_wipe(frame, now, scene["start"], colors["accent"])
    if scene_number + 1 < len(scenes):
        next_scene = scenes[scene_number + 1]
        if next_scene["starts_section"]:
            next_colors = get_scene_colors(scene_number + 1, settings)
            draw_wipe(frame, now, next_scene["start"], next_colors["accent"])

    if end_card_showing:
        draw_end_card(frame, now, branding["end_card_start"], branding["name"],
                      branding["slogan"], brand_color, colors["text"])
    return frame


def find_end_card_start(word_timings, slogan, voice_duration):
    """
    Decide when the end card appears: as the narrator starts the slogan.

    Args:
        word_timings (list[dict]): One {"word", "start", "end"} per word.
        slogan (str): e.g. "That's the news, made simple." May be "".
        voice_duration (float): Length of the voiceover in seconds.

    Returns:
        float: Seconds from the start of the video.

    Example:
        find_end_card_start(timings, "That's the news, made simple.", 80.0)  # → 77.9
    """
    slogan_words = slogan.split()
    count = len(slogan_words)
    if count and len(word_timings) >= count:
        last_words = []
        for timing in word_timings[-count:]:
            last_words.append(timing["word"].strip(".,!?\"'’").lower())
        wanted = []
        for word in slogan_words:
            wanted.append(word.strip(".,!?\"'’").lower())
        if last_words == wanted:
            return word_timings[-count]["start"]
    return max(0.0, voice_duration - 0.5)


def write_credits(video_path, shots, sound_credits):
    """
    Save a list of every outside clip, photo and sound used in the video.

    Two files are written next to the video: <video>_credits.json (for
    programs) and <video>_credits.txt (ready to paste into a description
    when a licence such as CC BY asks for attribution).

    Args:
        video_path (str): The finished .mp4 path.
        shots (list[dict]): From plan_shots(); footage/photo shots have credits.
        sound_credits (list[dict]): From build_soundtrack().

    Returns:
        list[dict]: The unique credits that were saved.
    """
    unique = []
    for shot in shots:
        credit = shot.get("credit")
        if credit and credit not in unique:
            unique.append(credit)
    for credit in sound_credits:
        if credit not in unique:
            unique.append(credit)

    base = os.path.splitext(video_path)[0]
    with open(base + "_credits.json", "w", encoding="utf-8") as f:
        json.dump(unique, f, indent=2, ensure_ascii=False)
    lines = []
    for credit in unique:
        lines.append(f"{credit.get('title', '')} — {credit.get('author', 'Unknown')} "
                     f"({credit.get('source', '')}, {credit.get('licence', '')}) "
                     f"{credit.get('page_url', '')}".strip())
    with open(base + "_credits.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return unique


def render_animated_video(script_path, channel_config, audio_path, word_timings,
                          video_path, llm=None, font_size=90, use_footage=True,
                          channel_key="news"):
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
        use_footage (bool): If False, skip stock footage and photo downloads
                            and use the moving gradient background instead.
        channel_key (str): The channel's file name (for its own music folder).

    Returns:
        str: video_path, once the file has been written.

    Raises:
        QualityGateFailed: If the plan breaks a rule (see quality_gate.py).

    Example:
        render_animated_video("output/news/story.md", config, "cache/audio/a.wav",
                              word_timings, "output/videos/news/story.mp4", llm=llm)
    """
    # Imported here because render.py also imports this file.
    from src.video.render import _get_audio_duration

    settings = load_animation_settings(channel_config)
    video_settings = channel_config.get("video", {})
    fps = settings["fps"]
    voice_duration = _get_audio_duration(audio_path)
    # The end card stays on screen a little after the voice finishes.
    duration = voice_duration + float(settings["end_card_seconds"])

    # --- Step 1: The plan ---
    scenes = get_storyboard(script_path, channel_config, llm)
    if not scenes:
        raise RuntimeError(f"No spoken text found in {script_path} — nothing to animate.")
    add_scene_times(scenes, word_timings, duration)
    add_section_details(scenes, channel_config.get("name", ""))

    words_per_caption = video_settings.get("words_per_caption", 3)
    caption_groups = build_caption_groups(scenes, word_timings, words_per_caption, font_size)

    # --- Step 2: Backgrounds — real photos first, then stock footage ---
    if use_footage:
        if settings["photos"]:
            print("  Looking for freely licensed photos of named people and places...")
            photo_count = find_photos_for_scenes(scenes)
            print(f"  Photos ready: {photo_count} scenes.")
        print("  Finding stock footage for each scene...")
        found = find_footage_for_scenes(scenes, settings["footage_topics"])
        print(f"  Footage ready: {found}/{len(scenes)} scenes have a video clip.")
    else:
        for scene in scenes:
            scene["clip_path"] = None
            scene["photo_path"] = None

    # --- Step 3: Shots — the picture changes every 1-3 seconds ---
    shots = plan_shots(scenes, duration, float(settings["max_shot_seconds"]))

    # --- Step 4: Soundtrack ---
    base = os.path.splitext(video_path)[0]
    soundtrack = build_soundtrack(audio_path, duration, scenes, video_settings,
                                  channel_key, base + "_soundtrack.wav")

    # --- Step 5: Quality gate (stops here if the plan is not usable) ---
    run_quality_gate(scenes, shots, caption_groups, font_size, soundtrack, duration,
                     channel_config, base + "_quality.json")

    branding = {
        "name": channel_config.get("name", ""),
        "slogan": channel_config.get("slogan", ""),
        "end_card_start": find_end_card_start(word_timings, channel_config.get("slogan", ""),
                                              voice_duration),
    }

    # --- Step 6: Start FFmpeg, ready to receive frames ---
    # "-f rawvideo ... -i -" tells FFmpeg to expect plain, uncompressed
    # pictures of a known size arriving through the pipe.
    command = [
        "ffmpeg", "-y",
        "-f", "rawvideo", "-pix_fmt", "rgb24",
        "-s", f"{VIDEO_WIDTH}x{VIDEO_HEIGHT}",
        "-framerate", str(fps),
        "-i", "-",
        "-i", soundtrack["path"],
        "-map", "0:v", "-map", "1:a",
        "-c:v", "libx264", "-preset", "fast", "-crf", "21", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-ar", "44100",
        "-t", f"{duration:.3f}",
        "-movflags", "+faststart",
        video_path,
    ]

    # FFmpeg's messages go to a log file so they cannot clog the pipe.
    log_path = base + "_ffmpeg.log"
    log_file = open(log_path, "w", encoding="utf-8")
    encoder = subprocess.Popen(command, stdin=subprocess.PIPE,
                               stdout=log_file, stderr=log_file)

    gradient_top = hex_to_rgb(settings["fallback_background"][0])
    gradient_bottom = hex_to_rgb(settings["fallback_background"][1])
    player = ShotPlayer(shots, fps, settings["footage_brightness"])

    total_frames = math.ceil(duration * fps)
    scene_number = 0
    next_report_percent = 20

    print(f"  Drawing {total_frames} frames ({duration:.1f}s at {fps} fps)...")

    # --- Step 7: Draw and send every frame ---
    try:
        for frame_number in range(total_frames):
            now = frame_number / fps

            # Move to the next scene once its start time has arrived.
            while scene_number + 1 < len(scenes) and now >= scenes[scene_number + 1]["start"]:
                scene_number += 1

            # Layer 1: the background. If there is none, or a clip cannot
            # be read, draw the moving gradient so the screen is never blank.
            frame = player.frame_at(now)
            if frame is None:
                colors = get_scene_colors(scene_number, settings)
                frame = draw_fallback_background(now, gradient_top, gradient_bottom,
                                                 colors["accent"])

            draw_frame(frame, now, scenes, scene_number, caption_groups, settings,
                       font_size, duration, branding)
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
        player.close()
        log_file.close()

    if encoder.returncode != 0:
        with open(log_path, "r", encoding="utf-8") as f:
            log_text = f.read()
        raise RuntimeError(
            f"FFmpeg failed (exit code {encoder.returncode}).\n"
            f"Log:\n{log_text[-2000:]}"
        )

    # The log is only useful when something went wrong, and the soundtrack
    # is now inside the MP4, so neither needs to be kept.
    os.remove(log_path)
    os.remove(soundtrack["path"])

    # --- Step 8: Credits for every clip, photo and sound used ---
    credits = write_credits(video_path, shots, soundtrack["credits"])
    print(f"  Credits saved: {base}_credits.txt ({len(credits)} items)")
    return video_path

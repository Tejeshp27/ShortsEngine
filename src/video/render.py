# =============================================================
# src/video/render.py — assemble the final 9:16 MP4 with FFmpeg
#
# Builds a 9:16 (1080×1920) MP4 video from an approved script:
#
#   1. Generate voiceover audio with edge-tts (see tts.py)
#   2. Use Pillow to draw PNG frames — one per caption group
#      (background colour, headline text, word captions)
#   3. Write an FFmpeg concat script that lists each frame and
#      how long to hold it
#   4. Run FFmpeg to encode the frames into a video and mix in audio
#
# Using Pillow for text rendering means we don't need FFmpeg's
# optional drawtext/subtitles filters (which require libfreetype
# and libass to be compiled in — not always available).
# =============================================================

import hashlib
import os
import subprocess

from src.video.frames import build_frames
from src.video.tts import get_voiceover


# Output folder for finished MP4s (also written to the DB).
VIDEO_OUTPUT_FOLDER = os.path.join("output", "videos")

# Temporary files produced mid-render (frames, concat script) go here.
RENDER_TEMP_FOLDER = os.path.join("cache", "render")


def _check_ffmpeg():
    """
    Confirm FFmpeg is installed; raise a clear error if it isn't.

    Raises:
        RuntimeError: If 'ffmpeg' is not found on the system PATH.
    """
    result = subprocess.run(
        ["ffmpeg", "-version"],
        capture_output=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            "FFmpeg is not installed or not on your PATH.\n"
            "Install it with:  brew install ffmpeg   (Mac)\n"
            "                  sudo apt install ffmpeg  (Linux)"
        )


def _get_audio_duration(audio_path):
    """
    Ask FFprobe for the length of an audio file in seconds.

    FFprobe is a tool that ships with FFmpeg.

    Args:
        audio_path (str): Path to the .mp3 voiceover file.

    Returns:
        float: Duration in seconds, e.g. 94.3.
    """
    result = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            audio_path,
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {result.stderr}")
    return float(result.stdout.strip())


def _pick_background_color(channel_key):
    """
    Return a hex colour string for the video background.

    Each channel can have its own brand colour. Dark colours work
    best because white caption text shows up clearly against them.

    Args:
        channel_key (str): The channel key, e.g. "news".

    Returns:
        str: Hex string like "0x1a1a2e".
    """
    colors = {
        "news": "0x1a1a2e",    # dark navy — serious, clean look
    }
    return colors.get(channel_key, "0x000000")


def _output_video_path(channel_key, script_path):
    """
    Choose where to save the finished MP4, without overwriting existing files.

    Args:
        channel_key (str): The channel key, e.g. "news".
        script_path (str): The script .md file (used to derive the slug).

    Returns:
        str: e.g. "output/videos/news/2026-11-07-big-story.mp4"
    """
    base_name = os.path.splitext(os.path.basename(script_path))[0]
    folder = os.path.join(VIDEO_OUTPUT_FOLDER, channel_key)
    os.makedirs(folder, exist_ok=True)

    path = os.path.join(folder, f"{base_name}.mp4")
    counter = 2
    while os.path.exists(path):
        path = os.path.join(folder, f"{base_name}-{counter}.mp4")
        counter += 1
    return path


def render_video(script_path, story_title, channel_key, voice,
                 font_size=90, dry_run=False):
    """
    Turn an approved script into a ready-to-post 9:16 MP4 video.

    Steps:
      1. Generate (or load from cache) the voiceover MP3 and word timings.
      2. Use Pillow to draw one PNG per caption group.
      3. Run FFmpeg to encode the PNGs into a video and mix in the audio.

    Args:
        script_path (str): Path to the approved .md script file.
        story_title (str): The story headline (shown at the top of the video).
        channel_key (str): The channel key, e.g. "news".
        voice (str): The edge-tts voice name from the channel config.
        font_size (int): Caption font size in pixels (from channel config).
        dry_run (bool): If True, skip TTS and FFmpeg; return a placeholder path.

    Returns:
        str: Path to the finished .mp4 file.

    Example:
        path = render_video(
            "output/news/2026-11-07-big-story.md",
            "Big story happened today",
            "news",
            "en-US-AriaNeural",
        )
    """
    _check_ffmpeg()

    # --- Step 1: Voiceover ---
    print("  Generating voiceover...")
    audio_path, word_timings = get_voiceover(script_path, voice, dry_run=dry_run)

    if dry_run:
        video_path = _output_video_path(channel_key, script_path)
        print(f"  [DRY RUN] Would save video to: {video_path}")
        return video_path

    # --- Step 2: Frames ---
    print("  Drawing video frames with Pillow...")
    bg_color = _pick_background_color(channel_key)

    # Give each render job a unique folder based on the audio file path
    # so re-renders with different settings don't collide.
    fingerprint = hashlib.sha256(audio_path.encode()).hexdigest()[:16]
    frames_folder = os.path.join(RENDER_TEMP_FOLDER, fingerprint)

    concat_path = build_frames(
        word_timings=word_timings,
        story_title=story_title,
        background_color_hex=bg_color,
        output_folder=frames_folder,
        font_size=font_size,
    )

    # Count how many frames were generated for the status message.
    frame_count = sum(1 for f in os.listdir(frames_folder) if f.endswith(".png"))
    print(f"  Drew {frame_count} frames.")

    # --- Step 3: FFmpeg encode ---
    print("  Encoding video with FFmpeg...")
    duration = _get_audio_duration(audio_path)
    video_path = _output_video_path(channel_key, script_path)

    # FFmpeg reads the frames from the concat script and mixes in audio.
    # -safe 0 is required because the concat script uses absolute paths.
    command = [
        "ffmpeg",
        "-y",                           # overwrite output without asking
        "-f", "concat",
        "-safe", "0",                   # allow absolute paths in concat script
        "-i", concat_path,              # input 0: frame sequence
        "-i", audio_path,               # input 1: voiceover audio
        "-map", "0:v",                  # video from input 0 (frames)
        "-map", "1:a",                  # audio from input 1 (voiceover)
        "-c:v", "libx264",              # H.264 video codec (universal)
        "-preset", "fast",              # encoding speed vs file size
        "-crf", "23",                   # quality (18=great, 28=small; 23 is default)
        "-pix_fmt", "yuv420p",          # required for compatibility on all devices
        "-c:a", "aac",                  # AAC audio codec (required for TikTok/Instagram)
        "-b:a", "192k",                 # audio bitrate
        "-t", str(duration),            # stop at the end of the voiceover
        "-movflags", "+faststart",      # move metadata to start for streaming
        video_path,
    ]

    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"FFmpeg failed (exit code {result.returncode}).\n"
            f"Stderr:\n{result.stderr[-2000:]}"
        )

    size_mb = os.path.getsize(video_path) / (1024 * 1024)
    print(f"  Video saved: {video_path} ({size_mb:.1f} MB, {duration:.1f}s)")
    return video_path

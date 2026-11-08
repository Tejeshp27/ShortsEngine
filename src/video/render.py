# =============================================================
# src/video/render.py — assemble the final 9:16 MP4 with FFmpeg
#
# Builds a 9:16 (1080×1920) MP4 video from an approved script:
#
#   1. Generate voiceover audio with edge-tts (see tts.py)
#   2. (Phase V.6) Optionally build a visual plan — Claude Haiku
#      decides what to show each segment (stock photo, chart, or
#      text card). Stock photos come from Pexels; charts from
#      matplotlib. Both are cached after first use.
#   3. Use Pillow to draw PNG frames — one per caption group,
#      with the planned visual as the background where available.
#   4. Write an FFmpeg concat script and encode the final MP4.
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


def _build_visual_frames(visual_plan, word_timings, channel_key,
                          no_paid_visuals=False):
    """
    Download/generate visuals and return a list of image paths — one per
    caption group.

    The visual plan has one entry per ~15-word segment. Word timings
    drive caption groups at 3 words each. So roughly every 5 caption
    groups share the same visual (15 words ÷ 3 words/group = 5 groups).

    Args:
        visual_plan (list[dict]): Output from visual_planner.plan_visuals().
        word_timings (list[dict]): Word timing data from tts.py.
        channel_key (str): Used for Pexels credits file path.
        no_paid_visuals (bool): If True, skip Pexels (use text cards only).

    Returns:
        list[str or None]: One entry per caption group. Each entry is a
            local image path or None (fall back to solid background).
    """
    from src.video.captions import _group_words, WORDS_PER_CAPTION
    from src.video.stock import fetch_stock_photo
    from src.video.charts import make_chart
    import hashlib as _hashlib

    if not visual_plan:
        return []

    caption_groups = _group_words(word_timings, WORDS_PER_CAPTION)
    total_caption_groups = len(caption_groups) + 1  # +1 for the trailing blank frame

    # How many caption groups per visual segment.
    # Visual segments are 15 words; caption groups are 3 words → 5 groups each.
    from src.video.visual_planner import WORDS_PER_SEGMENT
    groups_per_segment = max(1, WORDS_PER_SEGMENT // WORDS_PER_CAPTION)

    # Pre-resolve each visual segment to a local file path (or None).
    resolved = []
    for i, segment in enumerate(visual_plan):
        visual_type = segment.get("visual_type", "text_card")
        query = segment.get("query", "")
        path = None

        if visual_type == "chart" and query:
            # Generate a matplotlib chart from the data string.
            fingerprint = _hashlib.sha256(query.encode()).hexdigest()[:16]
            chart_path = os.path.join("cache", "charts", f"{fingerprint}.png")
            path = make_chart(query, chart_path, channel_key)

        elif visual_type == "stock_photo" and query:
            if no_paid_visuals:
                # Free-only mode: skip Pexels, try Wikimedia Commons directly.
                from src.video.stock import fetch_wikimedia_photo
                path = fetch_wikimedia_photo(query)
            else:
                # Normal mode: try Pexels first, then Wikimedia as fallback.
                path = fetch_stock_photo(query, channel_key=channel_key)

        # text_card → path stays None; the renderer uses the solid background.
        resolved.append(path)

    # Expand the per-segment list to one entry per caption group.
    visual_frames = []
    for group_index in range(total_caption_groups):
        segment_index = min(group_index // groups_per_segment, len(resolved) - 1)
        visual_frames.append(resolved[segment_index])

    return visual_frames


def render_video(script_path, story_title, channel_key, voice,
                 font_size=90, dry_run=False, llm=None,
                 channel_config=None, no_paid_visuals=False):
    """
    Turn an approved script into a ready-to-post 9:16 MP4 video.

    Steps:
      1. Generate (or load from cache) the voiceover MP3 and word timings.
      2. (Optional) Generate a visual plan with Claude Haiku: each spoken
         segment gets a background image (stock photo or chart).
      3. Use Pillow to draw one PNG per caption group.
      4. Run FFmpeg to encode the PNGs into a video and mix in the audio.

    Args:
        script_path (str): Path to the approved .md script file.
        story_title (str): The story headline (shown at the top of the video).
        channel_key (str): The channel key, e.g. "news".
        voice (str): The edge-tts voice name from the channel config.
        font_size (int): Caption font size in pixels (from channel config).
        dry_run (bool): If True, skip TTS and FFmpeg; return a placeholder path.
        llm (LLMClient or None): If provided, visual planning is run.
                                 If None, visuals are skipped (original behaviour).
        channel_config (dict or None): Required when llm is provided.
        no_paid_visuals (bool): Skip Pexels calls; use only free local visuals.

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

    # --- Step 2: Visual plan (optional) ---
    visual_frames = []
    if llm is not None and channel_config is not None:
        from src.video.visual_planner import plan_visuals
        print("  Planning visuals...")
        visual_plan = plan_visuals(script_path, channel_config, llm,
                                   dry_run=dry_run)
        visual_frames = _build_visual_frames(
            visual_plan, word_timings, channel_key,
            no_paid_visuals=no_paid_visuals,
        )
        visuals_used = sum(1 for v in visual_frames if v is not None)
        print(f"  Visuals resolved: {visuals_used}/{len(visual_frames)} frames "
              f"have an image background.")

    # --- Step 3: Frames ---
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
        visual_frames=visual_frames if visual_frames else None,
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

# =============================================================
# src/video/render.py — assemble the final 9:16 MP4 with FFmpeg
#
# Builds a 9:16 (1080×1920) MP4 video from an approved script:
#
#   1. Generate voiceover audio (see tts.py)
#   2. (Optional) Run the M-series scene planner — Claude Haiku
#      decides the visual type for each spoken segment:
#        footage   → Pexels Video frames   (stock_video.py)
#        image     → article photo / Pexels / Wikimedia (stock.py)
#        animated  → Pillow motion graphic  (animated_frames.py)
#        chart     → matplotlib chart       (charts.py)
#        text_card → solid dark background  (no image)
#   3. Use Pillow to draw PNG frames — one per caption group,
#      with the planned visual as the background where available.
#   4. Write an FFmpeg concat script and encode the final MP4.
#
# Using Pillow for text rendering means we don't need FFmpeg's
# optional drawtext/subtitles filters (which require libfreetype
# and libass to be compiled in — not always available).
#
# Animated mode (--render --animated) skips steps 2–4 above and
# hands over to src/video/animation/renderer.py, which draws every
# frame itself: stock footage playing full-screen, an animated
# graphic per sentence, and word-by-word captions on top.
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


def _build_ffmpeg_command(concat_path, audio_path, video_path, duration,
                          sfx_path=None, music_path=None, music_volume=0.12,
                          video_input_flags=None):
    """
    Build the FFmpeg command to encode the final video.

    Handles all combinations of optional audio tracks (hook sound effect
    and background music) by adding them as extra inputs and using the
    amix filter to blend them at the right volumes.

    Input numbering:
      0 = frame sequence (always)
      1 = voiceover (always)
      2 = hook sound effect (if sfx_path is provided)
      3 = background music (if music_path is provided; may be 2 if no sfx)

    Args:
        concat_path (str): Path to the FFmpeg concat script listing the frames.
        audio_path (str): Path to the voiceover .mp3 file.
        video_path (str): Where to write the finished .mp4.
        duration (float): Video length in seconds (from the voiceover).
        sfx_path (str or None): Path to the hook sound effect .mp3, or None.
        music_path (str or None): Path to the background music .mp3, or None.
        music_volume (float): Music volume as a fraction of the voiceover (0–1).
        video_input_flags (list[str] or None): FFmpeg flags describing where
            the pictures come from. Leave as None to read the frame list in
            concat_path. The animated renderer passes its own flags here so
            FFmpeg reads frames straight from Python instead.

    Returns:
        list[str]: The FFmpeg command as a list of arguments, ready for subprocess.
    """
    # Input 0 is always the pictures. By default they come from the
    # concat script written by frames.py.
    if video_input_flags is None:
        video_input_flags = ["-f", "concat", "-safe", "0", "-i", concat_path]

    # Shared output encoding settings — same regardless of audio configuration.
    output_flags = [
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "23",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "192k",
        "-t", str(duration),
        "-movflags", "+faststart",
        video_path,
    ]

    # Decide which optional audio tracks are actually available.
    has_sfx = sfx_path and os.path.exists(sfx_path)
    has_music = music_path and os.path.exists(music_path)

    if not has_sfx and not has_music:
        # Simple case: just voiceover. No filter needed.
        return [
            "ffmpeg", "-y",
        ] + video_input_flags + [
            "-i", audio_path,
            "-map", "0:v",
            "-map", "1:a",
        ] + output_flags

    # Build the input list and figure out which input index each track gets.
    inputs = video_input_flags + [                           # input 0: frames
        "-i", audio_path,                                    # input 1: voiceover
    ]

    # Track which amix input index each audio stream occupies.
    next_index = 2
    sfx_index = None
    music_index = None

    if has_sfx:
        inputs += ["-i", sfx_path]
        sfx_index = next_index
        next_index += 1
        print("  Mixing hook sound effect.")

    if has_music:
        # -stream_loop -1 makes the music loop if shorter than the video.
        inputs += ["-stream_loop", "-1", "-i", music_path]
        music_index = next_index
        next_index += 1
        print(f"  Mixing background music at volume={music_volume}.")

    # Build the amix filter string.
    # amix blends N audio streams. We set weights so the voiceover stays
    # at full volume and the other tracks are quieter.
    # normalize=0 prevents FFmpeg from lowering the voiceover to average all streams.
    audio_count = next_index - 1   # number of audio inputs (voiceover + extras)
    audio_labels = "[1:a]"         # voiceover is always input 1
    weights = ["1"]                # voiceover weight = 1 (full volume)

    if sfx_index is not None:
        audio_labels += f"[{sfx_index}:a]"
        weights.append("0.6")      # SFX at 60% — punchy but doesn't drown voice

    if music_index is not None:
        audio_labels += f"[{music_index}:a]"
        weights.append(str(music_volume))

    weight_str = " ".join(weights)
    filter_complex = (
        f"{audio_labels}amix=inputs={audio_count}:"
        f"weights={weight_str}:normalize=0[aout]"
    )

    return [
        "ffmpeg", "-y",
    ] + inputs + [
        "-filter_complex", filter_complex,
        "-map", "0:v",
        "-map", "[aout]",
    ] + output_flags


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


def _prepare_extra_audio(channel_config, channel_key, duration, use_music):
    """
    Get the optional extra sound tracks: the hook sound effect and music.

    Both render modes (classic and animated) mix the same extra audio, so
    the work of fetching it lives in this one function.

    Args:
        channel_config (dict or None): The channel config. If None, no
                                       extra audio is used.
        channel_key (str): The channel key, e.g. "news".
        duration (float): Video length in seconds (music is made to fit).
        use_music (bool): If True, generate background music via Mubert.

    Returns:
        tuple: (sfx_path, music_path, music_volume). Either path is None
               when that track is not available.
    """
    sfx_path = None
    music_path = None
    music_volume = 0.12  # default: music at 12% of voiceover volume

    if channel_config is None:
        return sfx_path, music_path, music_volume

    video_settings = channel_config.get("video", {})
    music_volume = video_settings.get("music_volume", 0.12)

    # Background music: a Mubert track matching the channel's music_mood,
    # mixed under the voiceover at the channel's music_volume level.
    if use_music:
        from src.video.music import get_background_music
        music_path = get_background_music(
            duration,
            mood=video_settings.get("music_mood", "focused"),
            channel_key=channel_key,
        )

    # Hook sound effect: a short punchy sound from Freesound, played at the
    # very start of the video to grab attention in the first second.
    from src.video.sound_effects import get_hook_sound
    sfx_path = get_hook_sound(
        video_settings.get("hook_sound_query", "news intro impact hit")
    )
    if sfx_path:
        print("  Hook sound effect ready.")

    return sfx_path, music_path, music_volume


def _build_visual_frames(scene_plan, word_timings, channel_key,
                          no_paid_visuals=False, script_path=None,
                          style="auto"):
    """
    Resolve the scene plan to a flat list of image paths — one per caption group.

    The scene plan has one entry per ~15-word segment. Each segment maps to
    roughly 5 caption groups (15 words ÷ 3 words/group). This function converts
    the per-segment plan into a per-caption-group list of PNG paths that
    build_frames() can use as background images.

    For "footage" and "animated" scene types, this function returns a *list*
    of paths per segment (one frame per caption group) rather than a single
    shared path. This lets the background "play" through the segment.

    Args:
        scene_plan (list[dict]): Output from scene_planner.plan_scenes().
                                 Each dict has: segment, scene_type, query, description.
                                 Also accepts old visual_planner format (visual_type key).
        word_timings (list[dict]): Word timing data from tts.py.
        channel_key (str): Used for Pexels credits file path.
        no_paid_visuals (bool): If True, skip Pexels (use text cards and Wikimedia only).
        script_path (str or None): Path to the .md script (for article image extraction).
        style (str): Visual style preference passed through for logging.

    Returns:
        list[str or None]: One entry per caption group — either a local PNG path
            or None (render.py treats None as a solid-colour background).
    """
    from src.video.captions import _group_words, WORDS_PER_CAPTION
    from src.video.stock import fetch_stock_photo
    from src.video.charts import make_chart
    from src.video.article_images import get_article_images
    from src.video.stock_video import get_video_frames
    from src.video.animated_frames import render_animated_scene

    if not scene_plan:
        return []

    caption_groups = _group_words(word_timings, WORDS_PER_CAPTION)
    total_caption_groups = len(caption_groups) + 1  # +1 for the trailing blank frame

    # How many caption groups per visual segment.
    # Segments are 15 words; caption groups are 3 words → 5 groups per segment.
    WORDS_PER_SEGMENT = 15
    groups_per_segment = max(1, WORDS_PER_SEGMENT // WORDS_PER_CAPTION)

    # Fetch article editorial photos first — most relevant visuals available.
    article_images = get_article_images(script_path)
    article_image_index = 0

    # Cache folder for animated frames.
    animated_folder = os.path.join("cache", "animated")

    # --- Resolve each segment to a path (or list of paths for footage/animated) ---
    # resolved[i] is one of:
    #   str       — single image path (image, chart, text_card)
    #   list[str] — multiple frames  (footage, animated)
    #   None      — solid background  (text_card or fallback)
    resolved = []

    for seg_index, segment in enumerate(scene_plan):
        # Accept both old "visual_type" key and new "scene_type" key.
        scene_type = segment.get("scene_type") or segment.get("visual_type", "text_card")
        # Treat the old "stock_photo" type as "image" for backward compatibility.
        if scene_type == "stock_photo":
            scene_type = "image"

        query = segment.get("query", "")
        description = segment.get("description", "")
        item = None   # will hold the resolved path(s) for this segment

        # --- footage: Pexels Video clip extracted into N frames ---
        if scene_type == "footage" and query and not no_paid_visuals:
            frames = get_video_frames(
                query=query,
                num_frames=groups_per_segment,
                segment_index=seg_index,
            )
            if frames:
                item = frames   # list of paths
            else:
                # Fall back to stock photo if no video was found.
                scene_type = "image"

        # --- animated: Pillow-drawn motion graphic ---
        if scene_type == "animated" and query:
            frames = render_animated_scene(
                query=query,
                description=description,
                num_frames=groups_per_segment,
                output_folder=animated_folder,
            )
            if frames:
                item = frames   # list of paths
            # If the query couldn't be parsed, item stays None → text card.

        # --- chart: matplotlib bar/line chart ---
        elif scene_type == "chart" and query:
            fingerprint = hashlib.sha256(query.encode()).hexdigest()[:16]
            chart_path = os.path.join("cache", "charts", f"{fingerprint}.png")
            item = make_chart(query, chart_path, channel_key)

        # --- image: editorial photo from article, then Pexels, then Wikimedia ---
        elif scene_type == "image":
            # Priority 1: photo from the actual source article.
            if article_images:
                item = article_images[article_image_index % len(article_images)]
                article_image_index += 1
            else:
                # Priority 2: Pexels stock photo (or Wikimedia if no Pexels key).
                if query:
                    if no_paid_visuals:
                        from src.video.stock import fetch_wikimedia_photo
                        item = fetch_wikimedia_photo(query)
                    else:
                        item = fetch_stock_photo(query, channel_key=channel_key)

        # text_card → item stays None; build_frames() uses the solid background.

        resolved.append(item)

    # --- Expand per-segment list to one entry per caption group ---
    visual_frames = []

    for group_index in range(total_caption_groups):
        # Clamp so the last segment covers any extra caption groups.
        seg_index = min(group_index // groups_per_segment, len(resolved) - 1)
        item = resolved[seg_index]

        if isinstance(item, list):
            # Multi-frame segment (footage or animated): pick the frame at
            # the position within this segment.
            pos_in_segment = group_index - (seg_index * groups_per_segment)
            # Clamp in case the frame list is shorter than expected.
            pos_in_segment = max(0, min(pos_in_segment, len(item) - 1))
            visual_frames.append(item[pos_in_segment])
        else:
            # Single-path or None — same image for every group in this segment.
            visual_frames.append(item)

    visuals_used = sum(1 for v in visual_frames if v is not None)
    print(f"  Visuals resolved: {visuals_used}/{len(visual_frames)} frames have a visual.")
    return visual_frames


def render_video(script_path, story_title, channel_key, voice,
                 font_size=90, dry_run=False, llm=None,
                 channel_config=None, no_paid_visuals=False,
                 tts_provider="edge", style="auto", use_music=False,
                 animated=False, cost_tracker=None):
    """
    Turn an approved script into a ready-to-post 9:16 MP4 video.

    Steps:
      1. Generate (or load from cache) the voiceover audio and word timings.
      2. (Optional) Run the M-series scene planner with Claude Haiku: each
         spoken segment gets a visual type (footage, image, animated, chart,
         or text_card) and the matching visual is downloaded or generated.
      3. Use Pillow to draw one PNG per caption group.
      4. Run FFmpeg to encode the PNGs into a video and mix in the audio.

    Args:
        script_path (str): Path to the approved .md script file.
        story_title (str): The story headline (shown at the top of the video).
        channel_key (str): The channel key, e.g. "news".
        voice (str): The TTS voice name from the channel config.
        font_size (int): Caption font size in pixels (from channel config).
        dry_run (bool): If True, skip TTS and FFmpeg; return a placeholder path.
        llm (LLMClient or None): If provided, scene planning is run.
                                 If None, visuals are skipped (original behaviour).
        channel_config (dict or None): Required when llm is provided.
        no_paid_visuals (bool): Skip Pexels calls; use only free local visuals.
        tts_provider (str): "edge", "macos", "kokoro", or "elevenlabs".
        style (str): Visual style: "auto" (Haiku decides per segment),
                     "footage" (prefer video), or "animated" (prefer graphics).
        use_music (bool): If True, generate background music via Mubert and
                          mix it under the voiceover at the channel's music_volume.
        animated (bool): If True, make a fully animated video (stock footage
                         playing behind animated graphics) instead of the
                         classic one-picture-per-caption video. Needs
                         channel_config.
        cost_tracker: Optional database tracker for paid voice generation.

    Returns:
        str: Path to the finished .mp4 file.

    Example:
        path = render_video(
            "output/news/2026-11-07-big-story.md",
            "Big story happened today",
            "news",
            "Flo (English (US))",
            tts_provider="macos",
        )
    """
    _check_ffmpeg()

    # --- Step 1: Voiceover ---
    print("  Generating voiceover...")
    audio_path, word_timings = get_voiceover(
        script_path, voice, tts_provider=tts_provider, dry_run=dry_run,
        cost_tracker=cost_tracker, voice_settings=(channel_config or {}).get("video", {})
    )

    if dry_run:
        video_path = _output_video_path(channel_key, script_path)
        print(f"  [DRY RUN] Would save video to: {video_path}")
        return video_path

    # --- Animated mode: hand over to the animation engine ---
    if animated:
        if channel_config is None:
            raise ValueError("Animated rendering needs the channel config.")

        from src.video.animation.renderer import render_animated_video

        duration = _get_audio_duration(audio_path)
        video_path = _output_video_path(channel_key, script_path)
        sfx_path, music_path, music_volume = _prepare_extra_audio(
            channel_config, channel_key, duration, use_music
        )
        render_animated_video(
            script_path=script_path,
            channel_config=channel_config,
            audio_path=audio_path,
            word_timings=word_timings,
            video_path=video_path,
            llm=llm,
            font_size=font_size,
            sfx_path=sfx_path,
            music_path=music_path,
            music_volume=music_volume,
            use_footage=not no_paid_visuals,
        )
        size_mb = os.path.getsize(video_path) / (1024 * 1024)
        print(f"  Video saved: {video_path} ({size_mb:.1f} MB, {duration:.1f}s)")
        return video_path

    # --- Step 2: Scene plan (optional — requires llm) ---
    visual_frames = []
    if llm is not None and channel_config is not None:
        from src.video.scene_planner import plan_scenes
        print(f"  Planning scenes (style={style})...")
        scene_plan = plan_scenes(
            script_path, channel_config, llm,
            dry_run=dry_run, style=style,
        )
        visual_frames = _build_visual_frames(
            scene_plan, word_timings, channel_key,
            no_paid_visuals=no_paid_visuals,
            script_path=script_path,
            style=style,
        )

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

    # --- Step 3.5: Hook sound effect and background music (optional) ---
    sfx_path, music_path, music_volume = _prepare_extra_audio(
        channel_config, channel_key, duration, use_music
    )

    # --- Step 4: FFmpeg encode ---
    # Build the FFmpeg command based on which audio tracks are available.
    # Inputs are always: 0=frames, 1=voiceover, then optionally sfx and music.
    # We use amix to blend all audio inputs, with weights controlling volume.
    command = _build_ffmpeg_command(
        concat_path=concat_path,
        audio_path=audio_path,
        video_path=video_path,
        duration=duration,
        sfx_path=sfx_path,
        music_path=music_path,
        music_volume=music_volume,
    )

    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"FFmpeg failed (exit code {result.returncode}).\n"
            f"Stderr:\n{result.stderr[-2000:]}"
        )

    size_mb = os.path.getsize(video_path) / (1024 * 1024)
    print(f"  Video saved: {video_path} ({size_mb:.1f} MB, {duration:.1f}s)")
    return video_path

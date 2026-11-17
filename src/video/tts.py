# =============================================================
# src/video/tts.py — convert script text to a voiceover audio file
#
# Uses edge-tts, which connects to Microsoft Edge's free neural
# TTS engine. No API key is needed. The voice sounds natural
# enough for news content and the package also returns exact
# word-level timestamps, which we use to build captions.
#
# The finished audio is saved as an .mp3 file and cached so the
# same script never pays (time-wise) for a second TTS conversion.
#
# How it fits in:
#   main.py → render.py → tts.py  (get_voiceover)
#                       → captions.py (build_subtitle_file)
# =============================================================

import asyncio
import hashlib
import json
import os

import edge_tts

from src.generation.sections import is_spoken_section


# Where the generated audio files are cached.
AUDIO_CACHE_FOLDER = os.path.join("cache", "audio")

# Which script sections are read aloud is decided in one place
# (src/generation/sections.py), so the voiceover, the captions and the
# animation storyboard always agree on exactly which words are spoken.


def _extract_spoken_text(script_path):
    """
    Read a script file and return only the lines that will be spoken aloud.

    The script is a Markdown file with sections like "## Hook" and
    "## What happened". They are all read aloud except the reviewer-only
    ones ("## Notes for reviewer", "## Quality check", "## Sources").

    Args:
        script_path (str): Path to the .md script file.

    Returns:
        str: The spoken text, with section headings removed.

    Example:
        text = _extract_spoken_text("output/news/2026-11-07-big-story.md")
        # Returns the Hook, Story, Why it matters, What happens next,
        # and Closing line paragraphs joined together.
    """
    spoken_lines = []
    inside_spoken_section = False

    with open(script_path, "r", encoding="utf-8") as f:
        for line in f:
            stripped = line.rstrip()

            # Lines starting with ## are section headings.
            if stripped.startswith("## "):
                section_name = stripped[3:].strip()
                inside_spoken_section = is_spoken_section(section_name)
                continue  # Don't include the heading itself

            # Skip separator lines and empty lines at the top.
            if stripped in ("---", ""):
                if inside_spoken_section:
                    # Keep blank lines between paragraphs so the TTS
                    # pauses slightly between sections.
                    spoken_lines.append("")
                continue

            if inside_spoken_section:
                spoken_lines.append(stripped)

    return "\n".join(spoken_lines).strip()


def spoken_section_word_counts(script_path):
    """
    Count the spoken words in each section of a script, in order.

    The pacing step uses this to know where the hook ends and where
    each new section starts, so it can place the right pauses there.

    Args:
        script_path (str): Path to the .md script file.

    Returns:
        list[int]: Words per spoken section, e.g. [14, 52, 30, 21].
    """
    counts = []
    current = None
    with open(script_path, "r", encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            if stripped.startswith("## "):
                if current is not None:
                    counts.append(current)
                current = 0 if is_spoken_section(stripped[3:]) else None
                continue
            if current is not None and stripped and stripped != "---":
                current += len(stripped.split())
    if current is not None:
        counts.append(current)
    return counts


def _audio_cache_path(script_path, voice, extension=".mp3"):
    """
    Work out where to cache the audio for a given script + voice combo.

    We hash the script file's contents (not its path) so if the script
    changes the cache is automatically invalidated.

    Args:
        script_path (str): Path to the script file.
        voice (str): The TTS voice name (edge-tts or Kokoro).
        extension (str): File extension — ".mp3" for edge-tts, ".wav" for Kokoro.

    Returns:
        tuple[str, str]: (audio file path, timing .json path)
    """
    with open(script_path, "r", encoding="utf-8") as f:
        content = f.read()

    fingerprint = hashlib.sha256(
        (content + voice).encode("utf-8")
    ).hexdigest()[:24]

    audio_path = os.path.join(AUDIO_CACHE_FOLDER, f"{fingerprint}{extension}")
    timing_path = os.path.join(AUDIO_CACHE_FOLDER, f"{fingerprint}_timing.json")
    return audio_path, timing_path


def match_words_to_boundaries(text, boundaries):
    """
    Give every word of the script a start and end time.

    edge-tts reports one timing event per spoken word, but without the
    punctuation ("traffic" instead of "traffic."). The rest of the program
    needs the words exactly as written (one per space-separated word, as
    text.split() gives them), so we walk through both lists side by side.

    Args:
        text (str): The spoken text that was sent to edge-tts.
        boundaries (list[dict]): One {"start", "end"} per spoken word, in order.

    Returns:
        list[dict]: One {"word", "start", "end"} per word of text.split().
                    A token that is only punctuation (like "—") is given
                    zero length at the previous word's end.

    Example:
        match_words_to_boundaries("Hi there.", [{"start": 0, "end": 0.2},
                                                {"start": 0.3, "end": 0.6}])
        # → [{"word": "Hi", ...}, {"word": "there.", "start": 0.3, "end": 0.6}]
    """
    timings = []
    next_event = 0
    last_end = 0.0
    for word in text.split():
        has_letters_or_digits = any(character.isalnum() for character in word)
        if has_letters_or_digits and next_event < len(boundaries):
            event = boundaries[next_event]
            next_event += 1
            timings.append({"word": word, "start": event["start"], "end": event["end"]})
            last_end = event["end"]
        else:
            timings.append({"word": word, "start": last_end, "end": last_end})
    return timings


async def _synthesize(text, voice, audio_path, timing_path):
    """
    Call the edge-tts API to generate audio and word timing.

    This is an async function because edge-tts uses Python's asyncio
    networking internally. (Async just means it can wait for the
    network without freezing — you don't need to understand it deeply.)

    We ask for "WordBoundary" events: one exact timing per spoken word,
    so captions and natural pacing line up with the voice.

    Args:
        text (str): The spoken text to convert.
        voice (str): The edge-tts voice name, e.g. "en-US-AriaNeural".
        audio_path (str): Where to save the .mp3 file.
        timing_path (str): Where to save the word-timing .json file.
    """
    communicate = edge_tts.Communicate(text, voice, boundary="WordBoundary")

    os.makedirs(os.path.dirname(audio_path), exist_ok=True)

    boundaries = []

    # communicate.stream() yields audio chunks and "WordBoundary" events.
    # We collect the audio into a file and the word events into a list.
    with open(audio_path, "wb") as audio_file:
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio_file.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                # offset and duration are in 100-nanosecond ticks.
                start_seconds = chunk["offset"] / 10_000_000
                duration_seconds = chunk["duration"] / 10_000_000
                boundaries.append({
                    "start": round(start_seconds, 3),
                    "end": round(start_seconds + duration_seconds, 3),
                })

    word_timings = match_words_to_boundaries(text, boundaries)

    with open(timing_path, "w", encoding="utf-8") as f:
        json.dump(word_timings, f, indent=2)


def get_voiceover(script_path, voice, tts_provider="edge", dry_run=False,
                  cost_tracker=None, voice_settings=None):
    """
    Generate (or load from cache) a voiceover audio file and word-timing data.

    Supported TTS providers:
      - "edge"       — Microsoft Edge neural TTS (free, online, .mp3)
      - "macos"      — Apple neural voices (free, local, .wav)
      - "kokoro"     — Kokoro ONNX (free, local, near-ElevenLabs quality, .wav)
      - "elevenlabs" — ElevenLabs API (paid, best quality, .mp3)

    Args:
        script_path (str): Path to the .md script file.
        voice (str): Voice name for the chosen provider.
                     edge:        e.g. "en-US-AriaNeural"
                     macos:       e.g. "Flo (English (US))"
                     kokoro:      e.g. "af_heart", "am_michael", "bf_emma"
                     elevenlabs:  e.g. "Rachel", "Josh", or a voice ID
        tts_provider (str): "edge", "macos", "kokoro", or "elevenlabs".
                            Defaults to "edge".
        dry_run (bool): If True, skip the actual TTS call and return
                        placeholder paths.
        cost_tracker: Optional database tracker for a paid voice call.
        voice_settings (dict or None): Per-channel model and character price.

    Returns:
        tuple[str, list[dict]]:
            - audio_path: path to the audio file (.mp3 for edge, .wav for kokoro)
            - word_timings: list of {"word", "start", "end"} dicts

    Example:
        audio, timings = get_voiceover(
            "output/news/2026-11-07-big.md", "af_heart", tts_provider="kokoro"
        )
        print(timings[0])  # {"word": "A", "start": 0.1, "end": 0.4}
    """
    # kokoro and macos produce WAV; edge-tts and elevenlabs produce MP3.
    extension = ".wav" if tts_provider in ("kokoro", "macos") else ".mp3"
    voice_settings = voice_settings or {}
    # Preserve the existing default-model cache so this upgrade does not pay
    # to regenerate old voiceovers. Other models receive a distinct cache key.
    cache_voice = voice
    model = voice_settings.get("elevenlabs_model", "eleven_turbo_v2_5")
    if tts_provider == "elevenlabs" and model != "eleven_turbo_v2_5":
        cache_voice = f"{tts_provider}:{voice}:{model}"
    # Different voice tuning (energy, speed) sounds different, so it gets
    # its own cache entry instead of reusing an old recording.
    tuning = voice_settings.get("elevenlabs_voice_settings")
    if tts_provider == "elevenlabs" and tuning:
        cache_voice += ":" + json.dumps(tuning, sort_keys=True)
    audio_path, timing_path = _audio_cache_path(script_path, cache_voice, extension)

    # Already cached? Use the saved files.
    if os.path.exists(audio_path) and os.path.exists(timing_path):
        print("  Voiceover loaded from cache.")
        with open(timing_path, "r", encoding="utf-8") as f:
            word_timings = json.load(f)
        return _paced(script_path, audio_path, word_timings, voice_settings)

    if dry_run:
        print("  [DRY RUN] Skipping TTS — no audio generated.")
        return audio_path, []

    text = _extract_spoken_text(script_path)
    if not text:
        raise ValueError(f"No spoken text found in {script_path}. "
                         "Check that the script has the expected ## sections.")

    if tts_provider == "macos":
        # macOS built-in neural voice (Flo, Reed, Shelley, etc.) — high quality,
        # fully local, no API key needed.
        from src.video.tts_macos import synthesize_macos
        rate = 155  # words per minute — slightly slower for a compassionate tone
        synthesize_macos(text, voice, audio_path, timing_path, rate=rate)

    elif tts_provider == "kokoro":
        # Kokoro ONNX — near-ElevenLabs quality; requires onnxruntime.
        from src.video.tts_kokoro import synthesize_kokoro
        synthesize_kokoro(text, voice, audio_path, timing_path)

    elif tts_provider == "elevenlabs":
        # ElevenLabs — best quality, paid API; requires ELEVENLABS_API_KEY in .env.
        from src.video.tts_elevenlabs import synthesize_elevenlabs
        synthesize_elevenlabs(text, voice, audio_path, timing_path,
                             cost_tracker=cost_tracker, settings=voice_settings)

    else:
        # edge-tts — free Microsoft Edge neural TTS (online).
        print(f"  Generating voiceover with {voice} "
              f"({len(text.split())} words)...")
        # asyncio.run() starts the async function and waits for it to finish.
        asyncio.run(_synthesize(text, voice, audio_path, timing_path))

    with open(timing_path, "r", encoding="utf-8") as f:
        word_timings = json.load(f)

    print(f"  Voiceover ready: {len(word_timings)} words timed.")
    return _paced(script_path, audio_path, word_timings, voice_settings)


def _paced(script_path, audio_path, word_timings, voice_settings):
    """
    Give the voiceover natural pauses (see pacing.py), if the channel wants them.

    Args:
        script_path (str): The script (to find where each section starts).
        audio_path (str): The raw voiceover.
        word_timings (list[dict]): Its word timings.
        voice_settings (dict): The channel's "video" block.

    Returns:
        tuple: (audio path, word timings), paced when pacing is enabled.
    """
    # Only channels that add a "pacing" block get reshaped pauses, so old
    # channels keep exactly the voiceover they had before.
    if not voice_settings.get("pacing"):
        return audio_path, word_timings
    from src.video.pacing import apply_pacing
    counts = spoken_section_word_counts(script_path)
    return apply_pacing(audio_path, word_timings, counts, voice_settings)

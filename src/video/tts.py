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


# Where the generated audio files are cached.
AUDIO_CACHE_FOLDER = os.path.join("cache", "audio")


def _extract_spoken_text(script_path):
    """
    Read a script file and return only the lines that will be spoken aloud.

    The script is a Markdown file with sections like "## Hook", "## Story",
    etc. Only those sections are read on camera. We skip "## Notes for
    reviewer" and "## Sources" because those are not part of the video.

    Args:
        script_path (str): Path to the .md script file.

    Returns:
        str: The spoken text, with section headings removed.

    Example:
        text = _extract_spoken_text("output/news/2026-11-07-big-story.md")
        # Returns the Hook, Story, Why it matters, What happens next,
        # and Closing line paragraphs joined together.
    """
    # Sections that get spoken aloud in the video.
    spoken_sections = {"Hook", "Story", "Why it matters",
                       "What happens next", "Closing line"}

    spoken_lines = []
    inside_spoken_section = False

    with open(script_path, "r", encoding="utf-8") as f:
        for line in f:
            stripped = line.rstrip()

            # Lines starting with ## are section headings.
            if stripped.startswith("## "):
                section_name = stripped[3:].strip()
                inside_spoken_section = section_name in spoken_sections
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


def _distribute_word_timing(sentence_boundaries):
    """
    Turn sentence-level timing into word-level timing.

    edge-tts 7.x gives us start and duration for each whole sentence.
    We split each sentence into words and divide the time evenly across
    them. This isn't frame-perfect, but it's accurate enough for
    3-words-at-a-time captions.

    Args:
        sentence_boundaries (list[dict]): Each dict has "text", "start",
                                          "end" keys (all in seconds).

    Returns:
        list[dict]: One dict per word: {"word", "start", "end"}.
    """
    word_timings = []

    for sentence in sentence_boundaries:
        words = sentence["text"].split()
        if not words:
            continue

        sentence_duration = sentence["end"] - sentence["start"]
        time_per_word = sentence_duration / len(words)

        for i, word in enumerate(words):
            start = sentence["start"] + i * time_per_word
            end = start + time_per_word
            word_timings.append({
                "word": word,
                "start": round(start, 3),
                "end": round(end, 3),
            })

    return word_timings


async def _synthesize(text, voice, audio_path, timing_path):
    """
    Call the edge-tts API to generate audio and word timing.

    This is an async function because edge-tts uses Python's asyncio
    networking internally. (Async just means it can wait for the
    network without freezing — you don't need to understand it deeply.)

    edge-tts 7.x emits SentenceBoundary events (not WordBoundary).
    We use _distribute_word_timing to split each sentence into
    evenly-spaced word timestamps.

    Args:
        text (str): The spoken text to convert.
        voice (str): The edge-tts voice name, e.g. "en-US-AriaNeural".
        audio_path (str): Where to save the .mp3 file.
        timing_path (str): Where to save the word-timing .json file.
    """
    communicate = edge_tts.Communicate(text, voice)

    os.makedirs(os.path.dirname(audio_path), exist_ok=True)

    sentence_boundaries = []

    # communicate.stream() yields audio chunks and "SentenceBoundary" events.
    # We collect the audio into a file and the sentence events into a list.
    with open(audio_path, "wb") as audio_file:
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio_file.write(chunk["data"])
            elif chunk["type"] == "SentenceBoundary":
                # offset and duration are in 100-nanosecond ticks.
                start_seconds = chunk["offset"] / 10_000_000
                duration_seconds = chunk["duration"] / 10_000_000
                sentence_boundaries.append({
                    "text": chunk["text"],
                    "start": round(start_seconds, 3),
                    "end": round(start_seconds + duration_seconds, 3),
                })

    word_boundaries = _distribute_word_timing(sentence_boundaries)

    with open(timing_path, "w", encoding="utf-8") as f:
        json.dump(word_boundaries, f, indent=2)


def get_voiceover(script_path, voice, tts_provider="edge", dry_run=False):
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
    audio_path, timing_path = _audio_cache_path(script_path, voice, extension)

    # Already cached? Use the saved files.
    if os.path.exists(audio_path) and os.path.exists(timing_path):
        print("  Voiceover loaded from cache.")
        with open(timing_path, "r", encoding="utf-8") as f:
            word_timings = json.load(f)
        return audio_path, word_timings

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
        synthesize_elevenlabs(text, voice, audio_path, timing_path)

    else:
        # edge-tts — free Microsoft Edge neural TTS (online).
        print(f"  Generating voiceover with {voice} "
              f"({len(text.split())} words)...")
        # asyncio.run() starts the async function and waits for it to finish.
        asyncio.run(_synthesize(text, voice, audio_path, timing_path))

    with open(timing_path, "r", encoding="utf-8") as f:
        word_timings = json.load(f)

    print(f"  Voiceover ready: {len(word_timings)} words timed.")
    return audio_path, word_timings

# =============================================================
# src/video/tts_elevenlabs.py — ElevenLabs text-to-speech provider
#
# ElevenLabs (elevenlabs.io) produces the most natural-sounding AI
# voices available. This module uses their REST API directly — no
# extra Python package needed.
#
# The "with-timestamps" endpoint returns audio + character-level
# timing in one call. We convert that to word-level timing so the
# rest of the pipeline (captions, frames) doesn't need to change.
#
# Cost (as of 2026): ~$0.30 per 1,000 characters with eleven_turbo_v2_5.
# A typical 75-second news script is ~600 characters → ~$0.18 per video.
# Monthly Starter plan ($5/mo) includes 30,000 characters → ~50 videos/mo.
#
# Setup:
#   1. Sign up at elevenlabs.io and copy your API key.
#   2. Add ELEVENLABS_API_KEY=your-key to .env.
#   3. Set tts_provider = "elevenlabs" and elevenlabs_voice = "Rachel"
#      (or any voice name from your ElevenLabs account) in the channel YAML.
#
# How it fits in:
#   tts.py → get_voiceover() → synthesize_elevenlabs() (here)
# =============================================================

import base64
import json
import os
import urllib.request


ELEVENLABS_API = "https://api.elevenlabs.io/v1"

# eleven_turbo_v2_5 is ElevenLabs' cheapest model with good quality.
# It supports the with-timestamps endpoint and has low latency.
MODEL_ID = "eleven_turbo_v2_5"


def _get_voice_id(voice_name_or_id):
    """
    Return the ElevenLabs voice ID for a given name or ID.

    ElevenLabs voice IDs are long alphanumeric strings (e.g. "21m00Tcm4TlvDq8ikWAM").
    You can also pass a friendly name like "Rachel" and we will look it up.

    Args:
        voice_name_or_id (str): A voice name ("Rachel") or voice ID.

    Returns:
        str: The voice ID to use in API calls.

    Example:
        voice_id = _get_voice_id("Rachel")
        # → "21m00Tcm4TlvDq8ikWAM"
    """
    api_key = os.getenv("ELEVENLABS_API_KEY", "")

    # If it looks like an ID (20+ alphanumeric characters), use it directly.
    # This avoids an extra API call when the user has already set a voice ID.
    if len(voice_name_or_id) >= 20 and voice_name_or_id.replace("-", "").isalnum():
        return voice_name_or_id

    # Otherwise, fetch all available voices and find one matching the name.
    req = urllib.request.Request(
        f"{ELEVENLABS_API}/voices",
        headers={"xi-api-key": api_key},
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
    except Exception as err:
        raise RuntimeError(f"ElevenLabs: could not fetch voices list: {err}")

    for voice in data.get("voices", []):
        if voice["name"].lower() == voice_name_or_id.lower():
            return voice["voice_id"]

    # Not found by name — treat the input as a voice ID and hope for the best.
    return voice_name_or_id


def _characters_to_words(characters, start_times, end_times):
    """
    Convert ElevenLabs character-level timing into word-level timing.

    ElevenLabs returns one start/end time per character. We group
    consecutive non-space characters into words and use the first
    character's start and last character's end as the word boundaries.

    Args:
        characters (list[str]): Each character in the spoken text.
        start_times (list[float]): Start time in seconds for each character.
        end_times (list[float]): End time in seconds for each character.

    Returns:
        list[dict]: One dict per word: {"word": str, "start": float, "end": float}.

    Example:
        words = _characters_to_words(["H","i"," ","t","h","e","r","e"],
                                     [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7],
                                     [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8])
        # → [{"word": "Hi", "start": 0.0, "end": 0.2},
        #    {"word": "there", "start": 0.3, "end": 0.8}]
    """
    words = []
    current_chars = []
    word_start = 0.0
    word_end = 0.0

    for i, char in enumerate(characters):
        if char in (" ", "\n", "\t"):
            # This character is a space — it marks the end of a word.
            if current_chars:
                words.append({
                    "word": "".join(current_chars),
                    "start": round(word_start, 3),
                    "end": round(word_end, 3),
                })
                current_chars = []
        else:
            # This is a letter or punctuation — part of a word.
            if not current_chars:
                # First character of a new word — record its start time.
                word_start = start_times[i]
            current_chars.append(char)
            word_end = end_times[i]

    # The last word may not be followed by a space — save it too.
    if current_chars:
        words.append({
            "word": "".join(current_chars),
            "start": round(word_start, 3),
            "end": round(word_end, 3),
        })

    return words


def synthesize_elevenlabs(text, voice, audio_path, timing_path, cost_tracker=None, settings=None):
    """
    Generate a voiceover MP3 and word-timing JSON using the ElevenLabs API.

    Calls the /text-to-speech/{voice_id}/with-timestamps endpoint, which
    returns both audio (base64-encoded) and character-level timing in one
    response. We decode the audio to .mp3 and convert timing to words.

    Args:
        text (str): The spoken text from the script.
        voice (str): Voice name ("Rachel") or voice ID from ElevenLabs.
        audio_path (str): Where to save the .mp3 file.
        timing_path (str): Where to save the word-timing .json file.
        cost_tracker: Optional database tracker for estimated voice spending.
        settings (dict or None): Channel model and dollars per 1,000 characters.

    Raises:
        ValueError: If ELEVENLABS_API_KEY is not set in .env.
        RuntimeError: If the API request fails.

    Example:
        synthesize_elevenlabs(
            "Hello world.",
            "Rachel",
            "cache/audio/abc123.mp3",
            "cache/audio/abc123_timing.json",
        )
    """
    api_key = os.getenv("ELEVENLABS_API_KEY", "")
    if not api_key:
        raise ValueError(
            "ELEVENLABS_API_KEY is not set. "
            "Get a free key at elevenlabs.io and add it to .env."
        )

    print(f"  Generating voiceover with ElevenLabs ({voice}, "
          f"{len(text.split())} words)...")

    voice_id = _get_voice_id(voice)

    url = f"{ELEVENLABS_API}/text-to-speech/{voice_id}/with-timestamps"

    settings = settings or {}
    model = settings.get("elevenlabs_model", MODEL_ID)
    payload = json.dumps({
        "text": text,
        "model_id": model,
        "voice_settings": {
            "stability": 0.50,          # 0=very expressive, 1=very stable
            "similarity_boost": 0.75,   # how closely to match the original voice
        },
    }).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "xi-api-key": api_key,
            "Content-Type": "application/json",
        },
    )

    event_id = None
    estimate = 0.0
    if cost_tracker:
        price = settings.get("elevenlabs_usd_per_1000_chars")
        if price is None or float(price) <= 0:
            raise ValueError("Set video.elevenlabs_usd_per_1000_chars in the channel YAML, or choose a free voice.")
        estimate = len(text) * float(price) / 1000
        event_id = cost_tracker.reserve("voiceover (character estimate)", model, estimate, len(text))
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as err:
        body = err.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"ElevenLabs API error {err.code}: {body[:500]}"
        )

    if event_id is not None:
        # Subscription allowances vary. This is a character-based estimate,
        # not a claim about the actual amount on the provider's invoice.
        cost_tracker.settle(event_id, estimate)

    # Decode the base64 audio and save it as an .mp3 file.
    audio_bytes = base64.b64decode(data["audio_base64"])
    os.makedirs(os.path.dirname(audio_path), exist_ok=True)
    with open(audio_path, "wb") as f:
        f.write(audio_bytes)

    # Convert character-level timing to word-level timing.
    alignment = data["alignment"]
    word_timings = _characters_to_words(
        alignment["characters"],
        alignment["character_start_times_seconds"],
        alignment["character_end_times_seconds"],
    )

    with open(timing_path, "w", encoding="utf-8") as f:
        json.dump(word_timings, f, indent=2)

    print(f"  ElevenLabs voiceover ready: {len(word_timings)} words timed.")

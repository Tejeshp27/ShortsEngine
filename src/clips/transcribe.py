# =============================================================
# src/clips/transcribe.py — turn a clip's speech into timed words
#
# Uses whisper.cpp, a free program that runs OpenAI's Whisper
# speech-recognition model on this computer (no API, no cost).
# Install it once with:  brew install whisper-cpp
#
# Why whisper.cpp and not a Python package: the usual Python
# packages (openai-whisper, faster-whisper) need libraries that
# do not support this project's Python 3.15 yet. whisper.cpp is a
# separate program, like FFmpeg, so it works with any Python
# version and uses the Mac's graphics chip to run fast.
#
# Steps:
#   1. FFmpeg copies the clip's sound into a 16 kHz mono WAV file
#      (the format Whisper expects).
#   2. whisper-cli writes a JSON file with one entry per word
#      (we ask for "segments" of one word each).
#   3. We turn that into [{"word", "start", "end"}, ...] — the same
#      shape the caption code already uses — and cache it, so a
#      clip is only ever transcribed once.
# =============================================================

import json
import os
import shutil
import subprocess

import httpx

# Downloaded Whisper models live here (git-ignored, re-downloadable).
WHISPER_MODEL_FOLDER = os.path.join("models", "whisper")
# The official whisper.cpp model files, hosted by the whisper.cpp author.
WHISPER_MODEL_URL = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-{name}.bin"
TRANSCRIPT_CACHE_FOLDER = os.path.join("cache", "clips", "transcripts")

# Whisper was trained on 16,000 samples per second, one channel.
WHISPER_SAMPLE_RATE = 16000


class TranscriptionError(Exception):
    """Raised with a plain message when speech-to-text cannot run."""


def whisper_program():
    """
    Find the whisper.cpp program.

    Returns:
        str: Its path.

    Raises:
        TranscriptionError: If it is not installed.
    """
    path = shutil.which("whisper-cli")
    if path is None:
        raise TranscriptionError("whisper.cpp is not installed. Run: brew install whisper-cpp")
    return path


def model_path(model_name):
    """
    Get the local Whisper model file, downloading it the first time.

    Args:
        model_name (str): For example "small.en" or "base.en".

    Returns:
        str: The path of the .bin model file.
    """
    path = os.path.join(WHISPER_MODEL_FOLDER, f"ggml-{model_name}.bin")
    if os.path.exists(path):
        return path

    os.makedirs(WHISPER_MODEL_FOLDER, exist_ok=True)
    url = WHISPER_MODEL_URL.format(name=model_name)
    print(f"  Downloading the Whisper model '{model_name}' once (free, a few hundred MB)...")
    partial_path = path + ".part"
    with httpx.stream("GET", url, follow_redirects=True, timeout=600) as response:
        if response.status_code != 200:
            raise TranscriptionError(f"Could not download Whisper model '{model_name}' "
                                     f"(HTTP {response.status_code}). Check the name in the channel YAML.")
        with open(partial_path, "wb") as file:
            for piece in response.iter_bytes():
                file.write(piece)
    os.replace(partial_path, path)
    return path


def extract_audio(video_path, wav_path):
    """
    Copy a video's sound into a WAV file Whisper can read.

    Args:
        video_path (str): The clip.
        wav_path (str): Where to write the WAV file.
    """
    subprocess.run([
        "ffmpeg", "-nostdin", "-v", "error", "-y",
        "-i", video_path,
        "-vn",                              # no video, sound only
        "-ac", "1",                         # one channel (mono)
        "-ar", str(WHISPER_SAMPLE_RATE),    # 16,000 samples per second
        "-c:a", "pcm_s16le",                # plain uncompressed 16-bit audio
        wav_path,
    ], check=True)


def clean_word(text):
    """
    Tidy one word from Whisper's output.

    Whisper puts a space before most words and sometimes outputs
    sound labels like "[MUSIC]" or "(laughs)". Those are not speech,
    so they are dropped.

    Args:
        text (str): The raw text of one segment.

    Returns:
        str: The cleaned word, or "" if it should be dropped.

    Example:
        clean_word(" Hello")   # → "Hello"
        clean_word("[MUSIC]")  # → ""
    """
    word = text.strip()
    if not word:
        return ""
    if word.startswith("[") or word.startswith("("):
        return ""
    return word


def is_only_punctuation(text):
    """
    Check whether a piece of text has no letters or digits at all.

    Args:
        text (str): For example "!" or "wow".

    Returns:
        bool: True for text like "!" or "...", False for "wow".
    """
    for character in text:
        if character.isalnum():
            return False
    return True


def words_from_whisper_json(data):
    """
    Turn whisper.cpp's JSON output into a list of timed words.

    Args:
        data (dict): The parsed JSON file. Its "transcription" list has
                     one entry per word, each with "text" and "offsets"
                     {"from": ms, "to": ms}.

    Returns:
        list[dict]: [{"word": "Hello", "start": 0.32, "end": 0.61}, ...]
                    with times in seconds.
    """
    words = []
    for segment in data.get("transcription", []):
        word = clean_word(segment.get("text", ""))
        if not word:
            continue
        offsets = segment.get("offsets", {})
        start = offsets.get("from", 0) / 1000
        end = offsets.get("to", 0) / 1000

        # A punctuation mark that arrives on its own (e.g. "!") belongs
        # to the word before it.
        if words and is_only_punctuation(word):
            words[-1]["word"] += word
            words[-1]["end"] = max(words[-1]["end"], end)
            continue
        words.append({"word": word, "start": round(start, 2), "end": round(max(end, start), 2)})
    return words


def transcribe_clip(video_path, cache_name, model_name="small.en", language="en"):
    """
    Get the timed words spoken in a clip (cached after the first time).

    Args:
        video_path (str): The clip's video file.
        cache_name (str): A unique name for the cached transcript, e.g. the clip ID.
        model_name (str): Which Whisper model to use.
        language (str): The spoken language, e.g. "en".

    Returns:
        list[dict]: [{"word", "start", "end"}, ...] — empty if nobody speaks.

    Raises:
        TranscriptionError: If whisper.cpp is missing or fails.
    """
    os.makedirs(TRANSCRIPT_CACHE_FOLDER, exist_ok=True)
    cache_path = os.path.join(TRANSCRIPT_CACHE_FOLDER, f"{cache_name}.json")
    if os.path.exists(cache_path):
        with open(cache_path, "r", encoding="utf-8") as file:
            return json.load(file)

    program = whisper_program()
    model = model_path(model_name)
    wav_path = os.path.join(TRANSCRIPT_CACHE_FOLDER, f"{cache_name}.wav")
    output_base = os.path.join(TRANSCRIPT_CACHE_FOLDER, f"{cache_name}_whisper")
    extract_audio(video_path, wav_path)

    result = subprocess.run([
        program,
        "-m", model,
        "-f", wav_path,
        "-l", language,
        "-ml", "1",        # "max length 1": one word per segment = word timings
        "-sow",            # split on whole words, not word pieces
        "-oj",             # write the result as JSON
        "-of", output_base,
        "-np",             # don't print progress
    ], capture_output=True, text=True)
    os.remove(wav_path)
    if result.returncode != 0:
        raise TranscriptionError(f"whisper.cpp failed: {result.stderr.strip()[-300:]}")

    with open(output_base + ".json", "r", encoding="utf-8") as file:
        words = words_from_whisper_json(json.load(file))
    os.remove(output_base + ".json")

    with open(cache_path, "w", encoding="utf-8") as file:
        json.dump(words, file)
    return words

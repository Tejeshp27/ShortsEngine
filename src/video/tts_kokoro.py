# =============================================================
# src/video/tts_kokoro.py — high-quality local text-to-speech via Kokoro
#
# Kokoro is a free, open-source neural TTS model that runs entirely
# on your machine. It produces voices that sound very close to
# ElevenLabs quality — no API key, no subscription needed.
#
# Two steps happen here:
#   1. Kokoro converts the script text into a WAV audio file.
#   2. faster-whisper (a fast version of OpenAI's Whisper model)
#      transcribes the audio back to text with word-level timestamps.
#      Those timestamps drive the caption sync in the video.
#
# First run: downloads ~320 MB of model files into models/.
# After that: everything runs locally from cache.
#
# How it fits in:
#   tts.py → synthesize_kokoro()  (called when tts_provider = "kokoro")
# =============================================================

import json
import os
import sys
import urllib.request
import wave

import numpy as np


# Where the Kokoro model files live on disk.
MODELS_FOLDER = "models"
MODEL_FILE = "kokoro-v1.0.onnx"
VOICES_FILE = "voices-v1.0.bin"

# Download URLs for the model files (GitHub releases).
MODEL_DOWNLOAD_URL = (
    "https://github.com/thewh1teagle/kokoro-onnx/releases/download/"
    "model-files-v1.0/kokoro-v1.0.onnx"
)
VOICES_DOWNLOAD_URL = (
    "https://github.com/thewh1teagle/kokoro-onnx/releases/download/"
    "model-files-v1.0/voices-v1.0.bin"
)


def _download_with_progress(url, dest_path, label):
    """
    Download a file from a URL and show a simple progress indicator.

    Args:
        url (str): The URL to download from.
        dest_path (str): Where to save the file.
        label (str): Short description shown to the user, e.g. "Kokoro model".
    """
    print(f"  Downloading {label} — this only happens once...")

    def _show_progress(block_count, block_size, total_size):
        downloaded = block_count * block_size
        if total_size > 0:
            pct = min(100, downloaded * 100 // total_size)
            # Overwrite the same line each time (\r) so it looks like a progress bar.
            sys.stdout.write(f"\r  {label}: {pct}%   ")
            sys.stdout.flush()

    urllib.request.urlretrieve(url, dest_path, reporthook=_show_progress)
    print()  # Move to a new line after the progress bar finishes.


def _ensure_models():
    """
    Make sure the Kokoro model files are present, downloading them if needed.

    Returns:
        tuple[str, str]: (model_path, voices_path) on disk.

    Raises:
        RuntimeError: If the download fails and the files are still missing.
    """
    os.makedirs(MODELS_FOLDER, exist_ok=True)

    model_path = os.path.join(MODELS_FOLDER, MODEL_FILE)
    voices_path = os.path.join(MODELS_FOLDER, VOICES_FILE)

    if not os.path.exists(model_path):
        try:
            _download_with_progress(MODEL_DOWNLOAD_URL, model_path,
                                    "Kokoro ONNX model (~310 MB)")
        except Exception as err:
            raise RuntimeError(
                f"Could not download Kokoro model: {err}\n"
                f"Try downloading manually from:\n  {MODEL_DOWNLOAD_URL}\n"
                f"and saving it to: {model_path}"
            )

    if not os.path.exists(voices_path):
        try:
            _download_with_progress(VOICES_DOWNLOAD_URL, voices_path,
                                    "Kokoro voices (~10 MB)")
        except Exception as err:
            raise RuntimeError(
                f"Could not download Kokoro voices: {err}\n"
                f"Try downloading manually from:\n  {VOICES_DOWNLOAD_URL}\n"
                f"and saving it to: {voices_path}"
            )

    return model_path, voices_path


def _save_wav(audio_path, samples, sample_rate):
    """
    Save a numpy array of float32 audio samples as a 16-bit mono WAV file.

    We use Python's built-in wave module so no extra libraries are needed.

    Args:
        audio_path (str): Where to write the .wav file.
        samples (numpy.ndarray): Float32 audio samples in the range [-1.0, 1.0].
        sample_rate (int): Audio sample rate in Hz, e.g. 24000.
    """
    # Clip values to [-1, 1] to prevent distortion, then scale to 16-bit integers.
    clipped = np.clip(samples, -1.0, 1.0)
    samples_int16 = (clipped * 32767).astype(np.int16)

    with wave.open(audio_path, "wb") as wf:
        wf.setnchannels(1)    # mono (one channel)
        wf.setsampwidth(2)    # 2 bytes = 16-bit audio
        wf.setframerate(sample_rate)
        wf.writeframes(samples_int16.tobytes())


def _get_word_timings_whisper(audio_path):
    """
    Use faster-whisper to get word-level timestamps from an audio file.

    faster-whisper runs OpenAI's Whisper model locally. We use the tiny
    English model (~75 MB) — it's fast and accurate enough for caption timing.

    Args:
        audio_path (str): Path to the .wav file to transcribe.

    Returns:
        list[dict]: One dict per word: {"word", "start", "end"} (times in seconds).
    """
    from faster_whisper import WhisperModel

    # int8 quantization makes the model faster and uses less RAM on CPU.
    # "tiny.en" is English-only — faster than the multilingual "tiny" model.
    print("  Extracting word timing with Whisper (tiny model)...")
    model = WhisperModel("tiny.en", device="cpu", compute_type="int8")

    # word_timestamps=True makes Whisper return per-word start/end times.
    segments, _ = model.transcribe(audio_path, word_timestamps=True)

    word_timings = []
    for segment in segments:
        if segment.words is None:
            continue
        for word in segment.words:
            cleaned = word.word.strip()
            if cleaned:
                word_timings.append({
                    "word": cleaned,
                    "start": round(word.start, 3),
                    "end": round(word.end, 3),
                })

    return word_timings


def synthesize_kokoro(text, voice, audio_path, timing_path):
    """
    Generate a WAV voiceover file with Kokoro and extract word-timing with Whisper.

    This replaces the edge-tts synthesis step when tts_provider = "kokoro".

    Args:
        text (str): The spoken script text.
        voice (str): Kokoro voice name, e.g. "af_heart". Options:
                     American female: af_heart, af_bella, af_sarah, af_nicole
                     American male:   am_adam, am_michael
                     British female:  bf_emma, bf_isabella
                     British male:    bm_george, bm_lewis
        audio_path (str): Where to save the .wav file. Must end in .wav.
        timing_path (str): Where to save the word-timing .json file.

    Returns:
        tuple[str, list[dict]]: (audio_path, word_timings)
    """
    try:
        from kokoro_onnx import Kokoro
    except ImportError:
        raise RuntimeError(
            "kokoro-onnx is not installed.\n"
            "Run: pip install kokoro-onnx faster-whisper"
        )

    model_path, voices_path = _ensure_models()

    print(f"  Synthesising with Kokoro ({voice})...")
    kokoro = Kokoro(model_path, voices_path)

    # speed=0.95 — very slightly slower than default gives a warmer, more
    # human feel for news content. 1.0 is the neutral default.
    samples, sample_rate = kokoro.create(text, voice=voice, speed=0.95, lang="en-us")

    os.makedirs(os.path.dirname(audio_path) or ".", exist_ok=True)
    _save_wav(audio_path, samples, sample_rate)

    print(f"  Audio saved: {os.path.basename(audio_path)}")

    word_timings = _get_word_timings_whisper(audio_path)

    with open(timing_path, "w", encoding="utf-8") as f:
        json.dump(word_timings, f, indent=2)

    print(f"  Timing saved: {len(word_timings)} words timed.")
    return audio_path, word_timings

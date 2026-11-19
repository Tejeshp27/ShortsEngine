# =============================================================
# src/clips/transcribe.py — turn a clip's speech into timed words
#
# Two free, local ways to run OpenAI's Whisper speech-recognition
# model on this computer (no API, no cost):
#
#   whisper.cpp (preferred) — a separate program, like FFmpeg.
#     Install: brew install whisper-cpp
#   faster-whisper (fallback) — a Python package.
#     Install: pip install faster-whisper av
#
# Why whisper.cpp first: on a real shouted stream clip (2026-11-18,
# same 45-second window, same small.en model) faster-whisper returned
# 37 words and skipped most of the shouting; whisper.cpp kept it.
# Its word times are looser, which profanity.bleep_spans() allows for.
# `transcription.engine: auto` uses whisper.cpp when it is installed and
# faster-whisper otherwise; either can be chosen explicitly.
#
# Steps:
#   1. FFmpeg copies the clip's sound into a 16 kHz mono WAV file
#      (the format Whisper expects).
#   2. Whisper finds every word with its start and end time.
#   3. We turn that into [{"word", "start", "end"}, ...] — the same
#      shape the caption code already uses — and cache it, so a
#      clip is only ever transcribed once per engine/model.
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

# Loaded faster-whisper models, so several clips in one run load the
# model (a few hundred MB) only once.
_faster_whisper_models = {}


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
        offsets = segment.get("offsets", {})
        start = offsets.get("from", 0) / 1000
        end = offsets.get("to", 0) / 1000
        add_timed_word(words, segment.get("text", ""), start, end)
    return words


def faster_whisper_installed():
    """
    Check whether the faster-whisper package (and its audio reader) can be used.

    Returns:
        bool: True when `import faster_whisper` works.
    """
    try:
        import faster_whisper  # noqa: F401  (imported only to test that it exists)
    except ImportError:
        return False
    return True


def choose_engine(engine):
    """
    Decide which Whisper program to use.

    Args:
        engine (str): "auto", "faster-whisper" or "whisper.cpp" (from the YAML).

    Returns:
        str: "faster-whisper" or "whisper.cpp".

    Raises:
        TranscriptionError: If faster-whisper was asked for but is not installed.
    """
    if engine == "auto":
        if shutil.which("whisper-cli") is not None:
            return "whisper.cpp"
        if faster_whisper_installed():
            return "faster-whisper"
        return "whisper.cpp"   # whisper_program() then explains how to install it
    if engine == "faster-whisper" and not faster_whisper_installed():
        raise TranscriptionError("faster-whisper is not installed. Run: pip install faster-whisper av")
    return engine


def add_timed_word(words, text, start, end):
    """
    Add one recognised word to the list, cleaning it first.

    Args:
        words (list[dict]): The words so far (changed in place).
        text (str): The raw word from Whisper, e.g. " Hello" or "!".
        start (float): When the word starts, in seconds.
        end (float): When it ends, in seconds.
    """
    word = clean_word(text)
    if not word:
        return
    # A punctuation mark that arrives on its own (e.g. "!") belongs
    # to the word before it.
    if words and is_only_punctuation(word):
        words[-1]["word"] += word
        words[-1]["end"] = max(words[-1]["end"], round(end, 2))
        return
    words.append({"word": word, "start": round(start, 2), "end": round(max(end, start), 2)})


def read_whisper_wav(wav_path):
    """
    Read a 16 kHz mono 16-bit WAV file as numbers between -1 and 1.

    Args:
        wav_path (str): A file written by extract_audio().

    Returns:
        numpy.ndarray: One float32 number per sample.
    """
    import wave

    import numpy as np

    with wave.open(wav_path, "rb") as file:
        raw = file.readframes(file.getnframes())
    # 16-bit samples range from -32768 to 32767; dividing scales them to -1..1.
    return np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768


def words_from_faster_whisper(wav_path, model_name, language):
    """
    Transcribe a WAV file with faster-whisper, word by word.

    Args:
        wav_path (str): A 16 kHz mono WAV file.
        model_name (str): For example "small.en" (downloaded once, free).
        language (str): The spoken language, e.g. "en".

    Returns:
        list[dict]: [{"word", "start", "end"}, ...] with times in seconds.
    """
    from faster_whisper import WhisperModel

    if model_name not in _faster_whisper_models:
        # int8 = smaller numbers inside the model: faster on a normal CPU
        # with almost the same accuracy.
        _faster_whisper_models[model_name] = WhisperModel(model_name, device="cpu", compute_type="int8")
    model = _faster_whisper_models[model_name]
    # We pass the sound as numbers, not a file name: faster-whisper's own
    # file reader (PyAV) changed its options in newer versions, and our
    # WAV is already in exactly the format Whisper needs.
    audio = read_whisper_wav(wav_path)
    # word_timestamps=True asks for each word's own start/end time.
    # condition_on_previous_text=False stops a mistake in one sentence
    # from repeating in the next (common with noisy game audio).
    segments, _ = model.transcribe(audio, language=language, word_timestamps=True,
                                   condition_on_previous_text=False)
    words = []
    for segment in segments:
        if segment.words is None:
            continue
        for word in segment.words:
            add_timed_word(words, word.word, word.start, word.end)
    return words


def words_from_whisper_cpp(wav_path, output_base, model_name, language):
    """
    Transcribe a WAV file with the whisper.cpp program, word by word.

    Args:
        wav_path (str): A 16 kHz mono WAV file.
        output_base (str): Path prefix for whisper.cpp's JSON output.
        model_name (str): For example "small.en".
        language (str): The spoken language, e.g. "en".

    Returns:
        list[dict]: [{"word", "start", "end"}, ...] with times in seconds.

    Raises:
        TranscriptionError: If whisper.cpp is missing or fails.
    """
    program = whisper_program()
    model = model_path(model_name)

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
    if result.returncode != 0:
        raise TranscriptionError(f"whisper.cpp failed: {result.stderr.strip()[-300:]}")

    with open(output_base + ".json", "r", encoding="utf-8") as file:
        words = words_from_whisper_json(json.load(file))
    os.remove(output_base + ".json")
    return words


def transcript_cache_path(cache_name, model_name, language, engine):
    """
    Work out where a clip's transcript is saved in the cache folder.

    Args:
        cache_name (str): The clip's unique cache name, e.g. "excerpt_12".
        model_name (str): Which Whisper model made it, e.g. "small.en".
        language (str): The spoken language, e.g. "en".
        engine (str): The engine actually used ("whisper.cpp" or "faster-whisper").

    Returns:
        str: The JSON file path, e.g. "cache/transcripts/excerpt_12_small.en_en.json".
    """
    # A different engine/language/model must not silently reuse an older
    # transcript. whisper.cpp keeps its original file names.
    suffix = ""
    if engine == "faster-whisper":
        suffix = "_faster-whisper"
    return os.path.join(TRANSCRIPT_CACHE_FOLDER, f"{cache_name}_{model_name}_{language}{suffix}.json")


def save_transcript_cache(cache_name, words, model_name="small.en", language="en", engine="auto"):
    """
    Save already-known timed words as a clip's cached transcript.

    Used when a short excerpt is cut from a longer recording that was
    already transcribed: its words are copied instead of running Whisper again.

    Args:
        cache_name (str): The excerpt's unique cache name.
        words (list[dict]): [{"word", "start", "end"}, ...] timed from the excerpt's start.
        model_name (str): The Whisper model the words came from.
        language (str): The spoken language.
        engine (str): "auto", "faster-whisper" or "whisper.cpp", as in the channel settings.

    Returns:
        str: The saved cache file path.
    """
    os.makedirs(TRANSCRIPT_CACHE_FOLDER, exist_ok=True)
    cache_path = transcript_cache_path(cache_name, model_name, language, choose_engine(engine))
    with open(cache_path + ".part", "w", encoding="utf-8") as file:
        json.dump(words, file)
    os.replace(cache_path + ".part", cache_path)
    return cache_path


def transcribe_clip(video_path, cache_name, model_name="small.en", language="en", engine="auto"):
    """
    Get the timed words spoken in a clip (cached after the first time).

    Args:
        video_path (str): The clip's video file.
        cache_name (str): A unique name for the cached transcript, e.g. the clip ID.
        model_name (str): Which Whisper model to use.
        language (str): The spoken language, e.g. "en".
        engine (str): "auto", "faster-whisper" or "whisper.cpp".

    Returns:
        list[dict]: [{"word", "start", "end"}, ...] — empty if nobody speaks.

    Raises:
        TranscriptionError: If the chosen Whisper program is missing or fails.
    """
    os.makedirs(TRANSCRIPT_CACHE_FOLDER, exist_ok=True)
    engine = choose_engine(engine)
    cache_path = transcript_cache_path(cache_name, model_name, language, engine)
    if os.path.exists(cache_path):
        with open(cache_path, "r", encoding="utf-8") as file:
            return json.load(file)

    wav_path = os.path.join(TRANSCRIPT_CACHE_FOLDER, f"{cache_name}.wav")
    output_base = os.path.join(TRANSCRIPT_CACHE_FOLDER, f"{cache_name}_whisper")
    extract_audio(video_path, wav_path)
    try:
        if engine == "faster-whisper":
            words = words_from_faster_whisper(wav_path, model_name, language)
        else:
            words = words_from_whisper_cpp(wav_path, output_base, model_name, language)
    finally:
        os.remove(wav_path)

    with open(cache_path + ".part", "w", encoding="utf-8") as file:
        json.dump(words, file)
    os.replace(cache_path + ".part", cache_path)
    return words

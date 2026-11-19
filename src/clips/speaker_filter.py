# =============================================================
# src/clips/speaker_filter.py — captions show only the streamer's words
#
# Whisper writes down every voice it hears: the streamer, but also game
# characters, other players and music. Captions should only show what the
# streamer says, so this file recognises voices:
#
#   1. The moment's speech is cut into short pieces (about 2 seconds).
#   2. A small free voice model turns each piece into a "voice fingerprint"
#      (an embedding: a list of numbers; similar voices give similar numbers).
#   3. The streamer's voice is the main voice of the clip, checked against a
#      saved profile of that streamer built up from earlier clips (so a clip
#      where a game character talks most still finds the streamer).
#   4. Pieces whose voice is clearly not the streamer's are marked "other"
#      and left out of the captions. The sound itself is not changed, and
#      swear words from any voice are still bleeped.
#
# Library: sherpa-onnx (k2-fsa) with the WeSpeaker CAM++ English model
# (~29 MB, Apache-2.0), run on this computer for free. Chosen over the more
# common pyannote/SpeechBrain because those need PyTorch (hundreds of MB, and
# pyannote needs a Hugging Face login), while sherpa-onnx is one package that
# works on this Python version. If it is missing, captions are left unfiltered.
# =============================================================

import hashlib
import json
import os
import subprocess
from pathlib import Path

import numpy as np

MODEL_PATH = Path("models/speaker/wespeaker_en_voxceleb_CAM++.onnx")
MODEL_URL = ("https://github.com/k2-fsa/sherpa-onnx/releases/download/"
             "speaker-recongition-models/wespeaker_en_voxceleb_CAM++.onnx")
MODEL_SHA256 = "7acd352a0372de23ce49379b02864833b7f3f501b4ea25248fc977389f08e5fc"
PROFILE_FOLDER = Path("cache/clips/voices")
SAMPLE_RATE = 16000

# How speech is cut into pieces.
PIECE_MAX_SECONDS = 2.0      # a piece never runs longer than this
PIECE_GAP_SECONDS = 0.35     # a pause this long starts a new piece
PIECE_MIN_SECONDS = 1.0      # shorter pieces are measured over this much audio around them

# A clip's main voice counts as the saved streamer profile when this similar.
PROFILE_MATCH = 0.6
# Never hide more than this share of a clip's words; if the filter wants to,
# the voices are too unclear and every word is kept.
MOST_WORDS_HIDDEN = 0.5

_extractor = None   # the loaded voice model, kept so it loads only once per process


def voice_model():
    """
    Load the voice model, downloading it once if needed.

    Returns:
        sherpa_onnx.SpeakerEmbeddingExtractor or None: The model, or None when
        sherpa-onnx is not installed or the model cannot be downloaded.
    """
    global _extractor
    if _extractor is not None:
        return _extractor
    try:
        import sherpa_onnx
    except ImportError:
        return None
    if not MODEL_PATH.is_file() and not download_model():
        return None
    config = sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(MODEL_PATH), num_threads=2)
    _extractor = sherpa_onnx.SpeakerEmbeddingExtractor(config)
    return _extractor


def download_model():
    """
    Download the voice model and check it is exactly the expected file.

    Returns:
        bool: True when the model is ready to use.
    """
    import httpx

    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    partial = MODEL_PATH.with_suffix(".onnx.part")
    try:
        with httpx.stream("GET", MODEL_URL, follow_redirects=True, timeout=120) as response:
            response.raise_for_status()
            with open(partial, "wb") as file:
                for block in response.iter_bytes():
                    file.write(block)
    except httpx.HTTPError:
        partial.unlink(missing_ok=True)
        return False
    digest = hashlib.sha256(partial.read_bytes()).hexdigest()
    if digest != MODEL_SHA256:
        partial.unlink(missing_ok=True)
        return False
    os.replace(partial, MODEL_PATH)
    return True


def read_audio(source, start, end):
    """
    Read a moment's sound as 16 kHz mono numbers.

    Args:
        source (str): The video file.
        start (float): The moment's start, in seconds.
        end (float): The moment's end.

    Returns:
        numpy.ndarray: One float32 number per sample.
    """
    raw = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-ss", f"{start:.3f}", "-t", f"{end - start:.3f}",
                          "-i", source, "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "f32le", "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, dtype="<f4")


def speech_pieces(words):
    """
    Cut a moment's words into short pieces for voice checking.

    Args:
        words (list[dict]): Timed words (times from the moment's start).

    Returns:
        list[list[int]]: Each piece is a list of word positions in `words`.

    Example:
        Words spoken without a pause for 5 seconds become three pieces of about 2 seconds.
    """
    pieces = []
    current = []
    for index, word in enumerate(words):
        if current:
            first = words[current[0]]
            last = words[current[-1]]
            pause = word["start"] - last["end"] >= PIECE_GAP_SECONDS
            too_long = word["end"] - first["start"] > PIECE_MAX_SECONDS
            if pause or too_long:
                pieces.append(current)
                current = []
        current.append(index)
    if current:
        pieces.append(current)
    return pieces


def piece_fingerprint(model, audio, words, piece):
    """
    Make the voice fingerprint of one piece of speech.

    Args:
        model: From voice_model().
        audio (numpy.ndarray): The moment's sound.
        words (list[dict]): The moment's words.
        piece (list[int]): Word positions in this piece.

    Returns:
        numpy.ndarray: A unit-length fingerprint.
    """
    begin = words[piece[0]]["start"]
    finish = words[piece[-1]]["end"]
    if finish - begin < PIECE_MIN_SECONDS:
        middle = (begin + finish) / 2
        begin = middle - PIECE_MIN_SECONDS / 2
        finish = middle + PIECE_MIN_SECONDS / 2
    samples = audio[int(max(0.0, begin) * SAMPLE_RATE):int(finish * SAMPLE_RATE)]
    stream = model.create_stream()
    stream.accept_waveform(SAMPLE_RATE, samples)
    stream.input_finished()
    fingerprint = np.array(model.compute(stream))
    return fingerprint / max(1e-9, float(np.linalg.norm(fingerprint)))


def main_voice(fingerprints, durations):
    """
    Find the voice that does most of the talking in a clip.

    Starts from the average of all pieces, then keeps re-averaging only the
    pieces closest to it, so a few other voices don't pull it off course.

    Args:
        fingerprints (numpy.ndarray): One fingerprint per piece (rows).
        durations (list[float]): How long each piece is, used as its weight.

    Returns:
        numpy.ndarray: A unit-length fingerprint of the main voice.
    """
    weights = np.array(durations)
    centre = (fingerprints * weights[:, None]).sum(axis=0)
    centre = centre / np.linalg.norm(centre)
    for _ in range(3):
        similarity = fingerprints @ centre
        closest = similarity >= np.quantile(similarity, 0.4)
        centre = (fingerprints[closest] * weights[closest, None]).sum(axis=0)
        centre = centre / np.linalg.norm(centre)
    return centre


def profile_path(platform, streamer):
    """Return where a streamer's saved voice profile lives, e.g. cache/clips/voices/twitch_kaicenat.json."""
    return PROFILE_FOLDER / f"{platform}_{streamer.lower()}.json"


def load_profile(platform, streamer):
    """
    Read a streamer's saved voice profile.

    Args:
        platform (str): "twitch" or "kick".
        streamer (str): The login name.

    Returns:
        dict or None: {"voice": numpy array, "clips": number of clips it was built from}.
    """
    path = profile_path(platform, streamer)
    if not path.is_file():
        return None
    data = json.loads(path.read_text())
    return {"voice": np.array(data["voice"]), "clips": data["clips"]}


def save_profile(platform, streamer, voice, profile):
    """
    Add one clip's streamer voice to the saved profile (a running average).

    Args:
        platform (str): "twitch" or "kick".
        streamer (str): The login name.
        voice (numpy.ndarray): This clip's streamer voice fingerprint.
        profile (dict or None): The profile before this clip.

    Returns:
        Nothing.
    """
    clips = 0
    combined = voice
    if profile is not None:
        clips = profile["clips"]
        combined = profile["voice"] * clips + voice
    combined = combined / np.linalg.norm(combined)
    PROFILE_FOLDER.mkdir(parents=True, exist_ok=True)
    path = profile_path(platform, streamer)
    partial = path.with_suffix(".part")
    partial.write_text(json.dumps({"voice": [round(float(x), 6) for x in combined], "clips": clips + 1}))
    os.replace(partial, path)


def mark_speakers(words, source, start, end, platform, streamer, minimum_match):
    """
    Mark each word of a moment as said by the streamer or by another voice.

    Args:
        words (list[dict]): Timed words in source time (the whole clip's words).
        source (str): The video the moment is cut from.
        start (float): The moment's start in the source, in seconds.
        end (float): The moment's end.
        platform (str): The streamer's platform.
        streamer (str): The streamer's login name.
        minimum_match (float): Lowest voice similarity (0–1) still counted as the streamer.

    Returns:
        tuple: (new copies of the words, the moment's words marked "speaker":
                "streamer" or "other"; a short note for the quality report).
    """
    marked = []
    for word in words:
        marked.append(dict(word))
    model = voice_model()
    if model is None:
        return marked, "Voice check unavailable (sherpa-onnx or its model missing); all words captioned."
    inside = []
    for index, word in enumerate(marked):
        if word["end"] > start and word["start"] < end:
            inside.append(index)
    if len(inside) < 2:
        return marked, ""
    moment_words = []
    for index in inside:
        word = marked[index]
        moment_words.append({"start": max(0.0, word["start"] - start), "end": word["end"] - start})
    audio = read_audio(source, start, end)
    pieces = speech_pieces(moment_words)
    fingerprints = []
    durations = []
    for piece in pieces:
        fingerprints.append(piece_fingerprint(model, audio, moment_words, piece))
        durations.append(max(0.2, moment_words[piece[-1]]["end"] - moment_words[piece[0]]["start"]))
    fingerprints = np.array(fingerprints)
    voice = main_voice(fingerprints, durations)
    profile = load_profile(platform, streamer)
    if profile is not None and float(voice @ profile["voice"]) < PROFILE_MATCH:
        # Another voice dominates this clip: trust the streamer's saved profile instead.
        voice = profile["voice"]
    else:
        save_profile(platform, streamer, voice, profile)
    similarity = fingerprints @ voice
    hidden = []
    for piece, score in zip(pieces, similarity):
        if score < minimum_match:
            hidden.extend(piece)
    if len(hidden) > MOST_WORDS_HIDDEN * len(inside):
        return marked, "Voices were unclear; all words captioned."
    for position in hidden:
        marked[inside[position]]["speaker"] = "other"
    for position in range(len(inside)):
        marked[inside[position]].setdefault("speaker", "streamer")
    note = ""
    if hidden:
        note = f"{len(hidden)} word(s) from other voices left out of the captions."
    return marked, note

# =============================================================
# src/clips/audio_events.py — hear laughter, screams, cheering,
#                             music and speech in a recording
#
# Loud does not mean funny. To judge a moment we need to know WHAT the
# sound is: someone laughing is strong evidence of a joke; a scream is a
# surprise; music under speech hints that the voice is a game character
# or a video, not the streamer.
#
# Uses a small sound-recognition model (CED-tiny, trained on Google's
# AudioSet labels, Apache-2.0, ~6 MB) through sherpa-onnx, which the
# project already uses for voice recognition. It runs on the CPU: one
# hour of audio takes about 20 seconds. No paid service.
#
# Output: one entry per second with the chance (0 to 1) of each sound
# group. Cached next to the recording as <video>.audio_events.json.
# =============================================================

import hashlib
import io
import json
import os
import subprocess
import tarfile
from pathlib import Path

import numpy as np

MODEL_FOLDER = Path("models") / "audio_tagging" / "ced-tiny"
MODEL_FILE = MODEL_FOLDER / "model.int8.onnx"
LABELS_FILE = MODEL_FOLDER / "class_labels_indices.csv"
ARCHIVE_URL = ("https://github.com/k2-fsa/sherpa-onnx/releases/download/audio-tagging-models/"
               "sherpa-onnx-ced-tiny-audio-tagging-2024-04-19.tar.bz2")
ARCHIVE_SHA256 = "84baf315b57d61aa69480c4fee878dab54cbc7be3e877db334e65d8b087e23c3"
ARCHIVE_FOLDER = "sherpa-onnx-ced-tiny-audio-tagging-2024-04-19"

SAMPLE_RATE = 16000
WINDOW_SECONDS = 2.0   # each guess listens to 2 seconds of sound...
STEP_SECONDS = 1.0     # ...and a new guess starts every second

# Which AudioSet labels count towards each simple sound group.
SOUND_GROUPS = {
    "laughter": ["Laughter", "Giggle", "Snicker", "Belly laugh", "Chuckle, chortle", "Baby laughter"],
    "scream": ["Screaming", "Shout", "Yell", "Children shouting", "Battle cry"],
    "cheer": ["Cheering", "Applause", "Crowd"],
    "gasp": ["Gasp", "Sigh"],
    "speech": ["Speech", "Male speech, man speaking", "Female speech, woman speaking", "Conversation"],
    "music": ["Music", "Video game music", "Background music", "Soundtrack music", "Theme music"],
}

_tagger = None


def download_model():
    """
    Download the sound model archive once, check it, and keep only the two needed files.

    Returns:
        bool: True when the model is ready.
    """
    import httpx

    try:
        response = httpx.get(ARCHIVE_URL, follow_redirects=True, timeout=180)
        response.raise_for_status()
    except httpx.HTTPError:
        return False
    if hashlib.sha256(response.content).hexdigest() != ARCHIVE_SHA256:
        return False
    MODEL_FOLDER.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(response.content), mode="r:bz2") as archive:
        for name, destination in ((ARCHIVE_FOLDER + "/model.int8.onnx", MODEL_FILE),
                                  (ARCHIVE_FOLDER + "/class_labels_indices.csv", LABELS_FILE)):
            member = archive.extractfile(name)
            if member is None:
                return False
            partial = destination.with_suffix(".part")
            partial.write_bytes(member.read())
            os.replace(partial, destination)
    return True


def sound_model():
    """
    Load the sound recognition model once per process.

    Returns:
        sherpa_onnx.AudioTagging or None when unavailable (callers then skip sound events).
    """
    global _tagger
    if _tagger is not None:
        return _tagger
    try:
        import sherpa_onnx
    except ImportError:
        return None
    if not (MODEL_FILE.is_file() and LABELS_FILE.is_file()) and not download_model():
        return None
    model = sherpa_onnx.AudioTaggingModelConfig(ced=str(MODEL_FILE), num_threads=4)
    # top_k=60 returns enough labels that every group's labels are seen.
    config = sherpa_onnx.AudioTaggingConfig(model=model, labels=str(LABELS_FILE), top_k=60)
    _tagger = sherpa_onnx.AudioTagging(config)
    return _tagger


def read_audio(source):
    """
    Read a whole video's sound as 16 kHz mono numbers between -1 and 1.

    Args:
        source (str): The video file.

    Returns:
        numpy array of float32 samples (empty when the video has no sound).
    """
    command = ["ffmpeg", "-nostdin", "-v", "error", "-i", str(source), "-vn", "-ac", "1",
               "-ar", str(SAMPLE_RATE), "-f", "f32le", "-"]
    result = subprocess.run(command, capture_output=True)
    if result.returncode != 0:
        return np.zeros(0, dtype=np.float32)
    return np.frombuffer(result.stdout, dtype=np.float32)


def group_scores(events):
    """
    Turn the model's label guesses into one chance per sound group.

    Args:
        events (list): The model's results, each with .name and .prob.

    Returns:
        dict: {"laughter": 0.31, "speech": 0.9, ...}; the strongest label in each group.
    """
    scores = {}
    for group in SOUND_GROUPS:
        scores[group] = 0.0
    for event in events:
        for group, labels in SOUND_GROUPS.items():
            if event.name in labels:
                scores[group] = max(scores[group], round(float(event.prob), 3))
    return scores


def tag_audio(samples):
    """
    Label every second of sound with the sound groups.

    Args:
        samples (numpy array): 16 kHz mono sound.

    Returns:
        list[dict]: One dict per second (second 0 first). Empty when the
                    model is unavailable or there is no sound.
    """
    tagger = sound_model()
    if tagger is None or samples.size == 0:
        return []
    window = int(WINDOW_SECONDS * SAMPLE_RATE)
    step = int(STEP_SECONDS * SAMPLE_RATE)
    seconds = []
    position = 0
    while position < samples.size:
        piece = samples[position:position + window]
        if piece.size < SAMPLE_RATE // 2:
            break
        stream = tagger.create_stream()
        stream.accept_waveform(SAMPLE_RATE, piece)
        seconds.append(group_scores(tagger.compute(stream)))
        position += step
    return seconds


def recording_events(source):
    """
    Get the per-second sound labels for a recording, from the cache if possible.

    Args:
        source (str): The video file (the cache file is saved next to it).

    Returns:
        list[dict]: Per-second sound group chances (may be empty).
    """
    cache = Path(source).with_suffix(".audio_events.json")
    if cache.is_file():
        try:
            return json.loads(cache.read_text())
        except ValueError:
            pass
    events = tag_audio(read_audio(source))
    if events:
        partial = cache.with_suffix(".part")
        partial.write_text(json.dumps(events))
        partial.replace(cache)
    return events


def window_events(events, start, end):
    """
    Summarise the sounds inside one time window.

    Args:
        events (list[dict]): Per-second sound chances.
        start (float): Window start, in seconds.
        end (float): Window end.

    Returns:
        dict: For each group, the strongest chance and how many seconds were
              clearly that sound (chance >= 0.3), plus "peak_laughter_time".
    """
    summary = {}
    for group in SOUND_GROUPS:
        summary[group] = {"max": 0.0, "seconds": 0}
    summary["peak_laughter_time"] = None
    best_laugh = 0.0
    first = max(0, int(start))
    last = min(len(events), int(end) + 1)
    for second in range(first, last):
        for group in SOUND_GROUPS:
            chance = events[second].get(group, 0.0)
            if chance > summary[group]["max"]:
                summary[group]["max"] = chance
            if chance >= 0.3:
                summary[group]["seconds"] += 1
        laugh = events[second].get("laughter", 0.0)
        if laugh > best_laugh and laugh >= 0.2:
            best_laugh = laugh
            summary["peak_laughter_time"] = float(second)
    return summary


def event_marks(events, start, end, threshold=0.3):
    """
    List the clearly-heard laughs, screams and cheers inside a window, for the editor model.

    Args:
        events (list[dict]): Per-second sound chances.
        start (float): Window start.
        end (float): Window end.
        threshold (float): Minimum chance to mention a sound.

    Returns:
        list[tuple]: (second, "LAUGH" | "SCREAM" | "CHEER") in time order,
                     one entry per run of seconds.
    """
    names = {"laughter": "LAUGH", "scream": "SCREAM", "cheer": "CHEER"}
    marks = []
    previous = {}
    for second in range(max(0, int(start)), min(len(events), int(end) + 1)):
        for group, label in names.items():
            heard = events[second].get(group, 0.0) >= threshold
            if heard and not previous.get(group):
                marks.append((second, label))
            previous[group] = heard
    return marks

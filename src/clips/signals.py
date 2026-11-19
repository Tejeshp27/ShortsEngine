# =============================================================
# Free audio/visual measurements and explainable highlight ranking.
# FFmpeg samples sound and tiny grayscale frames, so no GPU or new
# ML package is required. Scores are estimates, never predictions of views.
# =============================================================

import math
import re
import subprocess

import numpy as np

from src.clips.moments import clean_hook_title, fit_to_limits, snap_end_to_word, snap_start_to_word


def measure_media(path, has_audio=True):
    """
    Read local video path and audio availability; return per-second volume, motion and brightness lists.

    Args:
        path: The local file path to read or create.
        has_audio: Whether the source includes an audio track that can be measured.

    Returns:
        A dictionary of per-second volume, frame change and brightness lists.
    """
    levels = []
    if has_audio:
        command = ["ffmpeg", "-nostdin", "-v", "error", "-i", str(path), "-vn",
                   "-ac", "1", "-ar", "8000", "-f", "s16le", "-"]
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        try:
            block = process.stdout.read(16000)
            while block:
                samples = np.frombuffer(block, dtype="<i2").astype(float) / 32768
                rms = float(np.sqrt(np.mean(samples ** 2)))
                levels.append(round(20 * math.log10(max(rms, 0.000001)), 2))
                block = process.stdout.read(16000)
        finally:
            process.stdout.close()
            process.wait()
        if process.returncode:
            raise ValueError("FFmpeg could not measure the recording's audio.")
    command = ["ffmpeg", "-nostdin", "-v", "error", "-i", str(path), "-an", "-vf",
               "fps=1,scale=64:36,format=gray", "-f", "rawvideo", "-"]
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    motion = []
    brightness = []
    previous = None
    try:
        block = process.stdout.read(64 * 36)
        while len(block) == 64 * 36:
            frame = np.frombuffer(block, dtype=np.uint8).astype(float)
            difference = 0.0
            if previous is not None:
                difference = float(np.mean(np.abs(frame - previous))) / 255
            motion.append(round(difference, 4))
            brightness.append(round(float(np.mean(frame)) / 255, 4))
            previous = frame
            block = process.stdout.read(64 * 36)
    finally:
        process.stdout.close()
        process.wait()
    if process.returncode or not motion:
        raise ValueError("FFmpeg could not measure the recording's picture.")
    return {"volume_db": levels, "motion": motion, "brightness": brightness}


def sentence_bounds(words):
    """
    Read timed words and return sentence or pause boundaries as start/end pairs.

    Args:
        words: Timed speech entries containing word, start and end values in seconds.

    Returns:
        A list of sentence start/end time pairs.
    """
    sentences = []
    start = None
    for index, word in enumerate(words):
        if start is None:
            start = word["start"]
        final = index == len(words) - 1
        pause = not final and words[index + 1]["start"] - word["end"] >= 0.8
        if final or pause or word["word"].rstrip('"').endswith((".", "!", "?")):
            sentences.append((start, word["end"]))
            start = None
    return sentences


def candidate_window(peak, duration, words, settings):
    """
    Expand a signal peak into a context-preserving interval; return start and end seconds.

    Args:
        peak: The time in seconds of a possible reaction or activity peak.
        duration: The recording or selected interval length, in seconds.
        words: Timed speech entries containing word, start and end values in seconds.
        settings: The validated clip settings, including analysis and duration limits.

    Returns:
        A pair containing safe start and end times, in seconds.
    """
    limits = settings.clips
    target = min(settings.analysis.candidate_seconds, limits.moment_max_seconds)
    start = max(0, peak - settings.analysis.context_seconds)
    end = min(duration, start + target)
    sentences = sentence_bounds(words)
    for sentence_start, sentence_end in sentences:
        if sentence_start <= start < sentence_end:
            start = sentence_start
        if sentence_start < end <= sentence_end:
            end = sentence_end + 0.3
    start, end = fit_to_limits(start, end, duration, limits.moment_min_seconds, limits.moment_max_seconds)
    start = snap_start_to_word(start, words)
    end = min(end, duration, start + limits.moment_max_seconds)
    end = snap_end_to_word(end, words, min(duration, start + limits.moment_max_seconds))
    # Prefer a complete earlier sentence over chopping a late sentence at the maximum.
    if end - start >= limits.moment_max_seconds - 0.5:
        for sentence_start, sentence_end in reversed(sentences):
            if start + limits.moment_min_seconds <= sentence_end <= end:
                end = min(end, sentence_end + 0.2)
                break
    return round(start, 2), round(end, 2)


def interval_values(values, start, end):
    """
    Slice a per-second list using start/end seconds; return values within the interval.

    Args:
        values: A list containing one measurement for each second.
        start: The selected start time, in seconds in the original recording.
        end: The selected end time, in seconds in the original recording.

    Returns:
        The measurements inside the requested time interval.
    """
    return values[max(0, int(start)):max(1, math.ceil(end))]


def score_candidate(start, end, words, media, settings, engagement=None):
    """
    Score one interval from timed speech and measured media; return score, six components and evidence.

    Args:
        start: The selected start time, in seconds in the original recording.
        end: The selected end time, in seconds in the original recording.
        words: Timed speech entries containing word, start and end values in seconds.
        media: Per-second volume, frame-change and brightness measurements.
        settings: The validated clip settings, including analysis and duration limits.
        engagement: Optional real audience samples with time in seconds and count values.

    Returns:
        A dictionary containing the score, components and supporting speech evidence.
    """
    spoken = []
    speech_seconds = 0
    for word in words:
        if word["end"] > start and word["start"] < end:
            spoken.append(word)
            speech_seconds += max(0, min(end, word["end"]) - max(start, word["start"]))
    spoken_text = []
    opening_words = []
    for word in spoken:
        spoken_text.append(word["word"])
        if word["start"] < start + 6:
            opening_words.append(word["word"])
    text = " ".join(spoken_text)
    lower = text.lower()
    hits = 0
    for term in settings.analysis.excitement_terms:
        if re.search(r"\b" + re.escape(term.lower()) + r"\b", lower):
            hits += 1
    volume = interval_values(media.get("volume_db", []), start, end)
    baseline = media.get("volume_db", [])
    energy = 0.0
    audio = 0.0
    if volume:
        contrast = max(volume) - float(np.median(baseline))
        energy = min(1, max(0, contrast) / 16)
        audible_seconds = 0
        for level in volume:
            if level > -45:
                audible_seconds += 1
        audible = audible_seconds / len(volume)
        audio = audible * (0.5 if max(volume) > -1 else 1)
    movement = interval_values(media.get("motion", []), start, end)
    visual = min(1, max(movement or [0]) / 0.12)
    interest = min(1, hits / 3)
    hook_text = " ".join(opening_words).lower()
    hook = 0.15
    for term in settings.analysis.excitement_terms:
        if term.lower() in hook_text:
            hook = 0.85
            break
    clarity = min(1, speech_seconds / max(1, end - start) / 0.55)
    if spoken and spoken[-1]["word"].rstrip('"').endswith((".", "!", "?")):
        clarity = min(1, clarity + 0.15)
    # Engagement is optional, measured from creator-supplied counts, never invented.
    audience = None
    if engagement:
        counts = []
        chosen = []
        for event in engagement:
            counts.append(event["count"])
            if start <= event["time"] < end:
                chosen.append(event["count"])
        if counts:
            audience = min(1, max(chosen or [0]) / max(1, float(np.median(counts))) / 4)
    components = {"entertainment": min(1, 0.6 * interest + 0.4 * energy),
                  "hook": hook, "audience": audience, "clarity": clarity,
                  "audio": audio, "visual": visual}
    score = weighted_score(components, settings.analysis.weights)
    return {"score": score, "components": components, "excitement_terms": hits,
            "audio_energy": round(energy, 3), "transcript": text,
            "note": "Local heuristic estimate; audience omitted when unavailable. No virality guarantee."}


def weighted_score(components, weights):
    """
    Combine available 0–1 component measurements using configured weights; return a normalized 0–100 estimate.

    Args:
        components: Available score components from zero to one, with None for missing evidence.
        weights: The configured relative weight of each scoring dimension.

    Returns:
        A normalized score from zero to one hundred.
    """
    weighted = 0
    total_weight = 0
    for name, value in components.items():
        if value is not None:
            weighted += value * weights[name]
            total_weight += weights[name]
    if total_weight <= 0:
        return 0.0
    return round(100 * weighted / total_weight, 1)


def candidate_score(candidate):
    """
    Return a candidate dictionary's score for descending sorting.

    Args:
        candidate: One scored interval with start, end and transcript fields.

    Returns:
        The numeric score used to sort candidates.
    """
    return candidate["score"]


def similar_candidate(candidate, selected):
    """
    Compare an interval against selected intervals and speech; return True for substantial repeats.

    Args:
        candidate: One scored interval with start, end and transcript fields.
        selected: Intervals already chosen for this recording.

    Returns:
        True when the candidate substantially overlaps or repeats a selected interval.
    """
    for other in selected:
        overlap = max(0, min(candidate["end"], other["end"]) - max(candidate["start"], other["start"]))
        shorter = min(candidate["end"] - candidate["start"], other["end"] - other["start"])
        if overlap / max(1, shorter) > 0.25:
            return True
        first = set(re.findall(r"\w+", candidate["transcript"].lower()))
        second = set(re.findall(r"\w+", other["transcript"].lower()))
        if len(first) >= 8 and len(second) >= 8 and len(first & second) / len(first | second) > 0.85:
            return True
    return False


def select_highlights(duration, words, media, settings, engagement=None):
    """
    Rank a recording using speech/audio/visual peaks; return diverse candidates above the configured score.

    Args:
        duration: The recording or selected interval length, in seconds.
        words: Timed speech entries containing word, start and end values in seconds.
        media: Per-second volume, frame-change and brightness measurements.
        settings: The validated clip settings, including analysis and duration limits.
        engagement: Optional real audience samples with time in seconds and count values.

    Returns:
        The highest-scoring diverse candidates that meet the configured threshold.
    """
    peaks = []
    # Uniform coverage proposes windows; signals decide which, if any, survive.
    step = max(5, int(settings.analysis.candidate_seconds / 2))
    peaks.extend(range(0, math.ceil(duration), step))
    volume = media.get("volume_db", [])
    median = float(np.median(volume)) if volume else 0
    for index in range(1, len(volume) - 1):
        if volume[index] > median + 6 and volume[index] >= max(volume[index - 1], volume[index + 1]):
            peaks.append(index)
    for word in words:
        if word["word"].strip("!?.,").lower() in settings.analysis.excitement_terms:
            peaks.append(word["start"])
    for index, activity in enumerate(media.get("motion", [])):
        if activity > 0.12:
            peaks.append(index)
    if engagement:
        counts = []
        for event in engagement:
            counts.append(event["count"])
        baseline = max(1, float(np.median(counts)))
        for event in engagement:
            if event["count"] >= baseline * 2:
                peaks.append(event["time"])
                # Chat reacts after the moment happens, so also try a window
                # that starts earlier and keeps more of the build-up.
                peaks.append(max(0, event["time"] - settings.analysis.candidate_seconds * 0.5))
    candidates = []
    seen = set()
    for peak in sorted(set(peaks)):
        start, end = candidate_window(peak, duration, words, settings)
        if (start, end) in seen or end - start < min(duration, settings.clips.moment_min_seconds) - 0.5:
            continue
        seen.add((start, end))
        candidate = score_candidate(start, end, words, media, settings, engagement)
        candidate.update({"start": start, "end": end})
        candidate["hook_title"] = clean_hook_title(candidate["transcript"], settings.hook_title.max_words)
        if not candidate["hook_title"]:
            candidate["hook_title"] = "Stream moment"
        candidates.append(candidate)
    candidates.sort(key=candidate_score, reverse=True)
    selected = []
    for candidate in candidates:
        if candidate["score"] < settings.analysis.minimum_score or similar_candidate(candidate, selected):
            continue
        selected.append(candidate)
        if len(selected) >= settings.analysis.max_clips_per_recording:
            break
    return selected

# =============================================================
# src/video/pacing.py — natural pauses in the voiceover
#
# Text-to-speech engines pause in their own way: often a full
# second of dead air between paragraphs, and no pause at all
# before an important number. Good news explainers do the
# opposite: tight pacing overall, a short beat after the hook, and
# a tiny pause just before a key fact so it lands.
#
# This file reshapes the pauses AFTER the voice is made, using the
# word timings every engine already gives us:
#
#   1. Decode the voiceover into a list of audio samples (numbers).
#   2. Walk through the words. For each gap between two words, decide
#      how long the pause SHOULD be (see choose_pause_length).
#   3. Build a new audio track: each word's sound, then the chosen
#      pause, and shift every word's timing to match.
#
# Because the timings are updated too, captions and graphics stay in
# sync. It works the same for every engine (ElevenLabs, Kokoro, ...).
#
# How it fits in:
#   tts.py → get_voiceover() → apply_pacing() (here) → renderer
# =============================================================

import json
import os
import re
import subprocess
import wave

import numpy

# Every voiceover is converted to this format before reshaping:
# 44,100 samples per second, one channel (mono). 44.1 kHz is CD quality.
SAMPLE_RATE = 44100

# Default pause lengths in seconds, used when the channel YAML does not
# set its own under video.pacing.
DEFAULT_PACING = {
    "enabled": True,
    "after_hook": 0.45,        # a beat after the hook so it sinks in
    "between_sections": 0.30,  # short breath between parts of the story
    "after_sentence": 0.22,    # end of an ordinary sentence
    "after_comma": 0.12,       # a comma or dash inside a sentence
    "before_key_fact": 0.15,   # tiny pause before a number lands
    "max_gap": 0.12,           # longest pause allowed between words in a phrase
    "lead_in": 0.05,           # silence before the very first word
    "tail": 0.40,              # silence after the last word
}

# Words that mean a number is coming, so a key fact is about to land.
NUMBER_WORDS = {
    "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
    "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen",
    "eighteen", "nineteen", "twenty", "thirty", "forty", "fifty", "sixty",
    "seventy", "eighty", "ninety", "hundred", "thousand", "million", "billion",
    "trillion", "half", "dozen", "dozens", "hundreds", "thousands", "millions",
}

# Raised whenever the way pauses are reshaped changes, so voiceovers
# paced by older code are made again instead of loaded from the cache.
PACING_VERSION = 2


def load_pacing_settings(channel_video_settings):
    """
    Read the channel's pause settings, filling gaps with the defaults.

    Args:
        channel_video_settings (dict or None): The channel's "video" block.

    Returns:
        dict: Every key of DEFAULT_PACING.
    """
    settings = dict(DEFAULT_PACING)
    channel_pacing = (channel_video_settings or {}).get("pacing") or {}
    for key in settings:
        if key in channel_pacing:
            settings[key] = channel_pacing[key]
    return settings


def starts_key_fact(word):
    """
    Check whether a word begins a number, such as "17,000" or "three".

    Args:
        word (str): One spoken word, possibly with punctuation.

    Returns:
        bool: True if the word is a number or a number word.

    Example:
        starts_key_fact("$14")      # → True
        starts_key_fact("thirty")   # → True
        starts_key_fact("Senate")   # → False
    """
    if re.search(r"\d", word):
        return True
    cleaned = word.strip(".,;:!?\"'“”()—-").lower()
    return cleaned in NUMBER_WORDS


def choose_pause_length(previous_word, next_word, starts_section, after_hook, settings):
    """
    Decide how long the silence between two words should be.

    Args:
        previous_word (str): The word just spoken, with its punctuation.
        next_word (str): The word about to be spoken.
        starts_section (bool): True if next_word begins a new section.
        after_hook (bool): True if previous_word is the last word of the hook.
        settings (dict): Output of load_pacing_settings().

    Returns:
        float or None: The pause length in seconds, or None for two words
                       in the same phrase (keep the voice's own tiny gap,
                       only trimming it if it is longer than max_gap).

    Example:
        choose_pause_length("traffic.", "On", False, False, settings)  # → 0.22
        choose_pause_length("air", "traffic", False, False, settings)  # → None
    """
    pause = None
    if after_hook:
        pause = settings["after_hook"]
    elif starts_section:
        pause = settings["between_sections"]
    elif previous_word.endswith((".", "!", "?")):
        pause = settings["after_sentence"]
    elif previous_word.endswith((",", ";", ":", "—", "-")):
        pause = settings["after_comma"]

    # A short beat before a number makes the fact land, unless a
    # longer pause is already there.
    if starts_key_fact(next_word) and not starts_key_fact(previous_word):
        if pause is None or pause < settings["before_key_fact"]:
            pause = settings["before_key_fact"]
    return pause


def read_audio_samples(audio_path):
    """
    Decode any audio file into mono samples at SAMPLE_RATE.

    Args:
        audio_path (str): An .mp3 or .wav file.

    Returns:
        numpy.ndarray: Samples between -1.0 and 1.0 (32-bit floats).
    """
    # FFmpeg converts the file to raw 32-bit float numbers and sends them
    # through a pipe, so no temporary file is needed.
    result = subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-i", audio_path,
         "-f", "f32le", "-ac", "1", "-ar", str(SAMPLE_RATE), "-"],
        capture_output=True, check=True,
    )
    return numpy.frombuffer(result.stdout, dtype=numpy.float32)


def write_wav(path, samples):
    """
    Save mono samples as a 16-bit WAV file.

    Args:
        path (str): Where to save the .wav file.
        samples (numpy.ndarray): Samples between -1.0 and 1.0.
    """
    clipped = numpy.clip(samples, -1.0, 1.0)
    as_integers = (clipped * 32767).astype(numpy.int16)
    with wave.open(path, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)        # 2 bytes = 16 bits per sample
        wav_file.setframerate(SAMPLE_RATE)
        wav_file.writeframes(as_integers.tobytes())


def to_sample(seconds):
    """Turn a time in seconds into a sample position (a whole number)."""
    return int(round(seconds * SAMPLE_RATE))


# Loudness is measured in short windows of 10 milliseconds.
WINDOW_SAMPLES = SAMPLE_RATE // 100

# A window counts as "silent" when it is quieter than this fraction of
# the voice's normal loudness.
SILENCE_FRACTION = 0.08

# Sound kept after a word's last loud moment, so endings like "s" or "t"
# fade out naturally instead of being cut off.
WORD_TAIL_SECONDS = 0.04


def window_loudness(samples):
    """
    Measure how loud the audio is in every 10-millisecond window.

    Args:
        samples (numpy.ndarray): Mono samples.

    Returns:
        numpy.ndarray: One loudness value (RMS) per window.
    """
    usable = len(samples) - (len(samples) % WINDOW_SAMPLES)
    windows = samples[:usable].reshape(-1, WINDOW_SAMPLES)
    # RMS = square root of the average squared sample: a standard
    # measure of loudness that ignores whether the wave is up or down.
    return numpy.sqrt(numpy.mean(windows * windows, axis=1))


def find_spoken_end(loudness, start, end, threshold):
    """
    Find where a word's sound really stops, ignoring silence at its end.

    Voice engines often stretch a word's timing into the pause after it
    (for example the full stop "lasts" through the whole pause). The
    audio itself shows where the sound actually stops.

    Args:
        loudness (numpy.ndarray): Output of window_loudness().
        start (float): The word's start time, in seconds.
        end (float): The word's end time from the voice engine.
        threshold (float): Loudness below this counts as silence.

    Returns:
        float: The real end time (never before start, never after end).
    """
    first_window = int(start * 100)
    # Window w covers w/100 to (w+1)/100 seconds. Only look at windows that
    # end before `end`, so the next word's first sound is never mistaken
    # for the end of this one.
    last_window = min(int(end * 100) - 1, len(loudness) - 1)
    for window in range(last_window, first_window, -1):
        if loudness[window] > threshold:
            real_end = (window + 1) / 100 + WORD_TAIL_SECONDS
            return min(end, max(start, real_end))
    return end


def tighten_word_ends(samples, word_timings):
    """
    Move every word's end time to where its sound really stops.

    Args:
        samples (numpy.ndarray): The voiceover samples.
        word_timings (list[dict]): One {"word", "start", "end"} per word.

    Returns:
        list[dict]: New timings; the hidden pauses now count as gaps.
    """
    loudness = window_loudness(samples)
    if len(loudness) == 0:
        return word_timings
    # "Normal loudness" = the level the voice reaches most of the time
    # (the 90th percentile ignores pauses and a few very loud peaks).
    threshold = numpy.percentile(loudness, 90) * SILENCE_FRACTION

    tightened = []
    for index, timing in enumerate(word_timings):
        end = timing["end"]
        # Some engines report no gap at all; let a word reach up to the
        # next word's start so its real ending can be found.
        if index + 1 < len(word_timings):
            end = max(end, word_timings[index + 1]["start"])
        real_end = find_spoken_end(loudness, timing["start"], end, threshold)
        tightened.append({"word": timing["word"], "start": timing["start"], "end": real_end})
    return tightened


def section_first_words(section_word_counts):
    """
    Find the index of the first word of every section after the first.

    Args:
        section_word_counts (list[int]): Words in each spoken section,
                                         in order, e.g. [12, 40, 25].

    Returns:
        set[int]: e.g. {12, 52}. The hook ends just before the first one.
    """
    starts = set()
    position = 0
    for count in section_word_counts[:-1]:
        position += count
        starts.add(position)
    return starts


def reshape_pauses(samples, word_timings, section_word_counts, settings):
    """
    Rebuild the voiceover with the chosen pause after every word.

    Each word keeps its own sound. Where a gap is too long, the middle of
    the silence is cut out (keeping a little breath on both sides). Where a
    gap is too short, silence is added.

    Args:
        samples (numpy.ndarray): The original voiceover samples.
        word_timings (list[dict]): One {"word", "start", "end"} per word.
        section_word_counts (list[int]): Words per spoken section.
        settings (dict): Output of load_pacing_settings().

    Returns:
        tuple: (new samples, new word timings).
    """
    section_starts = section_first_words(section_word_counts)
    hook_end_index = -1
    if section_starts:
        hook_end_index = min(section_starts) - 1
    word_timings = tighten_word_ends(samples, word_timings)

    pieces = [numpy.zeros(to_sample(settings["lead_in"]), dtype=numpy.float32)]
    new_timings = []
    cursor = settings["lead_in"]   # where we are in the NEW audio, in seconds

    for index, timing in enumerate(word_timings):
        # The word's own sound, exactly as spoken.
        word_audio = samples[to_sample(timing["start"]):to_sample(timing["end"])]
        new_start = cursor
        pieces.append(word_audio)
        cursor += len(word_audio) / SAMPLE_RATE
        new_timings.append({"word": timing["word"], "start": round(new_start, 3),
                            "end": round(cursor, 3)})

        if index + 1 >= len(word_timings):
            break

        next_timing = word_timings[index + 1]
        wanted = choose_pause_length(timing["word"], next_timing["word"],
                                     (index + 1) in section_starts,
                                     index == hook_end_index, settings)
        gap_start = to_sample(timing["end"])
        gap_end = to_sample(next_timing["start"])
        gap = samples[gap_start:max(gap_start, gap_end)]
        if wanted is None:
            # Same phrase: keep the natural gap, but never longer than max_gap.
            wanted_samples = min(len(gap), to_sample(settings["max_gap"]))
        else:
            wanted_samples = to_sample(wanted)

        if len(gap) <= wanted_samples:
            # Too short (or exactly right): keep it and add silence.
            padding = numpy.zeros(wanted_samples - len(gap), dtype=numpy.float32)
            pieces.extend([gap, padding])
        else:
            # Too long: keep the start and end of the gap (the breath and
            # the lead-in to the next word) and drop the dead air between.
            half = wanted_samples // 2
            pieces.extend([gap[:half], gap[len(gap) - (wanted_samples - half):]])
        # Either way, the new pause is exactly wanted_samples long.
        cursor += wanted_samples / SAMPLE_RATE

    pieces.append(numpy.zeros(to_sample(settings["tail"]), dtype=numpy.float32))
    return numpy.concatenate(pieces), new_timings


def paced_paths(audio_path, settings):
    """
    Work out where to cache the paced version of a voiceover.

    The name includes the pause settings, so changing a setting in the
    channel YAML makes a fresh version instead of reusing an old one.

    Args:
        audio_path (str): The original voiceover file.
        settings (dict): Output of load_pacing_settings().

    Returns:
        tuple[str, str]: (paced .wav path, paced timing .json path)
    """
    import hashlib
    fingerprint_source = json.dumps(settings, sort_keys=True) + f"v{PACING_VERSION}"
    fingerprint = hashlib.sha256(fingerprint_source.encode()).hexdigest()[:8]
    base = os.path.splitext(audio_path)[0]
    return f"{base}_paced_{fingerprint}.wav", f"{base}_paced_{fingerprint}_timing.json"


def apply_pacing(audio_path, word_timings, section_word_counts, channel_video_settings):
    """
    Make (or load from cache) a version of the voiceover with natural pauses.

    Args:
        audio_path (str): The voiceover from tts.py.
        word_timings (list[dict]): Its word timings.
        section_word_counts (list[int]): Words per spoken section, in order.
        channel_video_settings (dict or None): The channel's "video" block.

    Returns:
        tuple: (audio path, word timings) — the paced versions, or the
               originals if pacing is turned off or there is no timing.

    Example:
        audio, timings = apply_pacing("cache/audio/a.mp3", timings, [12, 40], config["video"])
    """
    settings = load_pacing_settings(channel_video_settings)
    if not settings["enabled"] or not word_timings:
        return audio_path, word_timings

    paced_audio, paced_timing = paced_paths(audio_path, settings)
    if os.path.exists(paced_audio) and os.path.exists(paced_timing):
        with open(paced_timing, "r", encoding="utf-8") as f:
            return paced_audio, json.load(f)

    samples = read_audio_samples(audio_path)
    new_samples, new_timings = reshape_pauses(samples, word_timings, section_word_counts, settings)
    write_wav(paced_audio, new_samples)
    with open(paced_timing, "w", encoding="utf-8") as f:
        json.dump(new_timings, f, indent=2)

    before = len(samples) / SAMPLE_RATE
    after = len(new_samples) / SAMPLE_RATE
    print(f"  Natural pacing applied: {before:.1f}s → {after:.1f}s")
    return paced_audio, new_timings

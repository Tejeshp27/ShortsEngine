# =============================================================
# src/clips/ranking.py — find and rank the moments worth posting
#
# Replaces "loudest/most excited wins" with seven categories from the
# overhaul brief, each scored 0 to 1, then combined with weights from a
# versioned profile file (config/clip_ranking/v1.yaml):
#
#   humor 25 · surprise/payoff 20 · hook 15 · clarity 15 ·
#   pacing 10 · conversation 10 · audio/visual quality 5
#
# Step 1 (free, local): propose windows around possible events (laughter,
#   chat spikes, screams, sudden changes, plus even coverage) and estimate
#   each category from measurable evidence. Loudness is only a small part
#   of "surprise" and never a category on its own.
# Step 2 (optional, ~1 cent per recording): the best few windows go to the
#   editor model (src/clips/editor.py), which reads what was actually said,
#   explains the joke, picks setup-start and payoff-end, and scores humor etc.
# Step 3: combine, reject weak or confusing moments, keep at most N distinct
#   ones. Fewer clips — or none — is the correct result for a boring hour.
#
# Scores are editorial estimates, never predictions of views.
# =============================================================

import math
import re
from pathlib import Path

import numpy as np
import yaml

from src.clips.moments import snap_end_to_word, snap_start_to_word

CATEGORIES = ["humor", "surprise", "hook", "clarity", "pacing", "conversation", "quality"]

# Words people say when something is funny or surprising (only a weak hint).
LAUGH_WORDS = {"haha", "hahaha", "lol", "lmao", "lmfao", "dead", "crying", "bro", "nah", "stop"}
SURPRISE_WORDS = {"what", "wait", "no", "yo", "how", "why", "huh", "bruh"}

# A gap with no words counts as a reaction (not dead air) when it is no more
# than this many dB quieter than the talking around it.
REACTION_GAP_MARGIN_DB = 3

# Pacing bonus for a window that has at least one reaction beat (a 2 s+
# gap filled with laughing, screaming or a crowd).
REACTION_BEAT_BONUS = 0.15

DEFAULT_PROFILE = {
    "version": "v1",
    "weights": {"humor": 25, "surprise": 20, "hook": 15, "clarity": 15,
                "pacing": 10, "conversation": 10, "quality": 5},
    "minimum_score": 60,
    "payoff_floor": 0.45,
    "clarity_floor": 0.35,
    "model_share": 0.75,
    "lengths": {"reaction": [8, 25], "gameplay": [10, 35], "conversation": [15, 45],
                "awkward": [10, 35], "story": [25, 60], "argument": [15, 50]},
}


# ---------------------------------------------------------------------------
# Profiles (versioned weights) — see config/clip_ranking/
# ---------------------------------------------------------------------------

def load_profile(path):
    """
    Read a ranking profile (weights, thresholds, preferred lengths) from YAML.

    Missing values use DEFAULT_PROFILE, so an old or partial file still works.

    Args:
        path (str): The profile file, e.g. "config/clip_ranking/v1.yaml".

    Returns:
        dict: The complete profile, with "path" added.

    Raises:
        ValueError: If the file is missing or a weight is not a number >= 0.
    """
    file = Path(path)
    if not file.is_file():
        raise ValueError(f"Ranking profile {path} not found. Check ranking.profile in the channel YAML.")
    data = yaml.safe_load(file.read_text()) or {}
    profile = dict(DEFAULT_PROFILE)
    for key, value in data.items():
        profile[key] = value
    weights = {}
    for category in CATEGORIES:
        value = profile.get("weights", {}).get(category, 0)
        if not isinstance(value, (int, float)) or value < 0 or not math.isfinite(value):
            raise ValueError(f"Ranking weight for {category} must be a number >= 0.")
        weights[category] = float(value)
    if sum(weights.values()) <= 0:
        raise ValueError("At least one ranking weight must be positive.")
    profile["weights"] = weights
    profile["path"] = str(path)
    return profile


def weighted_total(components, weights):
    """
    Combine 0–1 category scores into one 0–100 score.

    Categories that are None (no evidence) are left out, and the weights
    of the rest are scaled up so the result is still out of 100.

    Args:
        components (dict): {"humor": 0.7, ...}.
        weights (dict): {"humor": 25, ...}.

    Returns:
        float: 0 to 100.

    Example:
        weighted_total({"humor": 1.0, "hook": 0.5}, {"humor": 25, "hook": 15}) → 81.2
    """
    total = 0.0
    used = 0.0
    for category, value in components.items():
        weight = weights.get(category, 0)
        if value is None or weight <= 0:
            continue
        total += value * weight
        used += weight
    if used <= 0:
        return 0.0
    return round(100 * total / used, 1)


# ---------------------------------------------------------------------------
# Local evidence
# ---------------------------------------------------------------------------

def ends_sentence_word(text):
    """
    Check whether a transcript word ends a sentence, ignoring quotes and Whisper's joining dashes.

    Args:
        text (str): A word such as "face.-" or "bro."

    Returns:
        bool: True when it ends with . ! or ?

    Example:
        ends_sentence_word("face.-") → True
    """
    return text.rstrip('"\'-–— ').endswith((".", "!", "?"))


def words_between(words, start, end):
    """
    Return the timed words that overlap a time window.

    Args:
        words (list[dict]): Timed words.
        start (float): Window start.
        end (float): Window end.

    Returns:
        list[dict]: The overlapping words.
    """
    inside = []
    for word in words:
        if word["end"] > start and word["start"] < end:
            inside.append(word)
    return inside


def plain_word(text):
    """
    Lower-case a word and strip punctuation around it.

    Args:
        text (str): For example "What?!".

    Returns:
        str: "what".
    """
    return text.strip(" .,!?\"'…-").lower()


def speech_gaps(spoken, start, end):
    """
    List the silent gaps between words inside a window (including the edges).

    Args:
        spoken (list[dict]): The window's timed words.
        start (float): Window start.
        end (float): Window end.

    Returns:
        list[tuple]: (gap_start, gap_end) pairs, in time order.
    """
    gaps = []
    cursor = start
    for word in spoken:
        if word["start"] > cursor:
            gaps.append((cursor, word["start"]))
        cursor = max(cursor, word["end"])
    if end > cursor:
        gaps.append((cursor, end))
    return gaps


def gap_is_reaction(gap_start, gap_end, volume, talk_level, sounds):
    """
    Decide whether a gap with no words is a reaction (laughing, screaming,
    a crowd) rather than dead air.

    The TikTok study (docs/TIKTOK_CONTENT_RESEARCH.md, section 2.2) found
    that clips which break out have MORE of these loud wordless moments
    than the same account's flops (higher in 22 of 25 accounts), so they
    must not be punished as "dead air".

    Args:
        gap_start (float): Start of the gap, in seconds.
        gap_end (float): End of the gap.
        volume (list[float]): Loudness in dB for each second of the recording.
        talk_level (float or None): Typical loudness while people talk in
            this window (None when unknown).
        sounds (list[dict]): Per-second sound chances (may be empty).

    Returns:
        bool: True when the gap is a reaction, False when it is quiet.

    Example:
        gap_is_reaction(10, 13, volume, -20.0, [])   # True if seconds 10-12 are about -20 dB
    """
    first = int(gap_start)
    last = max(first + 1, math.ceil(gap_end))
    # The sound model clearly hears a laugh, scream or cheer in the gap.
    for second in range(first, min(last, len(sounds or []))):
        heard = sounds[second]
        if max(heard.get("laughter", 0), heard.get("scream", 0), heard.get("cheer", 0)) >= 0.3:
            return True
    # Or the gap is about as loud as the talking (within REACTION_GAP_MARGIN_DB).
    levels = volume[first:last] if volume else []
    if levels and talk_level is not None:
        if float(np.median(levels)) >= talk_level - REACTION_GAP_MARGIN_DB:
            return True
    return False


def chat_ratio(engagement, start, end, reaction_delay=4.0):
    """
    How much chat reacted during (and just after) a window, versus normal.

    Args:
        engagement (list[dict]): {"time", "count"} samples (live chat, viewer clips).
        start (float): Window start.
        end (float): Window end.
        reaction_delay (float): Chat reacts a few seconds after the moment.

    Returns:
        float or None: Peak / median chat activity (None when no chat data).
    """
    if not engagement:
        return None
    counts = []
    inside = []
    for event in engagement:
        counts.append(event["count"])
        if start <= event["time"] < end + reaction_delay:
            inside.append(event["count"])
    baseline = max(1.0, float(np.median(counts)))
    if not inside:
        return 0.0
    return max(inside) / baseline


def local_components(start, end, words, media, sounds, engagement):
    """
    Estimate the seven categories for one window from measurable evidence.

    Args:
        start (float): Window start, in seconds.
        end (float): Window end.
        words (list[dict]): All timed words of the recording.
        media (dict): Per-second "volume_db" and "motion" lists (signals.measure_media).
        sounds (list[dict]): Per-second sound chances (audio_events.recording_events).
        engagement (list[dict]): Chat/viewer-clip samples, possibly empty.

    Returns:
        tuple: (components dict with 0–1 values, evidence dict explaining them).
    """
    from src.clips.audio_events import window_events

    length = max(1.0, end - start)
    spoken = words_between(words, start, end)
    texts = []
    for word in spoken:
        texts.append(plain_word(word["word"]))
    heard = window_events(sounds, start, end + 2) if sounds else None

    # Humor: laughter heard (the strongest local sign), chat bursts, laugh words.
    laughter = 0.0
    if heard:
        laughter = min(1.0, heard["laughter"]["max"] / 0.5) * 0.7 + min(1.0, heard["laughter"]["seconds"] / 4) * 0.3
    chat = chat_ratio(engagement, start, end)
    chat_score = None if chat is None else min(1.0, max(0.0, (chat - 1.0) / 3.0))
    laugh_words = 0
    surprise_words = 0
    for text in texts:
        if text in LAUGH_WORDS:
            laugh_words += 1
        if text in SURPRISE_WORDS:
            surprise_words += 1
    humor = 0.65 * laughter + 0.15 * min(1.0, laugh_words / 3)
    if chat_score is not None:
        humor += 0.2 * chat_score
    else:
        humor = humor / 0.8

    # Surprise: screams/gasps/cheers, a sudden change versus the 10 s before,
    # chat. Loudness is capped at 0.25 of this one category.
    volume = media.get("volume_db", [])
    jump = 0.0
    if volume:
        before = volume[max(0, int(start) - 10):max(1, int(start))]
        inside = volume[max(0, int(start)):max(1, math.ceil(end))]
        if before and inside:
            jump = min(1.0, max(0.0, max(inside) - float(np.median(before))) / 15)
    shock = 0.0
    if heard:
        shock = max(heard["scream"]["max"], heard["gasp"]["max"], heard["cheer"]["max"])
    surprise = 0.45 * min(1.0, shock / 0.5) + 0.25 * jump + 0.1 * min(1.0, surprise_words / 3)
    if chat_score is not None:
        surprise += 0.2 * chat_score
    else:
        surprise = surprise / 0.8

    # Hook: someone speaks (or something is heard) in the first 3 seconds.
    opening = words_between(spoken, start, start + 3)
    hook = 0.25
    if opening:
        hook = 0.6
        for word in opening:
            if word["word"].endswith(("?", "!")) or plain_word(word["word"]) in SURPRISE_WORDS:
                hook = 0.8
                break
    elif heard and (heard["laughter"]["max"] > 0.3 or heard["scream"]["max"] > 0.3):
        hook = 0.5

    # Clarity: enough speech, complete sentences, not mostly music (game dialogue).
    speech_seconds = 0.0
    for word in spoken:
        speech_seconds += max(0.0, min(end, word["end"]) - max(start, word["start"]))
    coverage = min(1.0, speech_seconds / length / 0.45)
    clarity = 0.8 * coverage
    if spoken and ends_sentence_word(spoken[-1]["word"]):
        clarity += 0.2
    if heard and heard["music"]["seconds"] > length * 0.6:
        clarity -= 0.2
    clarity = min(1.0, max(0.0, clarity))

    # Pacing: little dead air, a comfortable talking speed. A gap filled
    # with laughing/screaming is a "reaction beat", not dead air: clips that
    # break out on TikTok have more of them (TIKTOK_CONTENT_RESEARCH.md 2.2).
    talk_seconds = []
    for word in spoken:
        talk_seconds.append(int(word["start"]))
    talk_level = None
    if volume and talk_seconds:
        talk_levels = []
        for second in sorted(set(talk_seconds)):
            if second < len(volume):
                talk_levels.append(volume[second])
        if talk_levels:
            talk_level = float(np.median(talk_levels))
    dead_air = 0.0
    reaction_beats = 0
    for gap_start, gap_end in speech_gaps(spoken, start, end):
        gap_length = gap_end - gap_start
        if gap_length <= 1.5:
            continue
        if gap_is_reaction(gap_start, gap_end, volume, talk_level, sounds):
            if gap_length >= 2.0:
                reaction_beats += 1
        else:
            dead_air += gap_length - 1.5
    words_per_second = len(spoken) / length
    pacing = 1.0 - min(1.0, dead_air / length * 2)
    if words_per_second < 0.8 and reaction_beats == 0:
        pacing -= 0.2
    if reaction_beats:
        pacing += REACTION_BEAT_BONUS
    pacing = min(1.0, max(0.0, pacing))

    # Conversation: questions, talking to someone, chat activity.
    questions = 0
    for word in spoken:
        if word["word"].endswith("?"):
            questions += 1
    talking_to = 0
    for text in texts:
        if text in ("you", "bro", "chat", "y'all", "yall", "bruh"):
            talking_to += 1
    conversation = 0.5 * min(1.0, questions / 3) + 0.3 * min(1.0, talking_to / 4)
    if chat_score is not None:
        conversation += 0.2 * chat_score
    else:
        conversation = conversation / 0.8

    # Quality: sound present, not distorted, picture not frozen.
    quality = 0.5
    if volume:
        inside = volume[max(0, int(start)):max(1, math.ceil(end))]
        audible = 0
        for level in inside:
            if level > -45:
                audible += 1
        quality = audible / max(1, len(inside))
        if inside and max(inside) > -1:
            quality *= 0.6
    motion = media.get("motion", [])[max(0, int(start)):max(1, math.ceil(end))]
    if motion and max(motion) < 0.002:
        quality *= 0.5

    components = {"humor": round(min(1.0, humor), 3), "surprise": round(min(1.0, surprise), 3),
                  "hook": round(hook, 3), "clarity": round(clarity, 3), "pacing": round(pacing, 3),
                  "conversation": round(min(1.0, conversation), 3), "quality": round(quality, 3)}
    evidence = {"laughter_max": heard["laughter"]["max"] if heard else None,
                "laughter_seconds": heard["laughter"]["seconds"] if heard else None,
                "peak_laughter_time": heard["peak_laughter_time"] if heard else None,
                "scream_max": heard["scream"]["max"] if heard else None,
                "music_seconds": heard["music"]["seconds"] if heard else None,
                "chat_ratio": None if chat is None else round(chat, 2),
                "volume_jump": round(jump, 3), "dead_air_seconds": round(dead_air, 1),
                "reaction_beats": reaction_beats,
                "words": len(spoken)}
    return components, evidence


# ---------------------------------------------------------------------------
# Proposing windows
# ---------------------------------------------------------------------------

def event_times(duration, words, media, sounds, engagement):
    """
    List times where something may have happened (where windows should look).

    Args:
        duration (float): Recording length, in seconds.
        words (list[dict]): Timed words.
        media (dict): Per-second volume/motion.
        sounds (list[dict]): Per-second sound chances.
        engagement (list[dict]): Chat samples.

    Returns:
        list[float]: Candidate "payoff" times, sorted, without duplicates.
    """
    times = set()
    # Even coverage, so quiet deadpan moments are still looked at.
    for second in range(0, int(duration), 15):
        times.add(float(second))
    for second, chances in enumerate(sounds or []):
        if chances.get("laughter", 0) >= 0.25 or chances.get("scream", 0) >= 0.35 or chances.get("cheer", 0) >= 0.35:
            times.add(float(second))
    volume = media.get("volume_db", [])
    if volume:
        median = float(np.median(volume))
        for second in range(1, len(volume) - 1):
            if volume[second] > median + 8 and volume[second] >= max(volume[second - 1], volume[second + 1]):
                times.add(float(second))
    if engagement:
        counts = []
        for event in engagement:
            counts.append(event["count"])
        baseline = max(1.0, float(np.median(counts)))
        for event in engagement:
            if event["count"] >= baseline * 2:
                # Chat reacts ~4 s after the moment itself.
                times.add(max(0.0, float(event["time"]) - 4))
    for word in words:
        if word["word"].endswith("?") or plain_word(word["word"]) in LAUGH_WORDS:
            times.add(round(word["start"], 1))
    kept = []
    for value in sorted(times):
        if 0 <= value <= duration:
            kept.append(value)
    return kept


def sentence_start_before(words, time_seconds):
    """
    Find where the sentence containing (or just before) a time begins.

    A sentence ends at . ! ? or at a pause of 0.8 s or more.

    Args:
        words (list[dict]): Timed words.
        time_seconds (float): The time.

    Returns:
        float: The sentence's first word start (or the time itself when no words).
    """
    start = time_seconds
    for index, word in enumerate(words):
        if word["start"] > time_seconds:
            break
        previous_ends = index == 0 or ends_sentence_word(words[index - 1]["word"])
        pause_before = index == 0 or word["start"] - words[index - 1]["end"] >= 0.8
        if previous_ends or pause_before:
            start = word["start"]
    return start


def sentence_end_after(words, time_seconds, latest):
    """
    Find where the sentence that is running at a time ends.

    Args:
        words (list[dict]): Timed words.
        time_seconds (float): The time.
        latest (float): Never go past this time.

    Returns:
        float: The end of that sentence's last word (or the time itself).
    """
    for index, word in enumerate(words):
        if word["end"] < time_seconds:
            continue
        if word["start"] > latest:
            break
        final = index == len(words) - 1
        pause_after = not final and words[index + 1]["start"] - word["end"] >= 0.8
        if ends_sentence_word(word["word"]) or pause_after or final:
            return min(latest, word["end"])
    return min(latest, time_seconds)


def skip_leading_fragment(words, start, latest):
    """
    Do not open on the tail of an earlier sentence ("...teeth in the face. I hate fat people").

    When the clip starts in the middle of a sentence, move the start to the
    beginning of the next sentence, if that is no later than `latest`.

    Args:
        words (list[dict]): Timed words.
        start (float): Proposed start.
        latest (float): The start may move at most to here.

    Returns:
        float: The (possibly later) start.
    """
    previous = None
    for word in words:
        if word["end"] <= start:
            previous = word
        else:
            break
    if previous is None or ends_sentence_word(previous["word"]):
        return start
    first_inside = None
    for word in words:
        if word["start"] >= start - 0.05:
            first_inside = word
            break
    if first_inside is not None and first_inside["start"] - previous["end"] >= 0.8:
        return start   # a pause before the first word: it is a fresh start
    for index, word in enumerate(words):
        if word["start"] < start:
            continue
        if word["start"] > latest:
            break
        if ends_sentence_word(word["word"]) and index + 1 < len(words):
            following = words[index + 1]
            if following["start"] <= latest:
                return following["start"]
            break
    return start


def ends_cleanly(words, end):
    """
    Check whether a clip ending at `end` stops at a natural point (not mid-sentence).

    It ends cleanly when nobody is speaking at `end` and the last word before it
    finishes a sentence (. ! ?) or is followed by a pause of at least 0.8 seconds.

    Args:
        words (list[dict]): Timed words.
        end (float): The proposed end, in seconds.

    Returns:
        bool: True for a natural stopping point.
    """
    last = None
    following = None
    for word in words:
        if word["start"] < end < word["end"]:
            return False   # the end cuts through a word
        if word["end"] <= end:
            last = word
        elif following is None:
            following = word
    if last is None:
        return True
    if ends_sentence_word(last["word"]):
        return True
    return following is None or following["start"] - last["end"] >= 0.8


def window_around(payoff, duration, words, limits, lead_seconds=18.0, tail_seconds=3.0):
    """
    Build a setup → payoff window around a possible payoff time.

    The start goes back to the beginning of a sentence ~lead_seconds before
    the payoff (the setup); the end is the end of the sentence a few seconds
    after it (the reaction). The editor model can tighten both later.

    Args:
        payoff (float): When the funny/surprising thing seems to happen.
        duration (float): Recording length.
        words (list[dict]): Timed words.
        limits (ClipLimits): moment_min_seconds / moment_max_seconds.
        lead_seconds (float): How much setup to include before the payoff.
        tail_seconds (float): How much reaction to include after it.

    Returns:
        tuple: (start, end) in seconds.
    """
    start = sentence_start_before(words, max(0.0, payoff - lead_seconds))
    end = sentence_end_after(words, min(duration, payoff + tail_seconds), min(duration, payoff + 12))
    end = max(end, min(duration, payoff + tail_seconds))
    if end - start < limits.moment_min_seconds:
        start = max(0.0, end - limits.moment_min_seconds)
    if end - start > limits.moment_max_seconds:
        start = end - limits.moment_max_seconds
    start = snap_start_to_word(start, words)
    end = snap_end_to_word(end, words, min(duration, start + limits.moment_max_seconds))
    return round(start, 2), round(end, 2)


def overlaps(first, second, share=0.25):
    """
    Check whether two windows overlap by more than `share` of the shorter one.

    Args:
        first (dict): {"start", "end"}.
        second (dict): {"start", "end"}.
        share (float): Allowed overlap.

    Returns:
        bool: True for substantial overlap.
    """
    overlap = min(first["end"], second["end"]) - max(first["start"], second["start"])
    shorter = min(first["end"] - first["start"], second["end"] - second["start"])
    return overlap > share * max(1.0, shorter)


def propose_candidates(duration, words, media, sounds, engagement, settings, profile, pool_size=None):
    """
    Propose windows and score them locally; return the best distinct ones.

    pool_size (int or None): return this many instead of ranking.shortlist_size
        (a bigger pool is re-ranked with faces before the editor sees it).

    Args:
        duration (float): Recording length.
        words (list[dict]): Timed words.
        media (dict): Per-second volume/motion.
        sounds (list[dict]): Per-second sound chances.
        engagement (list[dict]): Chat samples.
        settings (ClipChannelSettings): Limits and ranking settings.
        profile (dict): From load_profile().

    Returns:
        list[dict]: Up to ranking.shortlist_size candidates, best first, each with
                    "start", "end", "payoff_time", "components", "local_score",
                    "evidence" and "transcript".
    """
    seen = set()
    candidates = []
    for payoff in event_times(duration, words, media, sounds, engagement):
        start, end = window_around(payoff, duration, words, settings.clips)
        if (start, end) in seen or end - start < min(duration, settings.clips.moment_min_seconds) - 0.5:
            continue
        seen.add((start, end))
        components, evidence = local_components(start, end, words, media, sounds, engagement)
        spoken = []
        for word in words_between(words, start, end):
            spoken.append(word["word"])
        candidates.append({"start": start, "end": end, "payoff_time": payoff,
                           "components": components, "evidence": evidence,
                           "local_score": weighted_total(components, profile["weights"]),
                           "transcript": " ".join(spoken)})
    candidates.sort(key=local_score_of, reverse=True)
    shortlist = []
    for candidate in candidates:
        repeated = False
        for chosen in shortlist:
            if overlaps(candidate, chosen):
                repeated = True
                break
        if repeated:
            continue
        shortlist.append(candidate)
        if len(shortlist) >= (pool_size or settings.ranking.shortlist_size):
            break
    return shortlist


def local_score_of(candidate):
    """
    Sorting key for candidates: their local score.

    Args:
        candidate (dict): A candidate.

    Returns:
        float: Its local score.
    """
    return candidate["local_score"]


# ---------------------------------------------------------------------------
# Faces (TikTok watching study, docs/TIKTOK_CONTENT_RESEARCH.md 2.4a-2)
# ---------------------------------------------------------------------------
# On 91 clip accounts never used to build the rules, an account's breakouts
# showed a bigger surprised face than its own flops (58 accounts vs 33), and
# its biggest breakouts had more people on screen (44 vs 31). A laughing face
# did NOT predict views (streamers smile constantly), so it is not scored.

# A peak surprise reading of this size counts fully (FER+ readings 0-1;
# breakouts averaged 0.37 vs 0.34, so 0.6 is a clearly surprised face).
FULL_SURPRISE = 0.6
# Half of the pictures showing two or more faces counts fully.
FULL_MULTI_FACE_SHARE = 0.5


def face_evidence(readings, start, end):
    """
    Summarise face readings inside one window.

    Args:
        readings (list[dict]): {"time", "surprise", "faces", ...} per sampled
            second (from expressions.read_face_track).
        start (float): Window start, in seconds.
        end (float): Window end.

    Returns:
        dict: {"peak_surprise", "multi_face_share", "face_readings"};
              both values are None when no reading falls inside the window.

    Example:
        face_evidence([{"time": 3, "surprise": 0.7, "faces": 2}], 0, 10)
        # {"peak_surprise": 0.7, "multi_face_share": 1.0, "face_readings": 1}
    """
    inside = []
    for reading in readings:
        if start <= reading.get("time", -1) <= end:
            inside.append(reading)
    if not inside:
        return {"peak_surprise": None, "multi_face_share": None, "face_readings": 0}
    peak = 0.0
    several = 0
    for reading in inside:
        peak = max(peak, float(reading.get("surprise", 0) or 0))
        if (reading.get("faces", 0) or 0) >= 2:
            several += 1
    return {"peak_surprise": round(peak, 3), "multi_face_share": round(several / len(inside), 3),
            "face_readings": len(inside)}


def add_face_evidence(candidate, readings, profile):
    """
    Fold face evidence into a candidate's local surprise and conversation scores.

    The profile's "face_weights" say how much each face signal counts
    (0 or missing = ignored, so older profiles behave exactly as before):
        surprise_peak: share of the surprise score taken by the peak surprise.
        multi_face: share of the conversation score taken by the share of
                    pictures with two or more people.

    Args:
        candidate (dict): From propose_candidates() (changed in place).
        readings (list[dict]): Face readings covering the window.
        profile (dict): From load_profile().

    Returns:
        dict: The same candidate, with updated "components", "local_score"
              and the face numbers added to "evidence".
    """
    weights = profile.get("face_weights") or {}
    found = face_evidence(readings, candidate["start"], candidate["end"])
    candidate["evidence"].update(found)
    if found["face_readings"] == 0:
        return candidate
    components = candidate["components"]
    surprise_share = float(weights.get("surprise_peak", 0) or 0)
    if surprise_share > 0:
        face_part = min(1.0, found["peak_surprise"] / FULL_SURPRISE)
        mixed = (1 - surprise_share) * components["surprise"] + surprise_share * face_part
        components["surprise"] = round(mixed, 3)
    people_share = float(weights.get("multi_face", 0) or 0)
    if people_share > 0:
        face_part = min(1.0, found["multi_face_share"] / FULL_MULTI_FACE_SHARE)
        mixed = (1 - people_share) * components["conversation"] + people_share * face_part
        components["conversation"] = round(mixed, 3)
    if surprise_share > 0 or people_share > 0:
        candidate["local_score"] = weighted_total(components, profile["weights"])
    return candidate


def rerank_with_faces(pool, tracks, profile, keep):
    """
    Re-score a pool of candidates with their faces and keep the best `keep`.

    Args:
        pool (list[dict]): Distinct candidates from propose_candidates().
        tracks (list[list[dict]]): Face readings for each pool candidate, same order.
        profile (dict): From load_profile().
        keep (int): How many to return (ranking.shortlist_size).

    Returns:
        tuple: (shortlist, its face readings), best first.
    """
    track_of = {}
    for candidate, track in zip(pool, tracks):
        add_face_evidence(candidate, track, profile)
        track_of[id(candidate)] = track
    # Python's sort keeps the original order for equal scores, so on a tie the
    # candidate that was better before faces stays first.
    ranked = list(pool)
    ranked.sort(key=local_score_of, reverse=True)
    shortlist = ranked[:keep]
    kept_tracks = []
    for candidate in shortlist:
        kept_tracks.append(track_of[id(candidate)])
    return shortlist, kept_tracks


# ---------------------------------------------------------------------------
# Combining with the editor model and deciding
# ---------------------------------------------------------------------------

def combine(candidate, review, profile):
    """
    Blend local evidence with the editor model's judgement for one candidate.

    The model reads the words, so it decides most of humor/surprise/hook/
    clarity/pacing/conversation (profile "model_share", 0.75 by default);
    local evidence (laughter, chat) keeps it honest. Quality stays local.

    Args:
        candidate (dict): From propose_candidates().
        review (dict or None): The editor model's review (editor.review_candidates).
        profile (dict): The ranking profile.

    Returns:
        dict: Final "components" (0–1) and "score" (0–100).
    """
    components = dict(candidate["components"])
    if review and isinstance(review.get("scores"), dict):
        share = float(profile.get("model_share", 0.75))
        for category in CATEGORIES:
            if category == "quality":
                continue
            value = review["scores"].get(category)
            if isinstance(value, (int, float)) and math.isfinite(value):
                model_value = min(1.0, max(0.0, value / 10))
                components[category] = round(share * model_value + (1 - share) * components[category], 3)
    return {"components": components, "score": weighted_total(components, profile["weights"])}


def rejection_reason(final, review, profile, has_review):
    """
    Decide whether a candidate must be rejected, and why.

    Args:
        final (dict): From combine().
        review (dict or None): The editor model's review.
        profile (dict): Thresholds.
        has_review (bool): Whether the editor model looked at this candidate.

    Returns:
        str: The reason, or "" when the candidate may be used.
    """
    components = final["components"]
    if review and review.get("keep") is False:
        return "Editor: " + str(review.get("reject_reason") or "no real payoff")[:200]
    if review and profile.get("require_event") and not str(review.get("event", "")).strip():
        return "Editor could not name an unexpected event (calm moments flop)."
    if has_review and not review:
        return "Editor model did not review this window."
    if max(components["humor"], components["surprise"]) < profile["payoff_floor"]:
        return "No meaningful payoff (humor and surprise both weak)."
    if components["clarity"] < profile["clarity_floor"]:
        return "Too hard to follow (unclear speech or context)."
    if final["score"] < profile["minimum_score"]:
        return f"Score {final['score']} is below the minimum {profile['minimum_score']}."
    return ""


def apply_boundaries(candidate, review, words, duration, limits, profile):
    """
    Use the editor model's setup-start and payoff-end, made safe.

    Args:
        candidate (dict): The window.
        review (dict or None): May contain "start" and "end" in seconds and a "category".
        words (list[dict]): Timed words.
        duration (float): Recording length.
        limits (ClipLimits): Length limits.
        profile (dict): Preferred lengths by category.

    Returns:
        tuple: (start, end) snapped to word edges and inside the limits.
    """
    start = candidate["start"]
    end = candidate["end"]
    if review:
        try:
            proposed_start = float(review.get("start"))
            proposed_end = float(review.get("end"))
            if 0 <= proposed_start < proposed_end <= duration:
                start, end = proposed_start, proposed_end
        except (TypeError, ValueError):
            pass
    maximum = limits.moment_max_seconds
    category = (review or {}).get("category", "")
    if category in profile.get("lengths", {}):
        maximum = min(maximum, float(profile["lengths"][category][1]) + 10)
    if end - start > maximum:
        start = end - maximum   # keep the payoff; lose some setup
    if end - start < limits.moment_min_seconds:
        end = min(duration, start + limits.moment_min_seconds)
    # Let the speaker finish: end at the close of the sentence that is still
    # running (punctuation or a pause), up to 8 seconds later and within the limits.
    latest = min(duration, start + limits.moment_max_seconds, end + 8.0)
    if not ends_cleanly(words, end):
        end = max(end, sentence_end_after(words, end, latest))
    start = skip_leading_fragment(words, start, min(start + 6.0, end - limits.moment_min_seconds))
    start = snap_start_to_word(max(0.0, start), words)
    end = snap_end_to_word(min(duration, end), words, min(duration, start + limits.moment_max_seconds))
    return round(start, 2), round(end, 2)


def decide_candidates(shortlist, reviews, words, duration, settings, profile):
    """
    Combine local scores with the editor model's reviews and pick the final moments.

    Args:
        shortlist (list[dict]): From propose_candidates().
        reviews (list): One review (dict or None) per shortlisted candidate, or an
                        empty list when the editor model was not used.
        words (list[dict]): Timed words.
        duration (float): Recording length, in seconds.
        settings (ClipChannelSettings): Limits and ranking settings.
        profile (dict): From load_profile().

    Returns:
        dict: {"selected": [...], "rejected": [...], "reviewed": bool}; every
              candidate carries its score, components, category, reason and boundaries.
    """
    reviewed = bool(reviews)
    selected = []
    rejected = []
    for index, candidate in enumerate(shortlist):
        review = reviews[index] if index < len(reviews) else None
        final = combine(candidate, review, profile)
        candidate["components"] = final["components"]
        candidate["score"] = final["score"]
        candidate["review"] = review or {}
        candidate["category"] = (review or {}).get("category") or guess_category(final["components"])
        candidate["start"], candidate["end"] = apply_boundaries(candidate, review, words, duration,
                                                                settings.clips, profile)
        reason = rejection_reason(final, review, profile, reviewed)
        if reason:
            candidate["rejected"] = reason
            rejected.append(candidate)
            continue
        candidate["reason"] = (review or {}).get("why_funny") or describe_local(candidate)
        selected.append(candidate)
    selected.sort(key=score_of, reverse=True)
    final_list = []
    for candidate in selected:
        repeated = False
        for chosen in final_list:
            if overlaps(candidate, chosen):
                repeated = True
                break
        if repeated:
            candidate["rejected"] = "Overlaps a better moment."
            rejected.append(candidate)
            continue
        if len(final_list) >= settings.analysis.max_clips_per_recording:
            candidate["rejected"] = "Recording already has its maximum number of clips."
            rejected.append(candidate)
            continue
        final_list.append(candidate)
    return {"selected": final_list, "rejected": rejected, "reviewed": reviewed}


def score_of(candidate):
    """
    Sorting key: final score.

    Args:
        candidate (dict): A candidate.

    Returns:
        float: Its score.
    """
    return candidate["score"]


def guess_category(components):
    """
    Name a moment's type from its strongest category (used without the editor model).

    Args:
        components (dict): 0–1 category scores.

    Returns:
        str: "reaction", "conversation" or "gameplay".
    """
    if components["conversation"] >= max(components["humor"], components["surprise"]):
        return "conversation"
    if components["humor"] >= components["surprise"]:
        return "reaction"
    return "gameplay"


def describe_local(candidate):
    """
    Explain in plain words why a moment was chosen without the editor model.

    Args:
        candidate (dict): A scored candidate.

    Returns:
        str: For example "Laughter heard (0.62); chat 3.1x normal; local estimate".
    """
    evidence = candidate["evidence"]
    parts = []
    if evidence.get("laughter_max"):
        parts.append(f"laughter heard ({evidence['laughter_max']:.2f})")
    if evidence.get("chat_ratio"):
        parts.append(f"chat {evidence['chat_ratio']}x normal")
    if evidence.get("scream_max") and evidence["scream_max"] >= 0.3:
        parts.append("shout/scream")
    if not parts:
        parts.append("speech and activity")
    return "Local estimate: " + "; ".join(parts) + "."


def similar_text(first, second):
    """
    Check whether two transcripts are nearly the same (a repeated moment).

    Args:
        first (str): One transcript.
        second (str): Another.

    Returns:
        bool: True when they share more than 85% of their words.
    """
    words_one = set(re.findall(r"\w+", first.lower()))
    words_two = set(re.findall(r"\w+", second.lower()))
    if len(words_one) < 8 or len(words_two) < 8:
        return False
    return len(words_one & words_two) / len(words_one | words_two) > 0.85

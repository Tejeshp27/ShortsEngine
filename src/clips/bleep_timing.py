# =============================================================
# src/clips/bleep_timing.py — put each bleep exactly on its swear word
#
# The main transcript comes from whisper.cpp, which keeps loud, shouted
# speech well but gives loose word times: a swear word's time can be
# 0.3–0.9 s off and up to a second long. A bleep placed there can miss
# the start of the word and cover the next words instead.
#
# faster-whisper lines its words up with the sound much more precisely.
# So for a finished moment (usually under a minute of audio) this file
# runs faster-whisper once, finds the swear words it heard, and moves
# each bleep onto the matching precise word. A swear word that can't be
# matched keeps its whisper.cpp start but is cut to a realistic length.
#
# Used by maker.prepare_bleeps() before the bleep ranges are worked out.
# Free and local; if faster-whisper is not installed, only the length
# limit is applied.
# =============================================================

import os
import subprocess
import tempfile

from src.clips.profanity import count_letters, find_profanity

# How far (seconds) a precise word may be from the loose one and still be the same word.
MATCH_WINDOW_SECONDS = 1.5


def precise_swear_words(source, start, end, word_list, model_name, language):
    """
    Find the swear words in one moment with faster-whisper's precise word times.

    Args:
        source (str): The video the moment is cut from.
        start (float): The moment's start in the source, in seconds.
        end (float): The moment's end.
        word_list (dict): From profanity.load_word_list().
        model_name (str): The Whisper model, e.g. "small.en".
        language (str): The spoken language, e.g. "en".

    Returns:
        list[dict]: [{"word", "matched", "start", "end"}, ...] with times counted
                    from the moment's start; empty when faster-whisper is missing
                    or fails (bleeping then falls back to the length limit).
    """
    from src.clips.transcribe import faster_whisper_installed, words_from_faster_whisper

    if not faster_whisper_installed():
        return []
    folder = tempfile.mkdtemp(prefix="bleep_timing_")
    wav_path = os.path.join(folder, "moment.wav")
    try:
        subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-ss", f"{start:.3f}",
                        "-t", f"{end - start:.3f}", "-i", source, "-vn", "-ac", "1", "-ar", "16000",
                        wav_path], check=True)
        words = words_from_faster_whisper(wav_path, model_name, language)
    except Exception:
        # Precise timing only improves the bleeps; never stop a render for it.
        return []
    finally:
        if os.path.exists(wav_path):
            os.remove(wav_path)
        os.rmdir(folder)
    found = []
    for word in words:
        root = find_profanity(word["word"], word_list)
        if root is not None:
            found.append({"word": word["word"], "matched": root, "start": word["start"], "end": word["end"]})
    return found


def longest_bleep(word, min_seconds, seconds_per_letter):
    """
    Work out the longest a bleep for this word should last.

    Args:
        word (str): The swear word as said, e.g. "fucking".
        min_seconds (float): The shortest allowed beep.
        seconds_per_letter (float): Speaking time allowed per letter.

    Returns:
        float: For example 7 letters × 0.09 = 0.63 seconds for "fucking".
    """
    return max(min_seconds, count_letters(word) * seconds_per_letter)


def align_bleeps(moment_words, precise, min_seconds, seconds_per_letter):
    """
    Move each flagged swear word onto its precise time, or limit its length.

    Args:
        moment_words (list[dict]): The moment's words (times from its start),
                                   swear words flagged by profanity.mark_profanity().
        precise (list[dict]): From precise_swear_words().
        min_seconds (float): The shortest allowed beep.
        seconds_per_letter (float): Speaking time allowed per letter, for unmatched words.

    Returns:
        list[dict]: New copies of the words. Flagged words get corrected times and
                    "timing": "precise" or "limited". Precise swear words with no
                    loose match (heard only by faster-whisper) are added as extra
                    flagged entries, so they are bleeped too.

    Example:
        A loose "fucking" at 6.61–6.70 with a precise "fucking" at 6.18–6.44
        becomes 6.18–6.44 ("precise").
    """
    unused = list(precise)
    result = []
    for word in moment_words:
        copy = dict(word)
        if not word.get("bleep"):
            result.append(copy)
            continue
        match = None
        best_distance = MATCH_WINDOW_SECONDS
        for candidate in unused:
            if candidate["matched"] != word.get("matched"):
                continue
            distance = abs(candidate["start"] - word["start"])
            if distance <= best_distance:
                match = candidate
                best_distance = distance
        if match is not None:
            unused.remove(match)
            copy["start"] = match["start"]
            copy["end"] = match["end"]
            copy["timing"] = "precise"
        else:
            said = word.get("original", word["word"])
            copy["end"] = min(word["end"], word["start"] + longest_bleep(said, min_seconds, seconds_per_letter))
            copy["timing"] = "limited"
        result.append(copy)
    for extra in unused:
        result.append({"word": extra["word"], "original": extra["word"], "matched": extra["matched"],
                       "bleep": True, "start": extra["start"], "end": extra["end"],
                       "timing": "precise"})
    result.sort(key=word_start)
    return result


def word_start(word):
    """Return a word's start time, used to keep words in speaking order."""
    return word["start"]

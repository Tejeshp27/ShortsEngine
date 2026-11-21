# =============================================================
# src/clips/caption_check.py — catch wrong captions before they are burned in
#
# Speech-to-text (Whisper) makes predictable mistakes on stream audio:
#   - it invents stock phrases over silence or music ("Thank you for
#     watching", "Please subscribe") — documented in research
#     (docs/VIRAL_CONTENT_RESEARCH.md, Cornell 2024 and a 2025 study);
#   - it gets stuck repeating itself ("no no no no no no no");
#   - whisper.cpp sometimes stretches one word over seconds of silence,
#     so its caption lingers after the person stopped talking;
#   - it transcribes game characters / videos as if the streamer said it.
#
# This file removes only words with clear evidence of being wrong (never
# replaces them with guessed words), shortens stretched words, decides
# which other voices belong in captions, and gives the captions a
# confidence score for the quality report.
# =============================================================

import re

HALLUCINATION_PHRASES = [
    "thank you for watching", "thanks for watching", "please subscribe", "subscribe to my channel",
    "like and subscribe", "subtitles by", "transcribed by", "amara.org", "see you in the next video",
    "thank you so much for watching", "don't forget to subscribe",
]

SPEECH_HEARD = 0.05      # below this "speech" chance for every second of a word, nobody is talking
LONGEST_WORD_SECONDS = 1.6
SECONDS_PER_LETTER = 0.09


def plain(text):
    """
    Lower-case a word and strip surrounding punctuation.

    Args:
        text (str): A word.

    Returns:
        str: The bare word.
    """
    return text.strip(" .,!?\"'…-").lower()


def speech_chance(sounds, start, end):
    """
    The highest chance of speech (or laughter) during a time span.

    Args:
        sounds (list[dict]): Per-second sound chances.
        start (float): Span start, in seconds (same timeline as sounds).
        end (float): Span end.

    Returns:
        float or None: 0 to 1, or None when there is no sound data for the span.
    """
    best = None
    for second in range(int(start), int(end) + 1):
        if 0 <= second < len(sounds):
            chances = sounds[second]
            value = max(chances.get("speech", 0.0), chances.get("laughter", 0.0), chances.get("scream", 0.0))
            best = value if best is None else max(best, value)
    return best


def find_phrase_positions(words, phrases):
    """
    Find where any of the given phrases appear in a list of words.

    Args:
        words (list[dict]): Timed words.
        phrases (list[str]): Lower-case phrases.

    Returns:
        set[int]: The positions (indexes) of words that belong to a phrase.
    """
    plain_words = []
    for word in words:
        plain_words.append(plain(word["word"]))
    positions = set()
    for phrase in phrases:
        parts = phrase.split()
        for index in range(len(plain_words) - len(parts) + 1):
            if plain_words[index:index + len(parts)] == parts:
                for offset in range(len(parts)):
                    positions.add(index + offset)
    return positions


def find_repeat_loops(words, max_repeats=2):
    """
    Find words that belong to a stuck repetition loop.

    A short phrase (1–4 words) repeated more than `max_repeats` times in a
    row is a loop; the extra copies are marked for removal (the first
    `max_repeats` copies are kept, since people do say "no no").

    Args:
        words (list[dict]): Timed words.
        max_repeats (int): How many copies to keep.

    Returns:
        set[int]: Indexes of words to remove.

    Example:
        "no no no no no" → indexes 2, 3, 4.
    """
    plain_words = []
    for word in words:
        plain_words.append(plain(word["word"]))
    remove = set()
    index = 0
    while index < len(plain_words):
        found = False
        for size in range(1, 5):
            phrase = plain_words[index:index + size]
            if len(phrase) < size:
                break
            copies = 1
            position = index + size
            while plain_words[position:position + size] == phrase:
                copies += 1
                position += size
            if copies > max_repeats:
                for extra in range(index + size * max_repeats, position):
                    remove.add(extra)
                index = position
                found = True
                break
        if not found:
            index += 1
    return remove


def tidy_word(word):
    """
    Remove leftover joining dashes Whisper puts between speakers ("party.-", "this?-").

    Args:
        word (dict): A timed word.

    Returns:
        dict: A copy with tidy text (unchanged when nothing needed fixing).
    """
    text = re.sub(r"(?<=[.!?,])[-–—]+$", "", word["word"])
    text = re.sub(r"^[-–—]+(?=\w)", "", text)
    text = re.sub(r"[-–—]+$", "", text) if len(text) > 1 else text
    if text == word["word"]:
        return word
    changed = dict(word)
    changed["word"] = text
    return changed


def shorten_long_word(word):
    """
    Shorten a word whose timing was stretched over silence.

    Args:
        word (dict): A timed word.

    Returns:
        tuple: (the word, possibly with an earlier end, True if it was shortened).
    """
    letters = len(plain(word["word"]))
    longest = max(0.35, min(LONGEST_WORD_SECONDS, 0.25 + SECONDS_PER_LETTER * letters))
    if word["end"] - word["start"] <= longest:
        return word, False
    changed = dict(word)
    changed["end"] = round(word["start"] + longest, 3)
    return changed, True


def check_captions(words, sounds, sound_offset=0.0):
    """
    Clean a moment's caption words and measure how trustworthy they are.

    Args:
        words (list[dict]): Timed words (any timeline).
        sounds (list[dict]): Per-second sound chances of the SOURCE recording.
        sound_offset (float): Add this to a word's time to find it in `sounds`
                              (0 when words are already in source time).

    Returns:
        tuple: (cleaned words, report dict with counts, "confidence" 0–1 and notes).
    """
    hallucinated = find_phrase_positions(words, HALLUCINATION_PHRASES)
    loops = find_repeat_loops(words)
    kept = []
    removed_hallucinations = 0
    removed_unheard = 0
    removed_repeats = 0
    shortened = 0
    unheard_run = []
    for index, word in enumerate(words):
        chance = None
        if sounds:
            chance = speech_chance(sounds, word["start"] + sound_offset, word["end"] + sound_offset)
        if index in hallucinated and (chance is None or chance < 0.4):
            removed_hallucinations += 1
            continue
        if index in loops:
            removed_repeats += 1
            continue
        if chance is not None and chance < SPEECH_HEARD:
            # Only remove runs of 2+ unheard words; one quiet word is more likely real.
            unheard_run.append(word)
            continue
        if len(unheard_run) == 1:
            kept.append(shorten_long_word(unheard_run[0])[0])
        else:
            removed_unheard += len(unheard_run)
        unheard_run = []
        word, was_shortened = shorten_long_word(tidy_word(word))
        if was_shortened:
            shortened += 1
        kept.append(word)
    if len(unheard_run) == 1:
        kept.append(shorten_long_word(unheard_run[0])[0])
    else:
        removed_unheard += len(unheard_run)
    total = max(1, len(words))
    removed = removed_hallucinations + removed_unheard + removed_repeats
    confidence = 1.0 - min(1.0, removed / total * 2) - min(0.3, shortened / total)
    if not sounds:
        confidence -= 0.1   # no independent speech evidence to check against
    notes = []
    if removed_hallucinations:
        notes.append(f"removed {removed_hallucinations} word(s) of a known Whisper phrase heard over no speech")
    if removed_repeats:
        notes.append(f"removed {removed_repeats} word(s) of a repetition loop")
    if removed_unheard:
        notes.append(f"removed {removed_unheard} word(s) where no speech was heard")
    if shortened:
        notes.append(f"shortened {shortened} stretched word time(s) so captions do not linger")
    report = {"words_in": len(words), "words_out": len(kept), "removed_hallucinations": removed_hallucinations,
              "removed_repeats": removed_repeats, "removed_unheard": removed_unheard,
              "shortened_words": shortened, "confidence": round(max(0.0, confidence), 2), "notes": notes}
    return kept, report


def music_share(sounds, start, end):
    """
    How much of a time span has music playing (a hint of game/video dialogue).

    Args:
        sounds (list[dict]): Per-second sound chances.
        start (float): Span start (source time).
        end (float): Span end.

    Returns:
        float: 0 to 1.
    """
    seconds = 0
    musical = 0
    for second in range(int(start), int(end) + 1):
        if 0 <= second < len(sounds):
            seconds += 1
            if sounds[second].get("music", 0.0) >= 0.4:
                musical += 1
    return musical / seconds if seconds else 0.0


def choose_other_voices(marked, mode, sounds, sound_offset=0.0, other_voices_matter=False, reply_seconds=2.0):
    """
    Decide which words from voices other than the streamer appear in captions.

    The streamer's words are always kept. Another voice is kept in
    "exchange" mode when it is part of a back-and-forth with the streamer
    (the streamer speaks within `reply_seconds` after it), it is not
    over music (likely a game cutscene or video), or the editor model said
    the other voice matters for the joke. "none" keeps only the streamer;
    "all" keeps everything. Uncertain words (no "speaker" mark) are kept.

    Args:
        marked (list[dict]): Words with "speaker": "streamer" | "other" (speaker_filter).
        mode (str): "exchange", "none" or "all".
        sounds (list[dict]): Per-second sound chances.
        sound_offset (float): Add to word times to reach source time.
        other_voices_matter (bool): The editor model's judgement.
        reply_seconds (float): How quickly the streamer must answer.

    Returns:
        tuple: (kept words, number of other-voice words kept, number dropped).
    """
    if mode == "all":
        return list(marked), 0, 0
    runs = []
    current = []
    for word in marked:
        if word.get("speaker") == "other":
            current.append(word)
        elif current:
            runs.append(current)
            current = []
    if current:
        runs.append(current)
    keep_ids = set()
    if mode == "exchange":
        for run in runs:
            run_end = run[-1]["end"]
            answered = False
            for word in marked:
                if word.get("speaker") != "other" and run_end <= word["start"] <= run_end + reply_seconds:
                    answered = True
                    break
            over_music = sounds and music_share(sounds, run[0]["start"] + sound_offset,
                                                run_end + sound_offset) >= 0.5
            if (answered and not over_music) or other_voices_matter:
                for word in run:
                    keep_ids.add(id(word))
    kept = []
    kept_other = 0
    dropped = 0
    for word in marked:
        if word.get("speaker") != "other":
            kept.append(word)
        elif id(word) in keep_ids:
            kept.append(word)
            kept_other += 1
        else:
            dropped += 1
    return kept, kept_other, dropped


def caption_words_per_second(words):
    """
    Reading speed of the captions (TikTok's guidance: 5–10 words per second at most).

    Args:
        words (list[dict]): Caption words.

    Returns:
        float: Peak words per second over any 2-second stretch.
    """
    peak = 0.0
    for index, word in enumerate(words):
        count = 0
        for other in words[index:]:
            if other["start"] < word["start"] + 2.0:
                count += 1
            else:
                break
        peak = max(peak, count / 2.0)
    return peak


def has_letters(text):
    """
    Check that a caption word contains at least one letter or digit.

    Args:
        text (str): A word.

    Returns:
        bool: True for real words.
    """
    return re.search(r"[A-Za-z0-9]", text) is not None

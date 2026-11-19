# =============================================================
# src/clips/profanity.py — find swear words in a transcript and
#                          decide exactly when to bleep them
#
# How it fits in: the clip maker (maker.py) gets the timed words
# from Whisper, asks this file which ones are profanity, and then:
#   - shows them masked in the burned-in captions ("f******")
#   - passes their times to the renderer, which mutes the voice
#     there and plays a 1 kHz beep instead (clip_audio.py)
#   - writes a bleep log next to the video so you can review it
#
# The word list is a YAML file you can edit (config/profanity.yaml).
# Everything here is plain text and number work, so it is easy to
# test without any audio or video.
# =============================================================

import json
import os
import re

import yaml

# Characters people swap in for letters ("sh1t", "a$$").
LOOKALIKE_LETTERS = {"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "@": "a", "$": "s"}

# Punctuation that may sit at the start or end of a transcript word.
# It is removed before matching and put back when masking.
EDGE_PUNCTUATION = ".,!?;:\"'()[]{}…-–—"

# A spoken word takes roughly this long per letter ("fucking" ≈ 0.5 s).
# Whisper sometimes reports a word as almost zero seconds long; the bleep
# is then widened to this estimate, so the word can't slip out around it.
SECONDS_PER_LETTER = 0.07

# Loaded word lists, keyed by file path and its last-changed time,
# so editing the YAML takes effect without restarting anything.
_loaded_lists = {}


def letters_pattern(root):
    """
    Turn a plain word into a pattern that also accepts stretched letters.

    Each letter may be repeated ("fuuuck"), but a double letter must
    stay at least double, so "ass" does not turn into "as".

    Args:
        root (str): A lowercase word such as "ass".

    Returns:
        str: A regular-expression pattern, e.g. "a+s{2,}".

    Example:
        letters_pattern("fuck")  # → "f+u+c+k+"
    """
    pattern = ""
    index = 0
    while index < len(root):
        letter = root[index]
        run_length = 1
        while index + run_length < len(root) and root[index + run_length] == letter:
            run_length += 1
        if run_length == 1:
            pattern += re.escape(letter) + "+"
        else:
            pattern += re.escape(letter) + "{" + str(run_length) + ",}"
        index += run_length
    return pattern


def load_word_list(path):
    """
    Read the editable profanity YAML file and prepare it for matching.

    Args:
        path (str): For example "config/profanity.yaml".

    Returns:
        dict: {"whole": [(root, pattern)], "inside": [(root, pattern)],
               "allow": set of words, "forms": [(form, root)]}.
               "forms" lists every plain spelling (root + ending), used
               to read censored words like "f**k".

    Raises:
        ValueError: If the file is missing or has no words.
    """
    if not os.path.exists(path):
        raise ValueError(f"Profanity list not found: {path}. Check bleep.word_list in the channel YAML.")
    changed_at = os.path.getmtime(path)
    cache_key = (os.path.abspath(path), changed_at)
    if cache_key in _loaded_lists:
        return _loaded_lists[cache_key]

    with open(path, "r", encoding="utf-8") as file:
        data = yaml.safe_load(file) or {}
    suffixes = []
    for suffix in data.get("suffixes", []):
        suffixes.append(re.escape(str(suffix).lower()))
    # "(s|es|ing)?" means "optionally one of these endings".
    ending = ""
    if suffixes:
        ending = "(" + "|".join(suffixes) + ")?"

    word_list = {"whole": [], "inside": [], "allow": set(), "forms": []}
    for root in data.get("words", []):
        root = str(root).lower()
        word_list["whole"].append((root, re.compile("^" + letters_pattern(root) + ending + "$")))
        add_plain_forms(word_list["forms"], root, data.get("suffixes", []))
    for root in data.get("match_inside", []):
        root = str(root).lower()
        word_list["inside"].append((root, re.compile(letters_pattern(root))))
        add_plain_forms(word_list["forms"], root, data.get("suffixes", []))
    for allowed in data.get("allow", []):
        word_list["allow"].add(str(allowed).lower())
    if not word_list["whole"] and not word_list["inside"]:
        raise ValueError(f"The profanity list {path} has no words.")

    _loaded_lists[cache_key] = word_list
    return word_list


def add_plain_forms(forms, root, suffixes):
    """
    Add a root word and each "root + ending" spelling to a list.

    Args:
        forms (list): The list to add (form, root) pairs to.
        root (str): A lowercase word, e.g. "shit".
        suffixes (list): Endings from the YAML, e.g. ["s", "ing"].
    """
    forms.append((root, root))
    for suffix in suffixes:
        forms.append((root + str(suffix).lower(), root))


def split_punctuation(word):
    """
    Separate a transcript word from the punctuation around it.

    Args:
        word (str): For example '"Fuck!"'.

    Returns:
        tuple: (leading punctuation, the word itself, trailing punctuation),
               e.g. ('"', "Fuck", '!"').
    """
    start = 0
    while start < len(word) and word[start] in EDGE_PUNCTUATION:
        start += 1
    end = len(word)
    while end > start and word[end - 1] in EDGE_PUNCTUATION:
        end -= 1
    return word[:start], word[start:end], word[end:]


def normalize_word(word):
    """
    Prepare a transcript word for matching: lowercase, no punctuation, no look-alikes.

    Args:
        word (str): For example "Sh1t!" or "fuckin'".

    Returns:
        str: For example "shit" or "fuckin". Censor stars ("f**k") are kept.
    """
    _, core, _ = split_punctuation(word.lower())
    cleaned = ""
    for character in core:
        character = LOOKALIKE_LETTERS.get(character, character)
        # Keep letters and censor stars; drop apostrophes, hyphens etc.
        if character.isalpha() or character == "*":
            cleaned += character
    return cleaned


def find_profanity(word, word_list):
    """
    Check one transcript word against the profanity list.

    Args:
        word (str): One word from Whisper, e.g. "Motherfucker," or "f**k".
        word_list (dict): From load_word_list().

    Returns:
        str or None: The listed root that matched (e.g. "fuck"), or None.

    Example:
        find_profanity("SHIIITTY", word_list)  # → "shit"
        find_profanity("class", word_list)     # → None
    """
    cleaned = normalize_word(word)
    if not cleaned or cleaned in word_list["allow"]:
        return None

    # A censored transcript word: each star stands for one letter.
    if "*" in cleaned:
        star_pattern = re.compile("^" + re.escape(cleaned).replace(r"\*", "[a-z]") + "$")
        for form, root in word_list["forms"]:
            if star_pattern.match(form):
                return root
        return None

    for root, pattern in word_list["whole"]:
        if pattern.match(cleaned):
            return root
    for root, pattern in word_list["inside"]:
        if pattern.search(cleaned):
            return root
    return None


def mask_word(word):
    """
    Hide a swear word for on-screen text, keeping its first letter and punctuation.

    Args:
        word (str): For example "Fucking,".

    Returns:
        str: For example "F******,".
    """
    before, core, after = split_punctuation(word)
    if len(core) <= 1:
        return before + "*" * len(core) + after
    return before + core[0] + "*" * (len(core) - 1) + after


def mark_profanity(words, word_list, mask_captions=True):
    """
    Flag every swear word in a list of timed words.

    Args:
        words (list[dict]): [{"word", "start", "end"}, ...] from Whisper.
        word_list (dict): From load_word_list().
        mask_captions (bool): True replaces the shown text with a masked version.

    Returns:
        list[dict]: New copies of the words. Matches get "bleep": True,
                    "original" (what was said) and "matched" (the listed root).
                    The input list is not changed.
    """
    marked = []
    for word in words:
        copy = dict(word)
        root = find_profanity(word["word"], word_list)
        if root is not None:
            copy["bleep"] = True
            copy["original"] = word["word"]
            copy["matched"] = root
            if mask_captions:
                copy["word"] = mask_word(word["word"])
        marked.append(copy)
    return marked


def mask_text(text, word_list):
    """
    Mask every swear word in a piece of plain text (for post captions).

    Args:
        text (str): For example "What the fuck, bro".
        word_list (dict): From load_word_list().

    Returns:
        str: For example "What the f***, bro". Line breaks are kept.
    """
    # re.split with a group "(\s+)" keeps the spaces/newlines between
    # words, so the text can be put back together unchanged.
    pieces = re.split(r"(\s+)", text)
    result = ""
    for piece in pieces:
        if piece.strip() and find_profanity(piece, word_list):
            result += mask_word(piece)
        else:
            result += piece
    return result


def bleep_spans(words, padding_seconds, duration, min_seconds):
    """
    Work out the exact time ranges to bleep in a video.

    Each flagged word is widened by `padding_seconds` on both sides
    (Whisper's word times are close, not perfect). A word reported as
    shorter than it could really be spoken is widened around its middle
    to at least `min_seconds`, and to about SECONDS_PER_LETTER per
    letter, so the beep covers the whole word. Ranges that touch or
    overlap are joined into one beep.

    Args:
        words (list[dict]): Timed words for this video (times start at 0),
                            from mark_profanity().
        padding_seconds (float): Extra time on each side, e.g. 0.04.
        duration (float): The video's length; no range may go past it.
        min_seconds (float): The shortest allowed beep, e.g. 0.15.

    Returns:
        list[dict]: [{"start": s, "end": s, "words": ["fuck", ...]}, ...]
                    in time order.

    Example:
        bleep_spans([{"word": "f***", "original": "fuck", "bleep": True,
                      "start": 1.0, "end": 1.3}], 0.04, 10, 0.15)
        # → [{"start": 0.96, "end": 1.34, "words": ["fuck"]}]
        # (0.38 s is longer than 4 letters × 0.07 s, so no extra widening)
    """
    spans = []
    for word in words:
        if not word.get("bleep"):
            continue
        said = word.get("original", word["word"])
        start = word["start"] - padding_seconds
        end = word["end"] + padding_seconds
        shortest = max(min_seconds, count_letters(said) * SECONDS_PER_LETTER)
        if end - start < shortest:
            middle = (start + end) / 2
            start = middle - shortest / 2
            end = middle + shortest / 2
        start = max(0.0, start)
        end = min(duration, end)
        if end <= start:
            continue
        if spans and start <= spans[-1]["end"]:
            # Overlaps the previous beep: make that beep longer instead.
            spans[-1]["end"] = max(spans[-1]["end"], end)
            spans[-1]["words"].append(said)
        else:
            spans.append({"start": start, "end": end, "words": [said]})

    for span in spans:
        span["start"] = round(span["start"], 3)
        span["end"] = round(span["end"], 3)
    return spans


def count_letters(word):
    """
    Count the letters in a word, ignoring punctuation and symbols.

    Args:
        word (str): For example "Fuck,".

    Returns:
        int: For example 4.
    """
    total = 0
    for character in word:
        if character.isalpha():
            total += 1
    return total


def write_review_files(video_path, words, spans, settings_summary):
    """
    Save the transcript and the bleep log next to a finished video.

    Args:
        video_path (str): The finished MP4, e.g. "output/videos/x/clip.mp4".
        words (list[dict]): This video's timed words (times start at 0),
                            with bleep flags from mark_profanity().
        spans (list[dict]): From bleep_spans().
        settings_summary (dict): The bleep settings used, for the record.

    Returns:
        tuple: (transcript path, bleep log path).
    """
    base = os.path.splitext(video_path)[0]
    transcript_path = base + ".transcript.json"
    log_path = base + ".bleeps.json"

    spoken = []
    for word in words:
        spoken.append(word.get("original", word["word"]))
    transcript = {"text": " ".join(spoken), "words": words}

    bleeped_words = []
    for word in words:
        if word.get("bleep"):
            bleeped_words.append({"said": word["original"], "shown_as": word["word"],
                                  "matched": word["matched"], "start": word["start"], "end": word["end"]})
    log = {"settings": settings_summary, "bleep_count": len(spans),
           "words": bleeped_words, "muted_ranges": spans}

    for path, content in ((transcript_path, transcript), (log_path, log)):
        with open(path + ".part", "w", encoding="utf-8") as file:
            json.dump(content, file, indent=2)
        os.replace(path + ".part", path)
    return transcript_path, log_path

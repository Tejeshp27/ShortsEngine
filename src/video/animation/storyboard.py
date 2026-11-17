# =============================================================
# src/video/animation/storyboard.py — plan what appears on screen
#
# A storyboard is the plan for an animated video. It says, for
# every few seconds of speech (a "beat"):
#
#   - which stock footage plays in the background, and
#   - which animated graphic is drawn on top, and what it says.
#
# Steps in this file:
#   1. Read the script and split the spoken text into beats
#      (one sentence, or part of a long sentence).
#   2. Ask the cheap AI model to choose footage and a graphic
#      for every beat. This is ONE small API call (about 1 cent).
#   3. Check the AI's answer. The AI is never trusted blindly:
#      on-screen words must come from the sentence being spoken,
#      numbers and names must be in the script, and a claim must
#      keep its "says" / "argues". Anything that breaks a rule is
#      replaced with a plain headline made of the spoken words.
#   4. Save the result next to the script as <name>_storyboard.json
#      so it can be reviewed, edited by hand, and reused for free.
#
# How it fits in:
#   renderer.py → get_storyboard() → add_scene_times()
# =============================================================

import json
import os
import re

from src.generation.sections import is_spoken_section


# A beat longer than this many words is split in two, so the picture
# changes every few seconds (people speak about 2.5 words per second).
MAX_WORDS_PER_BEAT = 8

# Never create a beat shorter than this when splitting a long sentence.
MIN_WORDS_PER_BEAT = 3

# Graphics start this many seconds before their first word is spoken,
# so the animation lands at the same moment as the voice.
SCENE_LEAD_SECONDS = 0.1

# A beat with this many words or fewer is shown in full when the plain
# fallback headline is used; longer beats are shortened.
MAX_FALLBACK_WORDS = 6

# Bump this if the storyboard file format ever changes.
STORYBOARD_VERSION = 2

# Short forms that end in a full stop but do not end a sentence.
ABBREVIATIONS = {"mr.", "mrs.", "ms.", "dr.", "rep.", "sen.", "gov.", "gen.",
                 "st.", "vs.", "jr.", "sr.", "no."}

# Words where a long sentence can be split if it has no comma.
JOINING_WORDS = {"and", "but", "because", "which", "while", "after", "before",
                 "since", "by", "for", "without", "until", "who", "that"}

# Weaker places to split: small linking words. These are only used when
# there is no comma or joining word near the middle of the sentence.
LINKING_WORDS = {"to", "in", "on", "from", "with", "at", "into", "over"}

# Common words skipped when building a plain fallback headline.
FILLER_WORDS = {"the", "a", "an", "is", "are", "was", "were", "of", "to", "in",
                "on", "for", "and", "but", "that", "this", "his", "her", "its",
                "he", "she", "it", "has", "have", "with", "from", "by", "as",
                "at", "be", "been", "will", "which", "who"}

# Small grammar words a graphic may use even if the sentence does not.
# They carry no facts. Words that change meaning ("not", "no", "never")
# are deliberately NOT in this list.
FREE_SCREEN_WORDS = {"the", "a", "an", "of", "to", "in", "on", "and", "or",
                     "is", "are", "vs", "for", "by", "at", "with", "from",
                     "as", "it", "its", "his", "her", "their", "this", "that",
                     "be", "was", "were", "has", "have", "will", "he", "she",
                     "they", "who", "what", "why", "how"}

# Words that mark something as a claim, a prediction or a possibility
# rather than a plain fact. If a beat uses one, the graphic must keep it —
# otherwise "Massie argues the law was broken" would appear on screen as
# "THE LAW WAS BROKEN", which states an accusation as fact.
ATTRIBUTION_WORDS = {"argues", "argue", "argued", "says", "say", "said",
                     "claims", "claim", "claimed", "alleges", "alleged",
                     "allegedly", "accuses", "accused", "believes", "thinks",
                     "according", "reportedly", "expected", "could", "may",
                     "might"}

# Symbols allowed directly before or after a counting number.
NUMBER_SYMBOLS = {"$", "%", "+", "-", "x", "K", "M", "B", "£", "€"}

# Emoji that must never be used. Calendar emoji show a fixed date
# ("July 17"), which would look like a fact we never stated.
BLOCKED_EMOJI = {"📅", "📆", "🗓️", "🗓"}

# Number words understood when checking that a number is in the script.
NUMBER_WORDS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
    "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
    "seventy": 70, "eighty": 80, "ninety": 90, "hundred": 100,
    "thousand": 1000, "million": 1000000, "billion": 1000000000,
}

# The instructions sent to the AI on every storyboard call. This text
# never changes between videos; only the list of beats does.
STORYBOARD_SYSTEM_PROMPT = """You are a motion designer planning a short vertical video (TikTok / Instagram Reels).

The narration is already written and split into numbered beats. A beat is a few seconds of speech. For EVERY beat you choose two things:

1. "footage": the stock video clip that plays full-screen behind everything.
2. One animated graphic "template" drawn on top of the footage.

FOOTAGE RULES
- If the message gives a list of approved footage topics, "footage" must be copied exactly from that list. Pick the topic that best fits what the beat is about.
- If there is no list, write 1 or 2 plain words for something concrete a camera can film at the place the story happens (a building, a flag, a street). Never a person's name, and never a metaphor or symbol.
- Change the footage from beat to beat so the picture keeps changing.

TEMPLATES (use these exact field names)
- headline: {"template":"headline","text":"2-6 words","highlight":"one word from text","emoji":"optional"}. Words slam in one at a time. Use for the hook and for punchy statements.
- icon_label: {"template":"icon_label","emoji":"one emoji","title":"1-4 words","subtitle":"optional, up to 7 words"}. A big icon with a title. Use for places, institutions, things and actions.
- person: {"template":"person","name":"Full Name","role":"up to 5 words"}. A name card. Use the first time a beat names a person. Copy the name exactly; the role must be stated in the script.
- big_number: {"template":"big_number","value":90,"prefix":"","suffix":"","label":"up to 6 words","emoji":"optional"}. A number that counts up. Only for a number written in the beat. Put the unit in the label ("days without approval"), not in the suffix. Not for years or dates.
- versus: {"template":"versus","left_emoji":"one emoji","left_label":"1-5 words","right_emoji":"one emoji","right_label":"1-5 words"}. Two different sides or options named in the beat.
- bullet_list: {"template":"bullet_list","title":"1-4 words","items":[{"emoji":"one emoji","text":"2-6 words"}]}. 2 or 3 items that appear one by one. Use when a beat makes several points.
- timeline: {"template":"timeline","title":"1-4 words","steps":["2-5 words","2-5 words"]}. 2 or 3 steps in order. Use for a sequence of events.
- bar_chart: {"template":"bar_chart","title":"1-4 words","unit":"","bars":[{"label":"1-2 words","value":10}]}. 2 to 4 bars. Only when the beat contains two or more numbers that can be compared.
- stamp: {"template":"stamp","text":"1-4 words","emoji":"optional"}. Words stamped onto the screen. Use for one strong fact the narrator states directly, such as "EXTREMELY RARE".
- analogy: {"template":"analogy","left_emoji":"one emoji","left_label":"1-5 words: the real thing","right_emoji":"one emoji","right_label":"1-5 words: the everyday thing"}. For a beat that compares something to everyday life ("think of it like...", "it's like..."). Shows the real thing, then "IS LIKE", then the comparison. right_label uses words from this beat's sentence; left_label may use words from anywhere in the script.

PHOTO (optional, any template)
- Add "photo": "Exact Name" when the beat names a specific real person, organization, building or place that has its own Wikipedia article, for example "Scott Bessent", "Federal Aviation Administration", "United States Capitol". Copy the name exactly as the script writes it, or its full official name if the script writes the full name somewhere. A real photo is shown if a freely licensed one exists.
- Never add a photo for general things ("investors", "airports"), for victims of crimes or tragedies, or for anything not named in the script.

ACCURACY RULES (the most important rules; graphics that break them are thrown away)
- Build all on-screen text from words in that beat's own sentence. Do not add any fact, number, name, date or conclusion, even one that seems obviously true.
- Never turn a claim into a fact. If the beat says someone "argues", "says" or "said" something, or that something is "expected" or "could" happen, the on-screen text must include that same word. It only needs to appear once in the graphic. Example: for the beat "Smith argues the plan is illegal", write "Smith argues: plan is illegal", never "Plan is illegal".
- Stay neutral. No opinions, and no dramatic words that the narration does not use.

VARIETY RULES
- Beat 1 must be a headline that hooks the viewer.
- The spoken words already appear as captions, so graphics should SHOW things (icons, numbers, names, comparisons) rather than repeat the sentence. Use headline for at most one beat in every five. Never use the same template twice in a row.
- Every beat containing an analogy must use the analogy template.
- Use at least six different templates across the video.
- Every emoji field holds exactly one common emoji that plainly shows the thing being talked about. Never use calendar emoji.

OUTPUT
Reply with ONLY a JSON array holding one object per beat, in order. Each object has "beat" (the beat number), "footage", and the fields of the template you chose. No explanation and no markdown."""


def read_script_sections(script_path):
    """
    Read a script file and return the spoken text of each section.

    Args:
        script_path (str): Path to the .md script file.

    Returns:
        list[dict]: One {"section": str, "text": str} per spoken section,
                    in the order they are read aloud.

    Example:
        sections = read_script_sections("output/news/2026-11-09-story.md")
        sections[0]   # → {"section": "Hook", "text": "A Republican ..."}
    """
    sections = []
    current_section = None
    current_lines = []

    with open(script_path, "r", encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()

            # Lines starting with "## " are section headings.
            if stripped.startswith("## "):
                if current_section is not None:
                    sections.append({"section": current_section,
                                     "text": " ".join(current_lines)})
                section_name = stripped[3:].strip()
                if is_spoken_section(section_name):
                    current_section = section_name
                else:
                    current_section = None
                current_lines = []
                continue

            if current_section is not None and stripped and stripped != "---":
                current_lines.append(stripped)

    if current_section is not None:
        sections.append({"section": current_section, "text": " ".join(current_lines)})

    return sections


def _ends_sentence(word):
    """
    Decide whether a word is the last word of a sentence.

    Args:
        word (str): One word with its punctuation, e.g. "cabinet." or "Dr."

    Returns:
        bool: True if a sentence ends after this word.
    """
    # Ignore closing quotes or brackets after the punctuation.
    stripped = word.rstrip("\"'”’)")
    if not stripped or stripped[-1] not in ".!?":
        return False
    if stripped.lower() in ABBREVIATIONS:
        return False
    # Initials such as "U.S." contain two or more dots in a short word.
    if len(stripped) <= 5 and stripped.count(".") >= 2:
        return False
    return True


def _split_sentences(words):
    """
    Group a list of words into sentences.

    Args:
        words (list[str]): Words in spoken order, with punctuation.

    Returns:
        list[list[str]]: One list of words per sentence.
    """
    sentences = []
    current = []
    for word in words:
        current.append(word)
        if _ends_sentence(word):
            sentences.append(current)
            current = []
    if current:
        sentences.append(current)
    return sentences


def _find_split_position(words):
    """
    Find the best place to cut a long sentence into two parts.

    We prefer to cut just after a comma near the middle. If there is no
    comma, we cut just before a joining word such as "and" or "because",
    or failing that a small linking word such as "to" or "in". If there
    is none of those, we cut exactly in the middle.

    Args:
        words (list[str]): The words of one long sentence.

    Returns:
        int: The index where the second part begins.
    """
    middle = len(words) / 2
    best_position = None
    best_score = None

    for position in range(MIN_WORDS_PER_BEAT, len(words) - MIN_WORDS_PER_BEAT + 1):
        previous_word = words[position - 1]
        after_comma = previous_word[-1] in ",;:—"
        before_joining_word = words[position].lower() in JOINING_WORDS
        before_linking_word = words[position].lower() in LINKING_WORDS

        if not after_comma and not before_joining_word and not before_linking_word:
            continue

        # A lower score is better: close to the middle wins. A comma is
        # worth being two words further from the middle, and a linking
        # word is a slightly worse place to cut than a joining word.
        score = abs(position - middle)
        if after_comma:
            score -= 2
        elif not before_joining_word:
            score += 1.5
        if best_score is None or score < best_score:
            best_score = score
            best_position = position

    if best_position is None:
        return len(words) // 2
    return best_position


def _split_long_sentence(words, max_words):
    """
    Cut a sentence into pieces of at most max_words words.

    Args:
        words (list[str]): The words of one sentence.
        max_words (int): The most words allowed in a piece.

    Returns:
        list[list[str]]: The pieces, in order.
    """
    if len(words) <= max_words or len(words) < MIN_WORDS_PER_BEAT * 2:
        return [words]

    position = _find_split_position(words)
    # This function calls itself on each half ("recursion"), so a very
    # long sentence keeps being halved until every piece is short enough.
    first_half = _split_long_sentence(words[:position], max_words)
    second_half = _split_long_sentence(words[position:], max_words)
    return first_half + second_half


def split_into_beats(sections, max_words=MAX_WORDS_PER_BEAT):
    """
    Split the spoken script into beats — the units the video is cut on.

    Each beat is a sentence, or part of a long sentence. Every beat
    remembers where its words sit in the full script, so it can later be
    matched to the voiceover timing.

    Args:
        sections (list[dict]): Output of read_script_sections().
        max_words (int): The most words allowed in one beat.

    Returns:
        list[dict]: One dict per beat:
            {"section": str, "text": str, "sentence": str,
             "word_start": int, "word_count": int}
            "sentence" is the full sentence the beat was cut from. It is
            used to check that on-screen words really come from the script.

    Example:
        beats = split_into_beats([{"section": "Hook", "text": "Big news. Here it is."}])
        # → two beats: "Big news." (words 0–1) and "Here it is." (words 2–4)
    """
    beats = []
    word_position = 0

    for section in sections:
        section_beats = []
        for sentence in _split_sentences(section["text"].split()):
            for piece in _split_long_sentence(sentence, max_words):
                # A very short piece (e.g. "Why?") joins the beat before it,
                # because a graphic shown for under a second is unreadable.
                if len(piece) < MIN_WORDS_PER_BEAT and section_beats:
                    previous = section_beats[-1]
                    previous["text"] = previous["text"] + " " + " ".join(piece)
                    previous["word_count"] += len(piece)
                else:
                    section_beats.append({
                        "section": section["section"],
                        "text": " ".join(piece),
                        "sentence": " ".join(sentence),
                        "word_start": word_position,
                        "word_count": len(piece),
                    })
                word_position += len(piece)
        beats.extend(section_beats)

    return beats


def build_storyboard_message(beats, channel_config):
    """
    Build the message that lists this video's beats for the AI.

    Args:
        beats (list[dict]): Output of split_into_beats().
        channel_config (dict): The channel config (for niche, audience,
                               tone, and the approved footage topics).

    Returns:
        str: The user message to send with STORYBOARD_SYSTEM_PROMPT.
    """
    lines = [
        f"Channel: {channel_config.get('name', '')} — {channel_config.get('niche', '')}",
        f"Audience: {channel_config.get('audience', '')}",
        f"Tone: {channel_config.get('tone', '')}",
        "",
    ]

    animation = channel_config.get("video", {}).get("animation", {})
    footage_topics = animation.get("footage_topics", {})
    if footage_topics:
        lines.append("Approved footage topics: " + ", ".join(footage_topics.keys()))
        lines.append("")

    lines.append("Beats:")
    for beat_number, beat in enumerate(beats, start=1):
        lines.append(f"{beat_number}. [{beat['section']}] {beat['text']}")
    return "\n".join(lines)


def _clean_word(word):
    """
    Lower-case a word and strip punctuation, for comparing words.

    A possessive ending is removed too, so "Hegseth's" matches "Hegseth".

    Args:
        word (str): e.g. "Impeach," or "Party's".

    Returns:
        str: e.g. "impeach" or "party".
    """
    cleaned = word.lower().strip(".,;:!?\"'“”‘’()-—")
    for ending in ("'s", "’s"):
        if cleaned.endswith(ending):
            cleaned = cleaned[:-2]
    return cleaned


def _clean_words(text):
    """
    Split text into cleaned words (lower-case, no punctuation).

    Args:
        text (str): Any text.

    Returns:
        list[str]: The cleaned words, with empty results left out.
    """
    words = []
    for word in text.split():
        cleaned = _clean_word(word)
        if cleaned:
            words.append(cleaned)
    return words


def find_unsupported_words(text, allowed_words):
    """
    List the words in on-screen text that the script does not back up.

    A word is backed up if it is a small grammar word, if it appears in
    allowed_words, or if it shares its first five letters with an allowed
    word (so "impeaching" is accepted when the script says "impeach").

    Args:
        text (str): Text a graphic wants to show.
        allowed_words (list[str]): Cleaned words from the script.

    Returns:
        list[str]: The words that are not backed up. Empty means all good.

    Example:
        find_unsupported_words("Democrats won", ["republicans", "lost"])
        # → ["democrats", "won"]
    """
    unsupported = []

    for word in _clean_words(text):
        if word in FREE_SCREEN_WORDS or word in allowed_words:
            continue
        # A lone number is checked separately, against the spoken numbers.
        if word.replace(",", "").replace(".", "").isdigit():
            continue

        supported = False
        if len(word) >= 5:
            for allowed_word in allowed_words:
                if len(allowed_word) >= 5 and word[:5] == allowed_word[:5]:
                    supported = True
                    break

        if not supported and word not in unsupported:
            unsupported.append(word)

    return unsupported


def find_attribution_words(text):
    """
    List the words in a text that mark a claim or a prediction.

    Args:
        text (str): Spoken text or on-screen text.

    Returns:
        list[str]: e.g. ["argues"] or ["expected"]. Empty if none.
    """
    found = []
    for word in _clean_words(text):
        if word in ATTRIBUTION_WORDS:
            found.append(word)
    return found


def _as_text(value, max_words):
    """
    Turn a value from the AI's answer into clean, short text.

    Args:
        value: Anything — hopefully a string.
        max_words (int): Extra words beyond this are cut off.

    Returns:
        str: The cleaned text, or "" if the value was not usable text.
    """
    if not isinstance(value, str):
        return ""
    words = value.split()
    return " ".join(words[:max_words])


def _clean_emoji(value):
    """
    Check that a value looks like a single emoji.

    Args:
        value: Anything — hopefully a one-emoji string.

    Returns:
        str: The emoji, or "" if the value is not a usable emoji.
    """
    if not isinstance(value, str):
        return ""
    emoji = value.strip()
    if not emoji or len(emoji) > 8 or emoji in BLOCKED_EMOJI:
        return ""
    # Real emoji never contain ordinary letters or digits.
    for character in emoji:
        if character.isascii() and character.isalnum():
            return ""
    return emoji


def find_numbers_in_text(text):
    """
    List every number mentioned in a piece of text.

    Understands digits ("90", "27,000", "3.5") and number words
    ("two", "ninety").

    Args:
        text (str): Some spoken text.

    Returns:
        list[float]: The numbers found.

    Example:
        find_numbers_in_text("within two days, for more than 90 days")
        # → [90.0, 2.0]
    """
    numbers = []

    # Digits, optionally with thousands commas and a decimal part.
    for match in re.findall(r"\d[\d,]*(?:\.\d+)?", text):
        numbers.append(float(match.replace(",", "")))

    for word in text.split():
        cleaned = _clean_word(word)
        if cleaned in NUMBER_WORDS:
            numbers.append(float(NUMBER_WORDS[cleaned]))

    return numbers


def _to_number(value):
    """
    Turn a value from the AI's answer into a number.

    Args:
        value: A number, or text such as "27,000".

    Returns:
        float or None: The number, or None if it cannot be read as one.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.replace(",", "").strip())
        except ValueError:
            return None
    return None


def make_fallback_fields(beat):
    """
    Build a plain headline graphic straight from a beat's own words.

    Used whenever the AI's choice for a beat is missing or invalid, and
    for every beat when no AI is available. It can never be inaccurate,
    because it only repeats words that are spoken.

    Args:
        beat (dict): One beat from split_into_beats().

    Returns:
        dict: The fields of a "headline" scene.
    """
    # A short beat is shown word for word — it already reads like a headline.
    spoken_words = beat["text"].split()
    if len(spoken_words) <= MAX_FALLBACK_WORDS:
        chosen = []
        for word in spoken_words:
            chosen.append(word.strip(".,;:!?\"“”()—"))
    else:
        # A longer beat is cut down to its first few meaningful words.
        chosen = []
        for word in spoken_words:
            cleaned = word.strip(".,;:!?\"“”()—")
            if cleaned and cleaned.lower() not in FILLER_WORDS:
                chosen.append(cleaned)
        chosen = chosen[:4]

    # Highlight the longest word — usually the key one.
    highlight = ""
    for word in chosen:
        if len(word) > len(highlight):
            highlight = word

    return {
        "template": "headline",
        "text": " ".join(chosen),
        "highlight": highlight,
        "emoji": "",
    }


def _keep_if_supported(text, allowed_words):
    """
    Return text only if every word in it is backed up by the script.

    Used for optional extras (subtitles, titles): if the extra text is
    not backed up we simply leave it out rather than reject the scene.

    Args:
        text (str): Optional on-screen text.
        allowed_words (list[str]): Cleaned words from the script.

    Returns:
        str: The text, or "" if any word in it is not backed up.
    """
    if find_unsupported_words(text, allowed_words):
        return ""
    return text


def _clean_number_symbol(value):
    """
    Accept a prefix or suffix for a number only if it is a simple symbol.

    Args:
        value: Hopefully a short string such as "%" or "$".

    Returns:
        str: The symbol, or "" if it is anything else (for example the
             word "days", which belongs in the label instead).
    """
    if isinstance(value, str) and value.strip() in NUMBER_SYMBOLS:
        return value.strip()
    return ""


def is_number_range(text):
    """
    Check whether a sentence gives a range of numbers ("thirty to fifty").

    Args:
        text (str): The beat's spoken text.

    Returns:
        bool: True if it has two or more numbers and a range word
              ("to", "between", "from").

    Example:
        is_number_range("seeking thirty to fifty billion dollars")   # → True
        is_number_range("seventeen of twenty centers")               # → False
    """
    if len(find_numbers_in_text(text)) < 2:
        return False
    words = _clean_words(text)
    return "to" in words or "between" in words or "from" in words


def _validate_template_fields(raw_scene, beat, script_words):
    """
    Check the graphic the AI chose for one beat and clean up its fields.

    Args:
        raw_scene (dict): One object from the AI's JSON answer.
        beat (dict): The beat this scene belongs to.
        script_words (list[str]): Every cleaned word of the spoken script.

    Returns:
        dict or None: The cleaned template fields, or None if the scene
                      breaks a rule and must be replaced by the fallback.
    """
    template = raw_scene.get("template", "")
    beat_numbers = find_numbers_in_text(beat["text"])
    # On-screen words must come from the sentence this beat belongs to.
    sentence_words = _clean_words(beat["sentence"])

    if template == "headline":
        text = _as_text(raw_scene.get("text"), 7)
        if not text or find_unsupported_words(text, sentence_words):
            return None
        return {
            "template": "headline",
            "text": text,
            "highlight": _as_text(raw_scene.get("highlight"), 1),
            "emoji": _clean_emoji(raw_scene.get("emoji")),
        }

    if template == "icon_label":
        title = _as_text(raw_scene.get("title"), 5)
        emoji = _clean_emoji(raw_scene.get("emoji"))
        if not title or not emoji or find_unsupported_words(title, sentence_words):
            return None
        subtitle = _as_text(raw_scene.get("subtitle"), 8)
        return {
            "template": "icon_label",
            "emoji": emoji,
            "title": title,
            "subtitle": _keep_if_supported(subtitle, sentence_words),
        }

    if template == "person":
        name = _as_text(raw_scene.get("name"), 4)
        # Accuracy guard: every part of the name must be spoken in this beat.
        if not name or find_unsupported_words(name, _clean_words(beat["text"])):
            return None
        for name_word in _clean_words(name):
            if name_word in FREE_SCREEN_WORDS:
                return None
        # The role may use facts from anywhere in the script.
        role = _as_text(raw_scene.get("role"), 6)
        return {
            "template": "person",
            "name": name,
            "role": _keep_if_supported(role, script_words),
        }

    if template == "big_number":
        value = _to_number(raw_scene.get("value"))
        # Accuracy guard: the number must be one that is spoken in this beat.
        if value is None or value not in beat_numbers:
            return None
        # Accuracy guard: "thirty to fifty billion" is a range. Showing only
        # "$50B" would overstate it, so a range never becomes one big number.
        if is_number_range(beat["text"]):
            return None
        label = _as_text(raw_scene.get("label"), 7)
        return {
            "template": "big_number",
            "value": value,
            "prefix": _clean_number_symbol(raw_scene.get("prefix")),
            "suffix": _clean_number_symbol(raw_scene.get("suffix")),
            "label": _keep_if_supported(label, sentence_words),
            "emoji": _clean_emoji(raw_scene.get("emoji")),
        }

    if template == "versus":
        left_label = _as_text(raw_scene.get("left_label"), 6)
        right_label = _as_text(raw_scene.get("right_label"), 6)
        if not left_label or not right_label:
            return None
        if find_unsupported_words(left_label + " " + right_label, sentence_words):
            return None
        return {
            "template": "versus",
            "left_emoji": _clean_emoji(raw_scene.get("left_emoji")),
            "left_label": left_label,
            "right_emoji": _clean_emoji(raw_scene.get("right_emoji")),
            "right_label": right_label,
        }

    if template == "analogy":
        left_label = _as_text(raw_scene.get("left_label"), 6)
        right_label = _as_text(raw_scene.get("right_label"), 6)
        if not left_label or not right_label:
            return None
        # Accuracy guard: the everyday comparison must be spoken in this
        # sentence, so the graphic can never add its own analogy. The real
        # thing is usually named in the sentence before ("the router flooded
        # the network... It's like shouting in a crowded room"), so it may
        # come from anywhere in the script — but never from outside it.
        if find_unsupported_words(right_label, sentence_words):
            return None
        if find_unsupported_words(left_label, script_words):
            return None
        return {
            "template": "analogy",
            "left_emoji": _clean_emoji(raw_scene.get("left_emoji")),
            "left_label": left_label,
            "right_emoji": _clean_emoji(raw_scene.get("right_emoji")),
            "right_label": right_label,
        }

    if template == "bullet_list":
        raw_items = raw_scene.get("items")
        if not isinstance(raw_items, list):
            return None
        items = []
        for raw_item in raw_items[:4]:
            if not isinstance(raw_item, dict):
                continue
            text = _as_text(raw_item.get("text"), 7)
            if text and not find_unsupported_words(text, sentence_words):
                items.append({"emoji": _clean_emoji(raw_item.get("emoji")),
                              "text": text})
        if len(items) < 2:
            return None
        title = _as_text(raw_scene.get("title"), 5)
        return {
            "template": "bullet_list",
            "title": _keep_if_supported(title, sentence_words),
            "items": items,
        }

    if template == "timeline":
        raw_steps = raw_scene.get("steps")
        if not isinstance(raw_steps, list):
            return None
        steps = []
        for raw_step in raw_steps[:4]:
            text = _as_text(raw_step, 6)
            if text and not find_unsupported_words(text, sentence_words):
                steps.append(text)
        if len(steps) < 2:
            return None
        title = _as_text(raw_scene.get("title"), 5)
        return {
            "template": "timeline",
            "title": _keep_if_supported(title, sentence_words),
            "steps": steps,
        }

    if template == "bar_chart":
        raw_bars = raw_scene.get("bars")
        if not isinstance(raw_bars, list):
            return None
        bars = []
        for raw_bar in raw_bars[:4]:
            if not isinstance(raw_bar, dict):
                continue
            label = _as_text(raw_bar.get("label"), 3)
            value = _to_number(raw_bar.get("value"))
            # Accuracy guard: every bar's number must be spoken in this beat.
            if not label or value is None or value not in beat_numbers:
                return None
            if find_unsupported_words(label, sentence_words):
                return None
            bars.append({"label": label, "value": value})
        if len(bars) < 2:
            return None
        title = _as_text(raw_scene.get("title"), 5)
        return {
            "template": "bar_chart",
            "title": _keep_if_supported(title, sentence_words),
            "unit": _clean_number_symbol(raw_scene.get("unit")),
            "bars": bars,
        }

    if template == "stamp":
        text = _as_text(raw_scene.get("text"), 4)
        if not text or find_unsupported_words(text, sentence_words):
            return None
        # A stamp looks like a verdict, so it is stricter than the other
        # graphics: if ANY part of the sentence is a claim or prediction,
        # the stamp itself must say so.
        if find_attribution_words(beat["sentence"]) and not find_attribution_words(text):
            return None
        return {
            "template": "stamp",
            "text": text,
            "emoji": _clean_emoji(raw_scene.get("emoji")),
        }

    # Unknown template name.
    return None


def collect_screen_text(scene):
    """
    Gather every piece of text a scene will show on screen.

    Args:
        scene (dict): One scene (validated, or just its template fields).

    Returns:
        str: All of the scene's on-screen words joined together.
    """
    pieces = []
    for key in ("text", "title", "subtitle", "name", "role", "label",
                "left_label", "right_label"):
        if scene.get(key):
            pieces.append(scene[key])
    for item in scene.get("items", []):
        pieces.append(item.get("text", ""))
    for step in scene.get("steps", []):
        pieces.append(step)
    for bar in scene.get("bars", []):
        pieces.append(bar.get("label", ""))
    return " ".join(pieces)


def validate_photo_subject(value, script_words):
    """
    Accept a photo subject only if the script itself names it.

    Args:
        value: The AI's "photo" field, hopefully a name like "Scott Bessent".
        script_words (list[str]): Every cleaned word of the spoken script.

    Returns:
        str: The subject, or "" if it is missing, too long, or names
             anything the script does not mention.

    Example:
        validate_photo_subject("Scott Bessent", ["scott", "bessent", "said"])
        # → "Scott Bessent"
    """
    subject = _as_text(value, 6)
    if not subject:
        return ""
    if find_unsupported_words(subject, script_words):
        return ""
    return subject


def validate_scene(raw_scene, beat, script_words=None):
    """
    Turn one object from the AI's answer into a safe, complete scene.

    Args:
        raw_scene (dict or None): The AI's plan for this beat, or None if
                                  the AI did not provide one.
        beat (dict): The beat this scene belongs to.
        script_words (list[str] or None): Every cleaned word of the spoken
            script. If None, only this beat's sentence is used.

    Returns:
        dict: A scene with the beat's details ("section", "spoken_text",
              "word_start", "word_count"), a "footage" search phrase, and
              the fields of its graphic template.

    Example:
        beat = {"section": "Story", "text": "It is rare.", "sentence": "It is rare.",
                "word_start": 0, "word_count": 3}
        validate_scene({"template": "stamp", "text": "Rare", "footage": "capitol"}, beat)
        # → {"section": "Story", "spoken_text": "It is rare.", ...,
        #    "footage": "capitol", "template": "stamp", "text": "Rare", "emoji": ""}
    """
    if script_words is None:
        script_words = _clean_words(beat["sentence"])

    scene = {
        "section": beat["section"],
        "spoken_text": beat["text"],
        "word_start": beat["word_start"],
        "word_count": beat["word_count"],
        "footage": "",
    }

    fields = None
    if isinstance(raw_scene, dict):
        scene["footage"] = _as_text(raw_scene.get("footage"), 3).lower()
        scene["photo"] = validate_photo_subject(raw_scene.get("photo"), script_words)
        fields = _validate_template_fields(raw_scene, beat, script_words)

    # Accuracy guard for every graphic except name cards: if this beat
    # reports a claim or a prediction ("argues", "says", "expected"), the
    # graphic must keep one of those words.
    if fields is not None and fields["template"] != "person":
        if find_attribution_words(beat["text"]):
            if not find_attribution_words(collect_screen_text(fields)):
                fields = None

    if fields is None:
        fields = make_fallback_fields(beat)

    scene.update(fields)
    return scene


def parse_storyboard_reply(reply_text, beats):
    """
    Turn the AI's text answer into a checked list of scenes.

    Every beat always gets a scene: if the AI skipped one, or its answer
    could not be read at all, the plain fallback headline is used.

    Args:
        reply_text (str): The AI's raw answer (should be a JSON array).
        beats (list[dict]): Output of split_into_beats().

    Returns:
        list[dict]: One validated scene per beat, in order.
    """
    raw_scenes = []

    # Keep only the part from the first "[" to the last "]", in case the
    # model wrapped its JSON in extra words or a markdown code block.
    start = reply_text.find("[")
    end = reply_text.rfind("]")
    if start != -1 and end > start:
        try:
            parsed = json.loads(reply_text[start:end + 1])
            if isinstance(parsed, list):
                raw_scenes = parsed
        except json.JSONDecodeError as error:
            print(f"  WARNING: Could not read the storyboard answer ({error}). "
                  "Using plain headlines instead.")

    # Index the AI's scenes by their beat number.
    scenes_by_beat = {}
    for position, raw_scene in enumerate(raw_scenes):
        if not isinstance(raw_scene, dict):
            continue
        beat_number = raw_scene.get("beat", position + 1)
        if isinstance(beat_number, int) and beat_number not in scenes_by_beat:
            scenes_by_beat[beat_number] = raw_scene

    script_words = []
    for beat in beats:
        script_words.extend(_clean_words(beat["text"]))

    scenes = []
    for beat_number, beat in enumerate(beats, start=1):
        scenes.append(validate_scene(scenes_by_beat.get(beat_number), beat,
                                     script_words))
    return scenes


def print_storyboard_review(scenes):
    """
    Print each scene's on-screen text beside the words spoken over it.

    Human review is mandatory before anything is published, and what
    appears on screen needs fact-checking just like the script does. This
    printout makes that check quick: read down the list and make sure no
    graphic says more than the narration.

    Args:
        scenes (list[dict]): The validated storyboard scenes.
    """
    print("  Storyboard — check each graphic against what is spoken:")
    for scene_number, scene in enumerate(scenes, start=1):
        print(f"    {scene_number:2d}. [{scene['template']}] "
              f"{collect_screen_text(scene)}")
        print(f"          spoken: {scene['spoken_text']}")


def storyboard_path_for(script_path):
    """
    Work out where a script's storyboard file is saved.

    Args:
        script_path (str): e.g. "output/news/2026-11-09-story.md".

    Returns:
        str: e.g. "output/news/2026-11-09-story_storyboard.json".
    """
    return os.path.splitext(script_path)[0] + "_storyboard.json"


def _load_saved_storyboard(path, beats, ai_available):
    """
    Load a saved storyboard if it still matches the script.

    Args:
        path (str): The storyboard .json path.
        beats (list[dict]): The beats of the current script.
        ai_available (bool): True if we could ask the AI for a new plan.

    Returns:
        list[dict] or None: The saved scenes, or None if there is no saved
                            storyboard or it should be made again.
    """
    if not os.path.exists(path):
        return None

    try:
        with open(path, "r", encoding="utf-8") as f:
            saved = json.load(f)
        scenes = saved["scenes"]
        saved_word_total = 0
        for scene in scenes:
            saved_word_total += scene["word_count"]
    except (json.JSONDecodeError, KeyError, TypeError):
        print("  Saved storyboard could not be read — making a new one.")
        return None

    # If the script was edited, its word count no longer matches.
    script_word_total = 0
    for beat in beats:
        script_word_total += beat["word_count"]
    if saved_word_total != script_word_total:
        print("  Script changed since the storyboard was made — making a new one.")
        return None

    # A storyboard made without the AI is only a placeholder. Replace it
    # as soon as the AI is available.
    if saved.get("source") == "fallback" and ai_available:
        return None

    return scenes


def get_storyboard(script_path, channel_config, llm):
    """
    Get the storyboard for a script: load it if saved, otherwise make it.

    Making it costs one small AI call. The result is saved next to the
    script so re-rendering the same script is free, and so you can open
    the file and edit any scene by hand before rendering.

    Args:
        script_path (str): Path to the .md script file.
        channel_config (dict): The channel config. The model name comes
                               from models.storyboard (or models.ranking).
        llm (LLMClient or None): The run's AI client. If None (dry run),
                                 plain headlines are used and nothing is spent.

    Returns:
        list[dict]: One scene per beat. See validate_scene() for the fields.

    Example:
        scenes = get_storyboard("output/news/2026-11-09-story.md", config, llm)
        scenes[0]["template"]   # → "headline"
    """
    beats = split_into_beats(read_script_sections(script_path))
    if not beats:
        print("  WARNING: No spoken text found — the storyboard is empty.")
        return []

    path = storyboard_path_for(script_path)
    saved_scenes = _load_saved_storyboard(path, beats, ai_available=llm is not None)
    if saved_scenes is not None:
        print(f"  Storyboard loaded from {path} — $0.00")
        print_storyboard_review(saved_scenes)
        return saved_scenes

    scenes = None
    source = "fallback"

    if llm is not None:
        # Imported here to avoid loading the AI module during offline tests.
        from src.generation.llm import CallLimitReached, LLMError

        models = channel_config.get("models", {})
        model = models.get("storyboard", models.get("ranking"))
        max_tokens = channel_config.get("llm", {}).get("storyboard_max_tokens", 6000)
        print(f"  Planning storyboard with {model} ({len(beats)} beats)...")

        try:
            reply = llm.ask(
                model=model,
                system_prompt=STORYBOARD_SYSTEM_PROMPT,
                user_message=build_storyboard_message(beats, channel_config),
                max_tokens=max_tokens,
                purpose="storyboard",
            )
            scenes = parse_storyboard_reply(reply, beats)
            source = "ai"
        except (LLMError, CallLimitReached) as error:
            print(f"  WARNING: Storyboard AI call failed ({error}). "
                  "Using plain headlines instead.")

    if scenes is None:
        scenes = []
        for beat in beats:
            scenes.append(validate_scene(None, beat))

    with open(path, "w", encoding="utf-8") as f:
        json.dump({"version": STORYBOARD_VERSION, "source": source, "scenes": scenes},
                  f, indent=2, ensure_ascii=False)
    print(f"  Storyboard saved: {path} ({len(scenes)} scenes)")
    print_storyboard_review(scenes)
    return scenes


def add_scene_times(scenes, word_timings, total_duration):
    """
    Work out when each scene starts and ends, from the voiceover timing.

    A scene starts just before its first word is spoken and lasts until
    the next scene starts, so there is never a gap between scenes.

    Args:
        scenes (list[dict]): Storyboard scenes. Each gains "start" and
                             "end" keys (seconds from the start of the video).
        word_timings (list[dict]): One {"word", "start", "end"} per spoken
                                   word, from tts.py.
        total_duration (float): Length of the voiceover in seconds.

    Returns:
        list[dict]: The same scenes, now with "start" and "end".

    Example:
        add_scene_times(scenes, word_timings, 95.0)
        scenes[1]["start"]   # → 5.48
    """
    for scene_number, scene in enumerate(scenes):
        if scene_number == 0 or not word_timings:
            scene["start"] = 0.0
            continue

        # Guard against a timing list that is shorter than the script.
        word_index = min(scene["word_start"], len(word_timings) - 1)
        start = word_timings[word_index]["start"] - SCENE_LEAD_SECONDS
        # A scene can never start before the one in front of it.
        scene["start"] = max(start, scenes[scene_number - 1]["start"])

    for scene_number, scene in enumerate(scenes):
        if scene_number + 1 < len(scenes):
            scene["end"] = scenes[scene_number + 1]["start"]
        else:
            scene["end"] = max(total_duration, scene["start"])

    return scenes

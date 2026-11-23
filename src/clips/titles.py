# =============================================================
# src/clips/titles.py — choose natural titles, captions and hashtags
#
# The editor model (editor.py) writes several title ideas from what
# actually happened. This file picks the best one with plain, testable
# rules, so titles stay specific and human instead of AI clickbait:
#
#   + specific: uses words that were really said / names who is in it
#   + short and readable; natural casing
#   - generic hype ("you won't believe", "insane", "goes crazy"...)
#   - more than one ALL-CAPS word or more than one emoji
#   - names or numbers that never appear in the clip (possible invention)
#   - nearly the same as a recent title (repeated formula)
#
# Four separate outputs, as the brief asks: on-screen hook text, YouTube
# Shorts title, TikTok description, and a few relevant hashtags.
# Research behind the rules: docs/VIRAL_CONTENT_RESEARCH.md section 2.
# =============================================================

import re

# Phrases that make a title sound like generic AI clickbait (lower case).
BANNED_PHRASES = [
    "you won't believe", "you wont believe", "won't believe", "what happens next", "wait for it",
    "must watch", "goes crazy", "went crazy", "is insane", "was insane", "this was insane", "insane",
    "epic", "most epic", "not ready", "was not ready", "wasn't ready", "shocking", "will shock you",
    "mind blowing", "mind-blowing", "unbelievable", "gone wrong", "you need to see", "jaw-dropping",
    "lost his mind", "loses it", "loses his mind", "crazy moment", "craziest", "ultimate", "legendary moment",
]

# Phrases that were far more common in flop titles than hit titles in the
# within-channel YouTube study (VIRAL_CONTENT_RESEARCH.md section 9: "in GTA"
# 2.6x more common among flops). A small penalty, not a ban.
FLOP_PHRASES = ["in gta"]

# Bonus when a title names another person in the clip (not the streamer):
# TikTok breakouts were tied to people and stories viewers follow, and their
# posts named more of the other people (TIKTOK_CONTENT_RESEARCH.md 2.4d-e).
OTHER_PERSON_BONUS = 8

# Filler words that carry no information when checking specificity.
COMMON_WORDS = {
    "the", "a", "an", "and", "or", "but", "to", "of", "in", "on", "at", "for", "with", "his", "her", "their",
    "he", "she", "they", "it", "this", "that", "was", "is", "are", "be", "so", "too", "way", "after", "before",
    "about", "just", "really", "actually", "got", "get", "gets", "made", "make", "when", "what", "why", "how",
    "out", "up", "down", "bro", "him", "them", "had", "has", "have", "did", "does", "do", "not", "no", "all",
    "much", "more", "very", "than", "then", "from", "into", "over", "said", "says", "tried", "tries",
}


def count_emoji(text):
    """
    Count emoji characters in a text.

    Args:
        text (str): Any text.

    Returns:
        int: How many characters are emoji (or similar pictographs).
    """
    count = 0
    for character in text:
        code = ord(character)
        if code >= 0x1F300 or 0x2600 <= code <= 0x27BF:
            count += 1
    return count


def caps_words(text):
    """
    Count fully capitalised words of 3+ letters ("INSANE"), ignoring short ones like "GTA".

    Args:
        text (str): A title.

    Returns:
        int: How many shouted words.
    """
    count = 0
    for word in re.findall(r"[A-Za-z']+", text):
        if len(word) >= 4 and word.isupper():
            count += 1
    return count


def content_words(text):
    """
    The meaningful words of a text (lower case, no filler words).

    Args:
        text (str): Any text.

    Returns:
        set[str]: Words of 3+ letters that are not common filler.
    """
    words = set()
    for word in re.findall(r"[a-z0-9']+", text.lower()):
        word = word.strip("'")
        if len(word) >= 3 and word not in COMMON_WORDS:
            words.add(word)
    return words


def unsupported_names(title, transcript, known_names):
    """
    Find capitalised names or numbers in a title that the clip never mentions.

    Args:
        title (str): A title.
        transcript (str): What was said in the clip.
        known_names (list[str]): Names that are always fine (the streamer...).

    Returns:
        list[str]: Suspicious words (possible inventions).
    """
    said = transcript.lower()
    allowed = set()
    for name in known_names:
        for part in name.lower().split():
            allowed.add(part)
    suspicious = []
    words = re.findall(r"[A-Za-z0-9$']+", title)
    capitalised = 0
    for word in words:
        if word[:1].isupper():
            capitalised += 1
    # In "Title Case" every word is capitalised, so capitals do not mark
    # names there; then only numbers are checked.
    title_case = capitalised > 0.6 * max(1, len(words))
    for position, word in enumerate(words):
        plain = word.strip("'").lower()
        is_number = any(character.isdigit() for character in word)
        is_name = (not title_case and word[:1].isupper() and not word.isupper() and position > 0)
        if not (is_number or is_name):
            continue
        if plain in allowed or plain.rstrip("'s") in allowed or plain in said:
            continue
        suspicious.append(word)
    return suspicious


def guessed_pronouns(title, transcript):
    """
    Find he/she/his/her-style words in a title that the clip itself never says.

    Args:
        title (str): A title idea.
        transcript (str): What was said.

    Returns:
        list[str]: The guessed pronouns (empty when every one appears in the clip).
    """
    pronouns = {"he", "him", "his", "she", "her", "hers", "he's", "she's"}
    said = set(re.findall(r"[a-z']+", transcript.lower()))
    guessed = []
    for word in re.findall(r"[a-z']+", title.lower()):
        if word in pronouns and word not in said and word not in guessed:
            guessed.append(word)
    return guessed


def title_score(title, transcript, known_names, recent_titles, max_words, max_emoji=1, other_names=None):
    """
    Score one title idea from 0 to 100 and list its problems.

    Args:
        title (str): The idea.
        transcript (str): What was said in the clip (for grounding).
        known_names (list[str]): Streamer and other known names.
        recent_titles (list[str]): Titles used recently (to avoid repeating a formula).
        max_words (int): Longest allowed title, in words.
        max_emoji (int): Most emoji allowed.
        other_names (list[str] or None): People in the clip other than the
            streamer; naming one of them earns a small bonus.

    Returns:
        tuple: (score, list of plain-English problems).

    Example:
        title_score("Jason Was NOT Ready For This!", ...) → low score, ["generic phrase: not ready"]
    """
    problems = []
    score = 70.0
    lower = title.lower()
    for phrase in BANNED_PHRASES:
        if re.search(r"(?<![a-z])" + re.escape(phrase) + r"(?![a-z])", lower):
            problems.append("generic phrase: " + phrase)
            score -= 35
            break
    for phrase in FLOP_PHRASES:
        if re.search(r"(?<![a-z])" + re.escape(phrase) + r"(?![a-z])", lower):
            problems.append("flop phrase: " + phrase)
            score -= 10
    for other in other_names or []:
        if len(other) >= 3 and re.search(r"(?<![a-z])" + re.escape(other.lower()) + r"(?![a-z])", lower):
            score += OTHER_PERSON_BONUS
            break
    words = title.split()
    if len(words) > max_words:
        problems.append(f"too long ({len(words)} words)")
        score -= 5 * (len(words) - max_words)
    if len(words) < 3:
        problems.append("too short to explain anything")
        score -= 15
    if caps_words(title) > 1:
        problems.append("more than one ALL-CAPS word")
        score -= 12 * (caps_words(title) - 1)
    if count_emoji(title) > max_emoji:
        problems.append("too many emoji")
        score -= 15
    if "#" in title:
        problems.append("hashtag in title")
        score -= 15
    if title.count("!") > 1 or "?!" in title:
        problems.append("fake urgency punctuation")
        score -= 10
    guessed = guessed_pronouns(title, transcript)
    if guessed:
        # "Marlon tells his wife" when Marlon said "their wife": a pronoun the clip
        # never uses is a guess (wrong facts or someone's gender).
        problems.append("pronoun not in the clip: " + ", ".join(guessed))
        score -= 15
    invented = unsupported_names(title, transcript, known_names)
    if invented:
        problems.append("not in the clip: " + ", ".join(invented[:3]))
        score -= 20
    # Specific: shares meaningful words with what was said (but is not a copy).
    shared = content_words(title) & content_words(transcript)
    score += min(20, 7 * len(shared))
    if not shared:
        problems.append("nothing specific from the clip")
    for recent in recent_titles:
        first = content_words(title)
        second = content_words(recent)
        if first and second and len(first & second) / len(first | second) >= 0.6:
            problems.append("almost the same as a recent title")
            score -= 25
            break
    return max(0.0, min(100.0, round(score, 1))), problems


def pick_best(options, transcript, known_names, recent_titles, max_words, max_emoji=1, other_names=None):
    """
    Score every idea and return the best one with all the scores.

    Args:
        options (list[str]): Title ideas.
        transcript (str): What was said.
        known_names (list[str]): Known names.
        recent_titles (list[str]): Recent titles.
        max_words (int): Word limit.
        max_emoji (int): Emoji limit.
        other_names (list[str] or None): People in the clip other than the streamer.

    Returns:
        tuple: (best title or "", list of {"text", "score", "problems"} for every idea).
    """
    scored = []
    for option in options:
        text = " ".join(option.replace('"', "").split())
        if not text:
            continue
        score, problems = title_score(text, transcript, known_names, recent_titles, max_words, max_emoji,
                                      other_names)
        scored.append({"text": text, "score": score, "problems": problems})
    if not scored:
        return "", []
    best = scored[0]
    for item in scored:
        if item["score"] > best["score"]:
            best = item
    return best["text"], scored


def quote_title(transcript, max_words):
    """
    Make a title from the clip's own words (used when no model title is usable).

    Research: some top clippers simply show the funniest line in quotes.
    This picks the last complete short sentence (usually the payoff line).

    Args:
        transcript (str): What was said.
        max_words (int): Word limit.

    Returns:
        str: For example '"why would you admit that"', or "".
    """
    cleaned = re.sub(r"(?<=[.!?,])[-–—]+", "", transcript.strip())
    sentences = re.split(r"(?<=[.!?])\s+", cleaned)
    for sentence in reversed(sentences):
        words = sentence.strip(" .").split()
        if 3 <= len(words) <= max_words:
            return '"' + " ".join(words).rstrip(".,") + '"'
    return ""


def build_hashtags(username, platform, game, channel_tags, maximum=4):
    """
    Choose a few relevant hashtags (no stuffing).

    Order: the streamer, the platform, the game (when known), then the
    channel's own tags until the limit.

    Args:
        username (str): The streamer's login, e.g. "jasontheween".
        platform (str): "twitch" or "kick".
        game (str): The game name the editor recognised, or "".
        channel_tags (list[str]): The channel YAML's hashtags.
        maximum (int): Most hashtags.

    Returns:
        list[str]: For example ["#jasontheween", "#twitch", "#gta"].
    """
    tags = []
    candidates = ["#" + username.lower(), "#" + platform.lower()]
    game_tag = re.sub(r"[^a-z0-9]", "", game.lower())
    if 2 <= len(game_tag) <= 20:
        candidates.append("#" + game_tag)
    platform_tags = {"#twitch", "#kick"}
    for tag in channel_tags:
        # Never tag the other streaming site (#kick on a Twitch clip).
        if tag.lower() in platform_tags and tag.lower() != "#" + platform.lower():
            continue
        candidates.append(tag)
    for tag in candidates:
        if tag.lower() not in tags and len(tags) < maximum:
            tags.append(tag.lower())
    return tags


def choose_titles(review, transcript, name, username, platform, other_names, recent_titles,
                  title_settings, channel_tags):
    """
    Produce the four separate texts for one clip.

    Args:
        review (dict): The editor model's review (may be empty without the model).
        transcript (str): What was said in the final clip.
        name (str): Streamer display name.
        username (str): Streamer login.
        platform (str): "twitch" or "kick".
        other_names (list[str]): Other names the model said are in the clip.
        recent_titles (list[str]): Recent YouTube titles of this channel.
        title_settings (TitleSettings): Limits from the channel YAML.
        channel_tags (list[str]): The channel YAML's hashtags.

    Returns:
        dict: {"hook_text", "youtube_title", "tiktok_description", "hashtags",
               "title_candidates": [...scored ideas...], "title_method"}.
    """
    known = [name, username] + other_names
    titles = review.get("titles", {}) if review else {}
    hook, hook_scores = pick_best(titles.get("hook", []), transcript, known, [],
                                  title_settings.hook_max_words, title_settings.max_emoji, other_names)
    youtube, youtube_scores = pick_best(titles.get("youtube", []), transcript, known, recent_titles,
                                        title_settings.youtube_max_words, title_settings.max_emoji, other_names)
    # Prefer an idea that already names the streamer over bolting "Name:" onto another one.
    best_named = None
    for idea in youtube_scores:
        lower = idea["text"].lower()
        if name.lower() in lower or username.lower() in lower:
            if best_named is None or idea["score"] > best_named["score"]:
                best_named = idea
    if (best_named is not None and not best_named["problems"]
            and best_named["score"] >= best_score(youtube_scores) - 10):
        youtube = best_named["text"]
    method = "editor model + rules"
    minimum = title_settings.minimum_score
    if not youtube or best_score(youtube_scores) < minimum:
        quoted = quote_title(transcript, title_settings.youtube_max_words - 2)
        youtube = f"{name}: {quoted}" if quoted else f"{name} on stream"
        method = "quote from the clip (no model title passed the checks)"
    elif name.lower() not in youtube.lower() and username.lower() not in youtube.lower():
        youtube = f"{name}: {youtube}"
    if not hook or best_score(hook_scores) < minimum:
        hook = quote_title(transcript, title_settings.hook_max_words) or ""
    description = (titles.get("tiktok") or "").strip()
    if not description or title_score(description, transcript, known, [], 30, title_settings.max_emoji)[0] < minimum:
        description = youtube
    return {"hook_text": hook, "youtube_title": youtube, "tiktok_description": description,
            "hashtags": build_hashtags(username, platform, (review or {}).get("game", ""),
                                       channel_tags, title_settings.max_hashtags),
            "title_candidates": {"hook": hook_scores, "youtube": youtube_scores}, "title_method": method}


def best_score(scored):
    """
    The highest score in a list of scored ideas (0 when empty).

    Args:
        scored (list[dict]): From pick_best().

    Returns:
        float: Best score.
    """
    best = 0.0
    for item in scored:
        best = max(best, item["score"])
    return best

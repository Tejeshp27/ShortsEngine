# =============================================================
# src/clips/moments.py — pick the best part of a clip (step L.4)
#
# The cheap AI model (Claude Haiku, set in the channel YAML) reads
# the clip's timed transcript and answers with:
#   - where the best moment starts and ends (20-60 seconds), or
#     "keep it whole" if the clip is already short and strong
#   - a hook title of at most 8 words
#
# The AI's answer is never trusted blindly. The code then:
#   - forces the moment inside the clip and inside the length limits
#   - moves the cut points so no word is cut in half
#   - cuts the hook title to the word limit and removes hashtags
# If the answer cannot be read at all, the whole clip is used with
# Twitch's own clip title — so a bad answer never breaks a video.
#
# A clip with no speech skips the AI call entirely (free).
# One call costs about $0.002.
# =============================================================

import json

# The standing instructions for the model. Channel-specific details
# (what the channel is about, who watches) come from the YAML.
SYSTEM_PROMPT = """You choose the single best moment from a livestream clip for a vertical short video (TikTok).
Channel: {niche}
Audience: {audience}

Rules:
- Pick ONE continuous moment between {min_seconds:g} and {max_seconds:g} seconds long, inside the clip.
- Start just before the setup and end right after the payoff or reaction. Never cut someone off mid-sentence.
- If the whole clip is already one strong moment within the length limits, keep it whole.
- Write a hook title of at most {max_words} words that makes people want to keep watching.
- The hook title must describe what really happens in the transcript or clip title. Do not invent events.
  No hashtags, no emojis, no quotation marks.
- Answer with JSON only, exactly like this:
{{"keep_whole": false, "start": 12.5, "end": 41.0, "hook_title": "He did NOT expect that", "reason": "one short sentence"}}"""

# Transcript lines are broken at pauses longer than this, or after this many words.
LINE_BREAK_PAUSE_SECONDS = 0.6
MAX_WORDS_PER_LINE = 12

# An end point this close to the end of the clip counts as "the very end".
END_OF_FILE_MARGIN_SECONDS = 0.1

# Used when neither the AI nor Twitch gives a usable title.
DEFAULT_HOOK_TITLE = "Wait for the ending"


def build_system_prompt(config, limits, max_words):
    """
    Fill in the standing instructions with this channel's details.

    Args:
        config (dict): The channel's YAML settings ("niche", "audience").
        limits (ClipLimits): The length limits.
        max_words (int): The most words a hook title may have.

    Returns:
        str: The system prompt.
    """
    return SYSTEM_PROMPT.format(
        niche=config.get("niche", "Livestream highlights"),
        audience=config.get("audience", "Short-video viewers"),
        min_seconds=limits.moment_min_seconds,
        max_seconds=limits.moment_max_seconds,
        max_words=max_words,
    )


def transcript_lines(words):
    """
    Group timed words into short lines that start with their time.

    The AI can then answer with real times from the clip.

    Args:
        words (list[dict]): [{"word", "start", "end"}, ...]

    Returns:
        str: For example "[0.0] okay watch this\\n[2.4] NO WAY".
    """
    lines = []
    current_words = []
    line_start = 0.0
    previous_end = None

    for word in words:
        long_pause = previous_end is not None and word["start"] - previous_end > LINE_BREAK_PAUSE_SECONDS
        if current_words and (long_pause or len(current_words) >= MAX_WORDS_PER_LINE):
            lines.append(f"[{line_start:.1f}] " + " ".join(current_words))
            current_words = []
        if not current_words:
            line_start = word["start"]
        current_words.append(word["word"])
        previous_end = word["end"]

    if current_words:
        lines.append(f"[{line_start:.1f}] " + " ".join(current_words))
    return "\n".join(lines)


def build_user_message(clip, words):
    """
    Describe one clip for the AI: its title, length and transcript.

    Args:
        clip (dict): The clip row from the database.
        words (list[dict]): The clip's timed words.

    Returns:
        str: The message sent to the model.
    """
    return (
        f"Clip title (written by viewers): {clip['title'] or '(none)'}\n"
        f"Streamer: {clip['streamer']}\n"
        f"Clip length: {clip['duration_seconds']:.1f} seconds\n"
        f"Transcript (seconds in brackets):\n{transcript_lines(words)}"
    )


def read_json_answer(text):
    """
    Pull the JSON object out of the model's answer.

    Models sometimes add words around the JSON, so this takes the
    text between the first "{" and the last "}".

    Args:
        text (str): The model's answer.

    Returns:
        dict or None: The parsed answer, or None if there is no valid JSON.
    """
    first = text.find("{")
    last = text.rfind("}")
    if first == -1 or last <= first:
        return None
    try:
        answer = json.loads(text[first:last + 1])
    except ValueError:
        return None
    if not isinstance(answer, dict):
        return None
    return answer


def as_seconds(value, default):
    """
    Read a number of seconds from the AI's answer, safely.

    Args:
        value: Whatever the AI wrote (a number, text, or nothing).
        default (float): Used when the value is not a usable number.

    Returns:
        float: The seconds.
    """
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return default
    if seconds != seconds:  # only "not a number" (NaN) is unequal to itself
        return default
    return seconds


def fit_to_limits(start, end, clip_length, min_seconds, max_seconds):
    """
    Force a moment to lie inside the clip and inside the length limits.

    Args:
        start (float): Proposed start, in seconds from the clip's beginning.
        end (float): Proposed end.
        clip_length (float): How long the whole clip is.
        min_seconds (float): Shortest allowed moment.
        max_seconds (float): Longest allowed moment.

    Returns:
        tuple: (start, end) that obey every rule.

    Example:
        fit_to_limits(10, 90, 100, 20, 60)   # → (10, 70): too long, end moved in
        fit_to_limits(50, 55, 60, 20, 60)    # → (40, 60): too short, grown backwards
        fit_to_limits(0, 15, 15, 20, 60)     # → (0, 15): clip shorter than 20s, kept whole
    """
    # A clip shorter than the minimum is simply used whole.
    if clip_length <= min_seconds:
        return 0.0, clip_length

    start = min(max(start, 0.0), clip_length)
    end = min(max(end, 0.0), clip_length)
    if end <= start:
        start, end = 0.0, clip_length

    if end - start > max_seconds:
        end = start + max_seconds

    if end - start < min_seconds:
        missing = min_seconds - (end - start)
        # Grow forwards first (keep the reaction), then backwards.
        room_after = clip_length - end
        grow_forward = min(missing, room_after)
        end += grow_forward
        start -= missing - grow_forward
        start = max(start, 0.0)

    return round(start, 2), round(end, 2)


def snap_start_to_word(start, words):
    """
    Move a start point back to the beginning of the word it would cut.

    Args:
        start (float): The start time.
        words (list[dict]): The clip's timed words.

    Returns:
        float: The start time, never inside a word.
    """
    for word in words:
        if word["start"] < start < word["end"]:
            return word["start"]
    return start


def snap_end_to_word(end, words, latest_allowed):
    """
    Move an end point so it does not cut a word in half.

    The end moves forward to finish the word if that stays within
    latest_allowed; otherwise it moves back to just before the word.

    Args:
        end (float): The end time.
        words (list[dict]): The clip's timed words.
        latest_allowed (float): The end may not go past this time.

    Returns:
        float: The end time, never inside a word.
    """
    for word in words:
        if word["start"] < end < word["end"]:
            if word["end"] <= latest_allowed:
                return word["end"]
            return word["start"]
    return end


def clean_hook_title(text, max_words):
    """
    Tidy the AI's hook title: no hashtags or quotes, at most max_words words.

    Args:
        text: The title the AI wrote (may be missing or not text).
        max_words (int): The word limit.

    Returns:
        str: The cleaned title, or "" if nothing usable is left.

    Example:
        clean_hook_title('"He really thought that would work" #twitch', 8)
        # → "He really thought that would work"
    """
    if not isinstance(text, str):
        return ""
    kept_words = []
    for word in text.replace('"', " ").replace("“", " ").replace("”", " ").split():
        if word.startswith("#"):
            continue
        kept_words.append(word)
        if len(kept_words) == max_words:
            break
    title = " ".join(kept_words).strip(" ,;:-")
    return title


def fallback_title(clip, max_words):
    """
    Get a hook title without AI help: the viewers' clip title, shortened.

    Args:
        clip (dict): The clip row.
        max_words (int): The word limit.

    Returns:
        str: A title that is never empty.
    """
    title = clean_hook_title(clip.get("title", ""), max_words)
    if title:
        return title
    return DEFAULT_HOOK_TITLE


def decide_moment(answer, clip, words, limits, max_words):
    """
    Turn the AI's answer (or no answer) into a safe, final moment.

    Args:
        answer (dict or None): From read_json_answer(), or None.
        clip (dict): The clip row ("duration_seconds", "title").
        words (list[dict]): The clip's timed words.
        limits (ClipLimits): The length limits.
        max_words (int): The hook title word limit.

    Returns:
        dict: {"start", "end", "hook_title", "reason"}.
    """
    clip_length = float(clip["duration_seconds"])
    if answer is None:
        answer = {"keep_whole": True, "reason": "No usable AI answer; using the whole clip."}

    if answer.get("keep_whole"):
        start, end = 0.0, clip_length
    else:
        start = as_seconds(answer.get("start"), 0.0)
        end = as_seconds(answer.get("end"), clip_length)

    start, end = fit_to_limits(start, end, clip_length, limits.moment_min_seconds,
                               limits.moment_max_seconds)
    latest_end = min(clip_length, start + limits.moment_max_seconds)
    start = snap_start_to_word(start, words)
    # An end at the very end of the file can't cut a word (there is
    # nothing after it). Whisper's last word often runs a little past
    # the file's end, so snapping there would wrongly drop that word.
    if end < clip_length - END_OF_FILE_MARGIN_SECONDS:
        end = snap_end_to_word(end, words, latest_end)

    title = clean_hook_title(answer.get("hook_title"), max_words)
    if not title:
        title = fallback_title(clip, max_words)

    reason = answer.get("reason", "")
    if not isinstance(reason, str):
        reason = ""
    return {"start": round(start, 2), "end": round(end, 2), "hook_title": title,
            "reason": reason[:300]}


def pick_moment(clip, words, config, settings, llm):
    """
    Choose the best moment of one clip and its hook title.

    Args:
        clip (dict): The clip row from the database.
        words (list[dict]): The clip's timed words (may be empty).
        config (dict): The channel's YAML settings (models, llm limits).
        settings (ClipChannelSettings): The checked clip settings.
        llm (LLMClient): The run's AI client (logs the cost of the call).

    Returns:
        dict: {"start", "end", "hook_title", "reason"}.

    Raises:
        CallLimitReached / LLMError: If the AI call cannot be made.
    """
    max_words = settings.hook_title.max_words
    if not words:
        # No speech to judge: skip the paid call and keep the clip whole.
        answer = {"keep_whole": True, "reason": "No speech found; kept the whole clip."}
        return decide_moment(answer, clip, words, settings.clips, max_words)

    text = llm.ask(
        model=config["models"]["clip_pick"],
        system_prompt=build_system_prompt(config, settings.clips, max_words),
        user_message=build_user_message(clip, words),
        max_tokens=config.get("llm", {}).get("clip_pick_max_tokens", 300),
        purpose="clip_pick",
    )
    return decide_moment(read_json_answer(text), clip, words, settings.clips, max_words)

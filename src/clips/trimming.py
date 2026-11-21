# =============================================================
# src/clips/trimming.py — tighten a chosen moment like a human editor
#
# What it does (all decisions are measured, then explained in the clip's
# decision log):
#   - Opening: starts just before the first spoken line, skipping a lone
#     filler word ("um", "okay so") followed by a pause.
#   - Dead air: a silent gap between lines longer than `max_dead_air_seconds`
#     is shortened to `keep_gap_seconds` — UNLESS something is happening in
#     it (laughter, a scream, big on-screen motion) or it is the pause right
#     before the payoff (comedic timing), which may stay up to
#     `comedic_pause_seconds`. At most `max_cuts` jump cuts per clip, so it
#     never feels over-edited.
#   - Ending: ends `reaction_tail_seconds` after the payoff line; if people
#     are still laughing, keeps the laugh (up to `max_tail_seconds`).
#
# Research (docs/VIRAL_CONTENT_RESEARCH.md): good clips open on the setup
# line and end on the reaction within about a second; editors cut silence
# but "never inside a laugh". auto-editor-style loudness cutting was rejected
# because it removes comedic pauses.
# =============================================================

FILLER_OPENERS = {"um", "uh", "so", "okay", "ok", "alright", "like", "yeah", "anyway", "and", "but"}


def plain(text):
    """
    Lower-case a word and remove punctuation around it.

    Args:
        text (str): A word, e.g. "Okay,".

    Returns:
        str: "okay".
    """
    return text.strip(" .,!?\"'…-").lower()


def words_inside(words, start, end):
    """
    Return the words that lie inside a window.

    Args:
        words (list[dict]): Timed words.
        start (float): Window start.
        end (float): Window end.

    Returns:
        list[dict]: The words, in time order.
    """
    inside = []
    for word in words:
        if word["end"] > start and word["start"] < end:
            inside.append(word)
    return inside


def something_happens(sounds, motion, gap_start, gap_end):
    """
    Check whether a gap without speech still contains a laugh, scream or big action.

    Args:
        sounds (list[dict]): Per-second sound chances (may be empty).
        motion (list[float]): Per-second picture change (may be empty).
        gap_start (float): Gap start, in source seconds.
        gap_end (float): Gap end.

    Returns:
        bool: True when the gap should be kept.
    """
    for second in range(int(gap_start), int(gap_end) + 1):
        if second < len(sounds):
            chances = sounds[second]
            if chances.get("laughter", 0) >= 0.3 or chances.get("scream", 0) >= 0.35 or chances.get("cheer", 0) >= 0.35:
                return True
        if second < len(motion) and motion[second] >= 0.12:
            return True
    return False


def opening_start(start, end, spoken, lead_seconds):
    """
    Choose where the clip starts: just before the first real line.

    Args:
        start (float): The chosen start.
        end (float): The chosen end.
        spoken (list[dict]): Words inside the moment.
        lead_seconds (float): Sound kept before the first word.

    Returns:
        tuple: (new start, note or "").
    """
    if not spoken:
        return start, ""
    first = 0
    # Skip one lone filler word followed by a pause ("Okay... so the thing is").
    if (len(spoken) > 2 and plain(spoken[0]["word"]) in FILLER_OPENERS
            and spoken[1]["start"] - spoken[0]["end"] >= 0.5):
        first = 1
    new_start = max(start, spoken[first]["start"] - lead_seconds)
    if new_start - start < 0.3:
        return start, ""
    note = f"Opening trimmed {new_start - start:.1f}s to the first line"
    if first == 1:
        note += " (skipped filler '" + spoken[0]["word"] + "')"
    return round(new_start, 2), note


def ending_end(start, end, spoken, sounds, payoff_time, tail_seconds, max_tail_seconds):
    """
    Choose where the clip ends: shortly after the payoff/last line, keeping a laugh.

    Args:
        start (float): The (new) start.
        end (float): The chosen end.
        spoken (list[dict]): Words inside the moment.
        sounds (list[dict]): Per-second sound chances.
        payoff_time (float or None): When the payoff happens, if known.
        tail_seconds (float): Normal reaction time kept after the last line.
        max_tail_seconds (float): Longest tail when laughter continues.

    Returns:
        tuple: (new end, note or "").
    """
    if not spoken:
        return end, ""
    last_line_end = spoken[-1]["end"]
    if payoff_time is not None and payoff_time > last_line_end:
        last_line_end = min(end, payoff_time)
    new_end = min(end, last_line_end + tail_seconds)
    # Keep laughing that continues after the last line.
    second = int(new_end)
    while second < len(sounds) and second < last_line_end + max_tail_seconds and second < end:
        if sounds[second].get("laughter", 0) >= 0.3:
            new_end = min(end, second + 1.0)
        second += 1
    if end - new_end < 0.4:
        return end, ""
    return round(new_end, 2), f"Ending trimmed {end - new_end:.1f}s after the payoff"


def plan_trim(start, end, words, sounds, motion, payoff_time, trim_settings, minimum_length):
    """
    Work out the tightened clip: new start/end and which parts to keep.

    Args:
        start (float): Chosen start in the source, seconds.
        end (float): Chosen end.
        words (list[dict]): Timed words (source time).
        sounds (list[dict]): Per-second sound chances (may be empty).
        motion (list[float]): Per-second picture change (may be empty).
        payoff_time (float or None): When the payoff happens (from laughter or the editor).
        trim_settings (TrimSettings): Limits from the channel YAML.
        minimum_length (float): Never make the clip shorter than this.

    Returns:
        dict: {"start", "end", "ranges" (kept parts from the new start; one part
               means no jump cuts), "removed_seconds", "cuts", "notes"}.

    Example:
        Words at 10–12 s and 16–18 s with nothing between: the 4 s gap becomes 0.5 s.
    """
    notes = []
    spoken = words_inside(words, start, end)
    new_start, note = opening_start(start, end, spoken, trim_settings.lead_seconds)
    if note:
        notes.append(note)
    new_end, note = ending_end(new_start, end, spoken, sounds, payoff_time,
                               trim_settings.reaction_tail_seconds, trim_settings.max_tail_seconds)
    if note:
        notes.append(note)
    if new_end - new_start < minimum_length:
        new_start, new_end = start, max(end, start + minimum_length)
        notes = ["Kept as chosen (trimming would make it too short)"]
    spoken = words_inside(words, new_start, new_end)

    # Find gaps between lines that are only dead air.
    gaps = []
    for index in range(len(spoken) - 1):
        gap_start = spoken[index]["end"]
        gap_end = spoken[index + 1]["start"]
        length = gap_end - gap_start
        if length <= trim_settings.max_dead_air_seconds:
            continue
        if something_happens(sounds, motion, gap_start, gap_end):
            continue
        keep = trim_settings.keep_gap_seconds
        # The pause just before the payoff is part of the joke.
        if payoff_time is not None and 0 <= payoff_time - gap_end <= 4.0:
            if length <= trim_settings.comedic_pause_seconds:
                continue
            keep = trim_settings.comedic_pause_seconds
        gaps.append({"start": gap_start, "end": gap_end, "remove": length - keep, "keep": keep})
    # Only the longest few gaps are cut, so the clip is not over-edited.
    gaps.sort(key=removable_seconds, reverse=True)
    gaps = gaps[:trim_settings.max_cuts]
    gaps.sort(key=gap_start_of)

    ranges = []
    cursor = 0.0
    removed = 0.0
    for gap in gaps:
        cut_from = gap["start"] - new_start + gap["keep"] / 2
        cut_to = gap["end"] - new_start - gap["keep"] / 2
        if cut_to - cut_from < 0.3:
            continue
        ranges.append((round(cursor, 3), round(cut_from, 3)))
        cursor = cut_to
        removed += cut_to - cut_from
    ranges.append((round(cursor, 3), round(new_end - new_start, 3)))
    if removed and new_end - new_start - removed < minimum_length:
        ranges = [(0.0, round(new_end - new_start, 3))]
        removed = 0.0
    if removed:
        notes.append(f"Removed {removed:.1f}s of dead air in {len(ranges) - 1} cut(s); comedic pauses kept")
    return {"start": new_start, "end": new_end, "ranges": ranges, "removed_seconds": round(removed, 2),
            "cuts": len(ranges) - 1, "notes": notes}


def removable_seconds(gap):
    """
    Sorting key: how much a gap would shrink.

    Args:
        gap (dict): A gap.

    Returns:
        float: Seconds removable.
    """
    return gap["remove"]


def gap_start_of(gap):
    """
    Sorting key: when a gap starts.

    Args:
        gap (dict): A gap.

    Returns:
        float: Its start.
    """
    return gap["start"]

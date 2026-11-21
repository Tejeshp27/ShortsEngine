# =============================================================
# src/clips/tiktok_content_research.py — what is INSIDE the TikTok
#                                        videos that break out
#
# Used for docs/TIKTOK_CONTENT_RESEARCH.md. The first TikTok study
# (src/clips/tiktok_research.py) only had numbers and captions. This one
# looks at the videos themselves. For each account we picked its own
# breakouts ("hit": 2x+ its usual views), its own flops ("flop": 0.6x or
# less) and a few typical videos, all at least 7 days old.
#
# While the owner's Chrome had TikTok open, the page measured each
# video in memory and saved only numbers (no video files were kept):
#   * content rows: TikTok's own auto-transcript (start, end, words),
#     loudness, picture quality score, resolution and sound type.
#   * frame rows: four pictures per second shrunk to 72x128 pixels,
#     turned into numbers: how much changes between pictures (cuts and
#     motion), brightness, colour, which parts of the screen stay sharp
#     or still (layout, fixed text), plus the sound level every 0.1 s.
#   * labels: Claude looked at 12 small stills per video without knowing
#     which group the video was in and noted layout, caption style and
#     what is happening (docs/research/tiktok_visual_labels_<date>.json).
#
# This file turns those rows into one set of measures per video and
# compares each account's hits with the SAME account's flops, so
# follower count and niche cannot explain the difference.
#
# It makes no network calls and costs nothing.
#
# Run:
#   python -m src.clips.tiktok_content_research \
#       --content cache/tiktok_content_raw.jsonl \
#       --frames cache/tiktok_frames_raw.jsonl \
#       --labels docs/research/tiktok_visual_labels_2026-11-21.json
# =============================================================

import argparse
import json
import re
import statistics
from pathlib import Path

# A change between two pictures (0.25 s apart) counts as a cut when the
# colour mix changes by at least this much (0 = identical colours,
# 1 = completely different)...
CUT_COLOUR_CHANGE = 0.3

# ...the pixels change by at least this much (0-255 scale)...
CUT_PIXEL_CHANGE = 18

# ...and the change is at least this many times bigger than the changes
# just before and after it. A cut is one sudden jump; a moving camera
# changes the picture a lot on every step, so it is not a "spike".
CUT_SPIKE_FACTOR = 2.0

# Sound quieter than (loudest moment - this many dB) counts as quiet.
QUIET_BELOW_PEAK_DB = 30

# Accounts need at least this many hits AND this many flops with a value
# before their comparison is counted.
MIN_VIDEOS_PER_GROUP = 3

# Words that end a sentence in TikTok's auto-transcripts.
SENTENCE_END = (".", "!", "?", "…")


def load_rows(path):
    """
    Read a .jsonl file (one JSON object per line) into a list of dicts.

    Lines that are empty, broken or marked with an "error" are skipped.
    If the same video id appears twice, the last good line wins.

    Args:
        path (str or Path): The file to read.

    Returns:
        dict: Video id → that video's saved row.

    Example:
        rows = load_rows("cache/tiktok_content_raw.jsonl")
        rows["7705539805286796557"]["loudness"]   # -12.4
    """
    rows = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if row.get("error") or not row.get("id"):
            continue
        rows[row["id"]] = row
    return rows


def count_words(text):
    """
    Count the words in a piece of text.

    Args:
        text (str): Any text.

    Returns:
        int: Number of words (groups of letters/digits, apostrophes allowed).

    Example:
        count_words("I don't know, bro!")   # 4
    """
    return len(re.findall(r"[\w']+", text or ""))


def transcript_measures(cues, duration):
    """
    Describe how speech is used in one video from its auto-transcript.

    Args:
        cues (list): [start_seconds, end_seconds, text] for each caption line
            TikTok heard, in time order. May be empty (no speech heard).
        duration (float): Length of the video in seconds.

    Returns:
        dict: has_speech (True/False), words, words_per_second (while
        talking), first_speech_seconds, speech_coverage (share of the
        video with someone talking), longest_pause_seconds, quiet_end_seconds
        (silence after the last words), ends_mid_sentence (True/False),
        hook_question (the first words ask a question), words_first_3s and
        hook_text (first ~12 words). Values that cannot be known are None.

    Example:
        transcript_measures([[0.2, 1.5, "Who did this?"]], 10)["hook_question"]  # True
    """
    words = 0
    talking_seconds = 0.0
    for start, end, text in cues:
        words += count_words(text)
        talking_seconds += max(0.0, end - start)
    measures = {"has_speech": words >= 8, "words": words}
    if words < 8 or not duration:
        for name in ("words_per_second", "first_speech_seconds", "speech_coverage",
                     "longest_pause_seconds", "quiet_end_seconds", "ends_mid_sentence",
                     "hook_question", "words_first_3s", "hook_text"):
            measures[name] = None
        return measures

    measures["words_per_second"] = round(words / max(talking_seconds, 0.5), 2)
    measures["first_speech_seconds"] = round(cues[0][0], 2)
    measures["speech_coverage"] = round(min(1.0, talking_seconds / duration), 3)

    longest_pause = cues[0][0]
    for index in range(1, len(cues)):
        gap = cues[index][0] - cues[index - 1][1]
        if gap > longest_pause:
            longest_pause = gap
    measures["longest_pause_seconds"] = round(longest_pause, 2)
    measures["quiet_end_seconds"] = round(max(0.0, duration - cues[-1][1]), 2)

    last_text = cues[-1][2].strip()
    measures["ends_mid_sentence"] = not last_text.endswith(SENTENCE_END)

    # The "hook" is what is said in the first 3 seconds of talking.
    hook_parts = []
    words_first_3s = 0
    for start, end, text in cues:
        if start <= 3.0:
            words_first_3s += count_words(text)
        if start <= cues[0][0] + 3.0:
            hook_parts.append(text)
    hook_text = " ".join(hook_parts)
    measures["words_first_3s"] = words_first_3s
    measures["hook_question"] = "?" in hook_text
    measures["hook_text"] = " ".join(hook_text.split()[:12])
    return measures


def wordless_stretches(cues, duration, minimum_seconds=2.0):
    """
    List the stretches of a video where nobody says a word.

    TikTok's auto-transcript only writes down words, so laughing,
    screaming, music, gameplay noise and silence all show up as gaps.

    Args:
        cues (list): [start_seconds, end_seconds, text] caption lines in time order.
        duration (float): Length of the video in seconds.
        minimum_seconds (float): Shorter gaps are normal breathing pauses.

    Returns:
        list[tuple]: (start_seconds, end_seconds) for each long enough gap,
        including before the first words and after the last words.

    Example:
        wordless_stretches([[0, 2, "hi"], [5, 6, "bye"]], 6)   # [(2, 5)]
    """
    stretches = []
    previous_end = 0.0
    for start, end, text in cues:
        if start - previous_end >= minimum_seconds:
            stretches.append((previous_end, start))
        previous_end = max(previous_end, end)
    if duration - previous_end >= minimum_seconds:
        stretches.append((previous_end, duration))
    return stretches


def wordless_measures(cues, duration, levels):
    """
    Measure the wordless moments of a video and how loud they are.

    Args:
        cues (list): [start_seconds, end_seconds, text] caption lines.
        duration (float): Length of the video in seconds.
        levels (list[int]): Sound level in dB every 0.1 s (may be empty).

    Returns:
        dict: wordless_beats_per_30s (gaps of 2 s or more, per 30 seconds),
        wordless_share (share of the video inside those gaps) and
        wordless_loudness_vs_talk (dB: how much louder the gaps are than the
        talking; above 0 means laughing/shouting/noise, well below 0 means
        quiet). None when there is too little speech to tell.

    Example:
        wordless_measures(cues, 30, levels)["wordless_beats_per_30s"]   # 2.0
    """
    words = 0
    for start, end, text in cues:
        words += count_words(text)
    if words < 8 or not duration:
        return {"wordless_beats_per_30s": None, "wordless_share": None,
                "wordless_loudness_vs_talk": None}
    stretches = wordless_stretches(cues, duration)
    gap_seconds = 0.0
    for start, end in stretches:
        gap_seconds += end - start
    result = {"wordless_beats_per_30s": round(len(stretches) / duration * 30, 2),
              "wordless_share": round(gap_seconds / duration, 3),
              "wordless_loudness_vs_talk": None}
    if not levels or not stretches:
        return result

    gap_levels = []
    talk_levels = []
    for index, level in enumerate(levels):
        second = index / 10
        inside_gap = False
        for start, end in stretches:
            if start <= second < end:
                inside_gap = True
                break
        if inside_gap:
            gap_levels.append(level)
        else:
            talk_levels.append(level)
    if gap_levels and talk_levels:
        result["wordless_loudness_vs_talk"] = statistics.median(gap_levels) - statistics.median(talk_levels)
    return result


def find_cuts(colour_changes, pixel_changes):
    """
    Find the moments where the picture jumps to a different shot.

    Args:
        colour_changes (list[float]): For each pair of neighbouring
            pictures, how much the colour mix changed (0-1).
        pixel_changes (list[float]): For the same pairs, the average pixel
            change (0-255).

    Returns:
        list[int]: Positions (index into the lists) where a cut happened.

    Example:
        find_cuts([0.1, 0.9, 0.1], [3, 60, 2])   # [1]
    """
    cuts = []
    for index in range(len(pixel_changes)):
        if colour_changes[index] < CUT_COLOUR_CHANGE or pixel_changes[index] < CUT_PIXEL_CHANGE:
            continue
        # Compare with the two changes on each side of this one.
        neighbours = []
        for other in range(index - 2, index + 3):
            if other != index and 0 <= other < len(pixel_changes):
                neighbours.append(pixel_changes[other])
        if neighbours and pixel_changes[index] < CUT_SPIKE_FACTOR * statistics.median(neighbours):
            continue
        # Cuts 0.5 s apart or less are one cut caught on two pictures
        # (or a flash between shots).
        if cuts and index - cuts[-1] <= 2:
            continue
        cuts.append(index)
    return cuts


def percentile(values, share):
    """
    Return the value below which a given share of the values fall.

    Args:
        values (list[float]): Numbers (any order).
        share (float): 0.0-1.0, e.g. 0.95 for the 95th percentile.

    Returns:
        float or None: The value, or None for an empty list.

    Example:
        percentile([1, 2, 3, 4, 5], 0.5)   # 3
    """
    if not values:
        return None
    ordered = sorted(values)
    position = min(len(ordered) - 1, int(round(share * (len(ordered) - 1))))
    return ordered[position]


def sound_measures(levels):
    """
    Describe the sound of one video from its level every 0.1 seconds.

    Args:
        levels (list[int]): Sound level in dB for each 0.1 s (0 = loudest
            possible, -100 = silence).

    Returns:
        dict: quiet_start_seconds (silence before anything is heard),
        loudest_second_position (where the loudest second falls, 0 = start,
        1 = end), loudest_in_last_third (True/False), sound_range_db
        (how much louder the loud parts are than the normal parts) and
        loud_bursts_per_10s (how often the sound jumps well above normal).

    Example:
        sound_measures([-90] * 10 + [-20] * 50)["quiet_start_seconds"]   # 1.0
    """
    empty = {"quiet_start_seconds": None, "loudest_second_position": None,
             "loudest_in_last_third": None, "sound_range_db": None, "loud_bursts_per_10s": None}
    if not levels or len(levels) < 30:
        return empty
    peak = max(levels)
    quiet_start = 0
    for level in levels:
        if level >= peak - QUIET_BELOW_PEAK_DB:
            break
        quiet_start += 1

    # Loudness of each 1-second window (10 levels), to find the loudest second.
    best_start = 0
    best_total = None
    for start in range(0, len(levels) - 9):
        total = sum(levels[start:start + 10])
        if best_total is None or total > best_total:
            best_total = total
            best_start = start
    position = (best_start + 5) / len(levels)

    typical = percentile(levels, 0.5)
    loud = percentile(levels, 0.95)
    # A "burst" is a jump of 8+ dB over the video's normal level, at least 1 s
    # after the previous burst (a shout, a laugh, a sound effect).
    bursts = 0
    last_burst = -10
    for index, level in enumerate(levels):
        if level >= typical + 8 and index - last_burst >= 10:
            bursts += 1
            last_burst = index
    seconds = len(levels) / 10
    return {"quiet_start_seconds": round(quiet_start / 10, 1),
            "loudest_second_position": round(position, 3),
            "loudest_in_last_third": position >= 2 / 3,
            "sound_range_db": loud - typical,
            "loud_bursts_per_10s": round(bursts / seconds * 10, 2)}


def picture_measures(frame_row):
    """
    Describe the picture of one video from the saved frame numbers.

    Args:
        frame_row (dict): One row from the frames file: fps, diffs (pixel
            change between pictures), hists (colour-mix change), bright,
            sat, bandEdge and bandStd (16 horizontal strips, top to bottom).

    Returns:
        dict: cuts_per_10s, first_cut_seconds (None when there is no cut),
        cut_in_first_2s, motion (typical pixel change between pictures that
        are not cuts), brightness (0-255), colourfulness (0-1),
        still_strips (how many of the 16 strips barely change: borders,
        bars or a frozen background) and sharp_strip_share (how much of the
        screen holds sharp detail).

    Example:
        picture_measures(row)["cuts_per_10s"]   # 2.4
    """
    fps = frame_row.get("fps") or 4
    colour_changes = frame_row.get("hists") or []
    pixel_changes = frame_row.get("diffs") or []
    seconds = (len(pixel_changes) + 1) / fps
    cuts = find_cuts(colour_changes, pixel_changes)
    cut_set = set(cuts)
    calm_changes = []
    for index, change in enumerate(pixel_changes):
        if index not in cut_set:
            calm_changes.append(change)
    first_cut = (cuts[0] + 1) / fps if cuts else None

    strip_sharpness = frame_row.get("bandEdge") or []
    strip_change = frame_row.get("bandStd") or []
    still_strips = 0
    for change in strip_change:
        if change < 8:
            still_strips += 1
    sharp_strips = 0
    if strip_sharpness:
        sharpest = max(strip_sharpness) or 1
        for sharpness in strip_sharpness:
            if sharpness >= 0.5 * sharpest:
                sharp_strips += 1

    return {"cuts_per_10s": round(len(cuts) / max(seconds, 1) * 10, 2),
            "first_cut_seconds": first_cut,
            "cut_in_first_2s": first_cut is not None and first_cut <= 2.0,
            "motion": statistics.median(calm_changes) if calm_changes else None,
            "brightness": statistics.mean(frame_row["bright"]) if frame_row.get("bright") else None,
            "colourfulness": statistics.mean(frame_row["sat"]) if frame_row.get("sat") else None,
            "still_strips": still_strips,
            "sharp_strip_share": round(sharp_strips / len(strip_sharpness), 3) if strip_sharpness else None}


def file_measures(content_row):
    """
    Pick out the technical facts TikTok reports about the video file.

    Args:
        content_row (dict): One row from the content file.

    Returns:
        dict: duration, loudness_lufs (TikTok's loudness reading),
        quality_score (TikTok's picture quality score, None when TikTok
        shows 0), height (pixels), shape ("vertical 9:16", "square-ish",
        "horizontal"), original_sound (True when it is not a song) and
        text_stickers (True when TikTok's own text stickers were used).

    Example:
        file_measures({"width": 720, "height": 1280})["shape"]   # "vertical 9:16"
    """
    width = content_row.get("width") or 0
    height = content_row.get("height") or 0
    shape = None
    if width and height:
        ratio = height / width
        if ratio >= 1.6:
            shape = "vertical 9:16"
        elif ratio >= 1.1:
            shape = "tall (4:5-3:4)"
        elif ratio >= 0.9:
            shape = "square"
        else:
            shape = "horizontal"
    quality = None
    try:
        quality = float(content_row.get("vq") or 0) or None
    except ValueError:
        quality = None
    return {"duration": content_row.get("duration"),
            "loudness_lufs": content_row.get("loudness"),
            "quality_score": quality,
            "height": max(width, height) or None,
            "shape": shape,
            "original_sound": content_row.get("music_original"),
            "text_stickers": bool(content_row.get("stickers"))}


def build_videos(content_rows, frame_rows, labels):
    """
    Join the three sources into one record of measures per video.

    Args:
        content_rows (dict): id → content row (transcript and file facts).
        frame_rows (dict): id → frame row (picture and sound numbers).
        labels (dict): id → Claude's blind visual notes (may be empty).

    Returns:
        list[dict]: One dict per video with id, author, group, rel, genre and
        every measure that could be worked out (missing ones are None).
    """
    videos = []
    all_ids = list(content_rows.keys())
    for video_id in frame_rows:
        if video_id not in content_rows:
            all_ids.append(video_id)
    for video_id in all_ids:
        base = content_rows.get(video_id) or frame_rows.get(video_id)
        video = {"id": video_id, "author": base["author"], "group": base["group"],
                 "rel": base["rel"], "genre": base["genre"]}
        content = content_rows.get(video_id)
        if content:
            video.update(file_measures(content))
            video.update(transcript_measures(content.get("cues") or [], content.get("duration") or 0))
        frames = frame_rows.get(video_id)
        if frames:
            video.update(picture_measures(frames))
            video.update(sound_measures(frames.get("audio_env") or []))
        if content:
            levels = []
            if frames:
                levels = frames.get("audio_env") or []
            video.update(wordless_measures(content.get("cues") or [], content.get("duration") or 0, levels))
        if video_id in labels:
            for name, value in labels[video_id].items():
                video["label_" + name] = value
        videos.append(video)
    return videos


def compare_within_accounts(videos, measure, genre=None):
    """
    Compare each account's hits with its own flops on one measure.

    For a number (e.g. cuts per 10 s), each account's hit median is
    compared with its flop median. For a yes/no measure the share of
    "yes" is compared instead.

    Args:
        videos (list[dict]): Videos from build_videos.
        measure (str): The measure's name, e.g. "cuts_per_10s".
        genre (str): Only use this genre ("streamer_clips" or "other"),
            or None for all videos.

    Returns:
        dict or None: accounts (how many accounts had enough of both),
        hit and flop (median over accounts of each account's value),
        typical (the same for typical videos, or None), hits_higher and
        flops_higher (number of accounts where that side was higher).
        None when no account had enough videos.

    Example:
        compare_within_accounts(videos, "cuts_per_10s", "streamer_clips")
        # {"accounts": 30, "hit": 2.1, "flop": 1.6, "hits_higher": 21, ...}
    """
    by_account = {}
    for video in videos:
        if genre and video["genre"] != genre:
            continue
        value = video.get(measure)
        if value is None:
            continue
        if isinstance(value, bool):
            value = 1.0 if value else 0.0
        groups = by_account.setdefault(video["author"], {"hit": [], "flop": [], "typical": []})
        groups[video["group"]].append(value)

    hit_values = []
    flop_values = []
    typical_values = []
    hits_higher = 0
    flops_higher = 0
    for groups in by_account.values():
        if len(groups["hit"]) < MIN_VIDEOS_PER_GROUP or len(groups["flop"]) < MIN_VIDEOS_PER_GROUP:
            continue
        hit_value = statistics.mean(groups["hit"])
        flop_value = statistics.mean(groups["flop"])
        hit_values.append(hit_value)
        flop_values.append(flop_value)
        if groups["typical"]:
            typical_values.append(statistics.mean(groups["typical"]))
        if hit_value > flop_value:
            hits_higher += 1
        elif flop_value > hit_value:
            flops_higher += 1
    if not hit_values:
        return None
    return {"accounts": len(hit_values),
            "hit": statistics.median(hit_values),
            "flop": statistics.median(flop_values),
            "typical": statistics.median(typical_values) if typical_values else None,
            "hits_higher": hits_higher,
            "flops_higher": flops_higher}


def count_by_group(videos, measure, genre=None):
    """
    Count how often each value of a label appears among hits, flops and typical videos.

    Args:
        videos (list[dict]): Videos from build_videos.
        measure (str): A measure with a few text values, e.g. "label_layout".
        genre (str): Only this genre, or None for all.

    Returns:
        dict: value → {"hit": n, "flop": n, "typical": n}.

    Example:
        count_by_group(videos, "shape")["vertical 9:16"]["hit"]   # 210
    """
    counts = {}
    for video in videos:
        if genre and video["genre"] != genre:
            continue
        value = video.get(measure)
        if value is None:
            continue
        values = value if isinstance(value, list) else [value]
        for one_value in values:
            entry = counts.setdefault(one_value, {"hit": 0, "flop": 0, "typical": 0})
            entry[video["group"]] += 1
    return counts


# Measures shown in the main table: (name, plain-English label, number format).
MEASURES = [
    ("duration", "Length (s)", "{:.0f}"),
    ("cuts_per_10s", "Cuts per 10 s", "{:.1f}"),
    ("cut_in_first_2s", "Cut in the first 2 s", "{:.0%}"),
    ("motion", "Movement between pictures (0-255)", "{:.1f}"),
    ("brightness", "Brightness (0-255)", "{:.0f}"),
    ("colourfulness", "Colourfulness (0-1)", "{:.2f}"),
    ("still_strips", "Still strips of screen (of 16)", "{:.1f}"),
    ("sharp_strip_share", "Share of screen that is sharp", "{:.0%}"),
    ("loudness_lufs", "Loudness (LUFS, TikTok's reading)", "{:.1f}"),
    ("quality_score", "TikTok picture quality score", "{:.1f}"),
    ("height", "Resolution (long side, px)", "{:.0f}"),
    ("original_sound", "Original sound (not a song)", "{:.0%}"),
    ("quiet_start_seconds", "Silence before first sound (s)", "{:.1f}"),
    ("loudest_second_position", "Where the loudest second falls (0=start, 1=end)", "{:.2f}"),
    ("loudest_in_last_third", "Loudest second in the last third", "{:.0%}"),
    ("sound_range_db", "Loud parts above normal (dB)", "{:.0f}"),
    ("loud_bursts_per_10s", "Loud bursts per 10 s", "{:.1f}"),
    ("has_speech", "Has speech", "{:.0%}"),
    ("words_per_second", "Words per second while talking", "{:.2f}"),
    ("first_speech_seconds", "First words at (s)", "{:.1f}"),
    ("words_first_3s", "Words in the first 3 s", "{:.1f}"),
    ("speech_coverage", "Share of video with talking", "{:.0%}"),
    ("longest_pause_seconds", "Longest pause in talking (s)", "{:.1f}"),
    ("wordless_beats_per_30s", "Wordless stretches of 2 s+ per 30 s", "{:.2f}"),
    ("wordless_share", "Share of video in those stretches", "{:.0%}"),
    ("wordless_loudness_vs_talk", "Wordless stretches vs talking (dB)", "{:+.1f}"),
    ("quiet_end_seconds", "Time after the last words (s)", "{:.1f}"),
    ("ends_mid_sentence", "Ends mid-sentence", "{:.0%}"),
    ("hook_question", "Opens with a question", "{:.0%}"),
]


def format_value(value, pattern):
    """
    Format a number for the report, or a dash when it is missing.

    Args:
        value (float or None): The number.
        pattern (str): A format pattern such as "{:.1f}".

    Returns:
        str: The formatted number, or "-".
    """
    if value is None:
        return "-"
    return pattern.format(value)


def measures_table(videos, genre, title):
    """
    Write the main hits-versus-flops table for one genre.

    Args:
        videos (list[dict]): Videos from build_videos.
        genre (str): "streamer_clips", "other" or None for all.
        title (str): Heading above the table.

    Returns:
        str: A Markdown section.
    """
    lines = [f"### {title}\n",
             "Each account's hits compared with **its own** flops. Values are the median "
             "over accounts of each account's average. \"Hits higher\" counts the accounts "
             "where the hits had the higher value.\n",
             "| Measure | Hits | Typical | Flops | Accounts | Hits higher | Flops higher |",
             "|---|---|---|---|---|---|---|"]
    for name, label, pattern in MEASURES:
        result = compare_within_accounts(videos, name, genre)
        if not result:
            continue
        lines.append(f"| {label} | {format_value(result['hit'], pattern)} | "
                     f"{format_value(result['typical'], pattern)} | "
                     f"{format_value(result['flop'], pattern)} | {result['accounts']} | "
                     f"{result['hits_higher']} | {result['flops_higher']} |")
    return "\n".join(lines) + "\n"


def label_table(videos, measure, genre, title):
    """
    Write a table of how often each visual label appears among hits and flops.

    Args:
        videos (list[dict]): Videos from build_videos.
        measure (str): The label, e.g. "label_layout".
        genre (str): "streamer_clips", "other" or None.
        title (str): Heading above the table.

    Returns:
        str: A Markdown section ("" when there are no labels).
    """
    counts = count_by_group(videos, measure, genre)
    if not counts:
        return ""
    totals = {"hit": 0, "flop": 0, "typical": 0}
    for video in videos:
        if (genre is None or video["genre"] == genre) and video.get(measure) is not None:
            totals[video["group"]] += 1
    lines = [f"#### {title}\n", f"Videos labelled: {totals['hit']} hits, {totals['flop']} flops.\n",
             "| Label | Hits | Flops | Hits ÷ flops |", "|---|---|---|---|"]
    ordered = sorted(counts.items(), key=lambda item: -(item[1]["hit"] + item[1]["flop"]))
    for value, entry in ordered:
        hit_share = entry["hit"] / max(totals["hit"], 1)
        flop_share = entry["flop"] / max(totals["flop"], 1)
        ratio = (hit_share + 0.01) / (flop_share + 0.01)
        lines.append(f"| {value} | {hit_share:.0%} ({entry['hit']}) | "
                     f"{flop_share:.0%} ({entry['flop']}) | {ratio:.2f}x |")
    return "\n".join(lines) + "\n"


def build_report(videos):
    """
    Write all tables for the content study as Markdown.

    Args:
        videos (list[dict]): Videos from build_videos.

    Returns:
        str: The whole Markdown report.
    """
    counts = {}
    for video in videos:
        key = (video["genre"], video["group"])
        counts[key] = counts.get(key, 0) + 1
    parts = ["# TikTok content study: tables\n",
             "Generated by `python -m src.clips.tiktok_content_research`. "
             "Written up in `docs/TIKTOK_CONTENT_RESEARCH.md`.\n",
             "| Genre | Hits | Typical | Flops |", "|---|---|---|---|"]
    for genre in ("streamer_clips", "other"):
        parts.append(f"| {genre} | {counts.get((genre, 'hit'), 0)} | "
                     f"{counts.get((genre, 'typical'), 0)} | {counts.get((genre, 'flop'), 0)} |")
    parts.append("")
    parts.append(measures_table(videos, "streamer_clips", "Streamer-clip accounts"))
    parts.append(measures_table(videos, "other", "Other accounts (all genres)"))
    for measure, title in (("shape", "Video shape (from the file)"),
                           ("label_layout", "Layout (blind visual label)"),
                           ("label_captions", "On-screen captions (blind visual label)"),
                           ("label_banner", "Fixed title/text banner (blind visual label)"),
                           ("label_people", "Who is on screen (blind visual label)"),
                           ("label_content", "What happens (blind visual label)")):
        for genre, genre_title in (("streamer_clips", "streamer clips"), ("other", "other accounts")):
            parts.append(label_table(videos, measure, genre, f"{title}: {genre_title}"))
    return "\n".join(parts)


def main():
    """
    Read the saved rows, write the tables and a per-video measures file.

    Command-line inputs: --content, --frames, --labels, --output and --measures.
    """
    parser = argparse.ArgumentParser(description="Compare what is inside TikTok hits and flops.")
    parser.add_argument("--content", default="cache/tiktok_content_raw.jsonl",
                        help="transcript and file facts saved from the page")
    parser.add_argument("--frames", default="cache/tiktok_frames_raw.jsonl",
                        help="picture and sound numbers saved from the page")
    parser.add_argument("--labels", default=None, help="blind visual labels (JSON: id → notes)")
    parser.add_argument("--output", default="docs/research/tiktok_content_analysis.md",
                        help="where to write the Markdown tables")
    parser.add_argument("--measures", default="docs/research/tiktok_content_measures_2026-11-21.jsonl",
                        help="where to write one line of measures per video")
    arguments = parser.parse_args()

    content_rows = load_rows(arguments.content)
    frame_rows = load_rows(arguments.frames)
    labels = {}
    if arguments.labels and Path(arguments.labels).exists():
        labels = json.loads(Path(arguments.labels).read_text(encoding="utf-8"))
    videos = build_videos(content_rows, frame_rows, labels)

    Path(arguments.output).write_text(build_report(videos), encoding="utf-8")
    lines = []
    for video in videos:
        lines.append(json.dumps(video, ensure_ascii=False))
    Path(arguments.measures).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"{len(videos)} videos ({len(content_rows)} with transcripts/file facts, "
          f"{len(frame_rows)} with picture numbers) → {arguments.output}")


if __name__ == "__main__":
    main()

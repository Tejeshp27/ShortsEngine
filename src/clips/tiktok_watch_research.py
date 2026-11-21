# =============================================================
# src/clips/tiktok_watch_research.py — compare what happens inside
#                                      streamer-clip hits and flops
#                                      (the "watching" rounds)
#
# Used for docs/TIKTOK_CONTENT_RESEARCH.md (sections 2.2 replication and
# 2.4b). While TikTok was open in the owner's Chrome, a watcher tab read
# 4,670 streamer clips from 26 clip accounts in memory and kept only:
#   * watch rows (cache/tiktok_watch_raw.jsonl): auto-transcript lines,
#     sound level every 0.1 s, length, and how the clip did against the
#     same account's usual views ("rel": 3.0 = three times usual);
#   * face rows (docs/research/tiktok_faces_<date>.jsonl.gz): for every
#     half second, the strongest happiness/surprise/... read by FER+ on any
#     face, and how many faces there were (src/clips/expressions.py).
#
# This file turns those rows into per-clip measures and compares each
# account's hits with its own flops, at two strictness levels:
#   "2x vs 0.6x" (all clear hits/flops) and "5x vs 0.3x" (big breakouts
#   vs real flops). It makes no network calls and costs nothing.
#
# Run:
#   python -m src.clips.tiktok_watch_research \
#       --watch cache/tiktok_watch_raw.jsonl \
#       --faces docs/research/tiktok_faces_2026-11-21.jsonl.gz
# =============================================================

import argparse
import gzip
import json
import statistics
from pathlib import Path

from src.clips import tiktok_content_research as content

# A face counts as laughing / shocked when the model's chance is at least this.
LAUGH_FACE = 0.9
SHOCK_FACE = 0.5

# Each account needs at least this many hits AND flops to be compared.
MIN_PER_GROUP = 3

# (label, hit threshold, flop threshold) for the two comparisons.
LEVELS = [("2x+ vs 0.6x or less", 2.0, 0.6), ("5x+ vs 0.3x or less", 5.0, 0.3)]


def read_jsonl(path):
    """
    Read a .jsonl or .jsonl.gz file into a list of dicts (bad lines skipped).

    Args:
        path (str or Path): The file.

    Returns:
        list[dict]: One dict per good line.
    """
    opener = gzip.open if str(path).endswith(".gz") else open
    rows = []
    with opener(path, "rt", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    return rows


def faces_by_video(rows):
    """
    Join the face rows (sent in parts of up to 40 pictures) per video.

    Args:
        rows (list[dict]): {"id", "per_frame": [...]} rows.

    Returns:
        dict: Video id → list of per-picture readings sorted by time.
    """
    joined = {}
    for row in rows:
        joined.setdefault(row["id"], []).extend(row.get("per_frame") or [])
    for readings in joined.values():
        readings.sort(key=reading_time)
    return joined


def reading_time(reading):
    """
    Sorting key: when a face reading was taken.

    Args:
        reading (dict): One per-picture reading with "time".

    Returns:
        float: Seconds.
    """
    return reading.get("time", 0.0)


def count_runs(flags, minimum=2):
    """
    Count stretches of at least `minimum` True values in a row.

    Args:
        flags (list[bool]): One flag per picture.
        minimum (int): Shortest stretch that counts.

    Returns:
        int: Number of stretches.

    Example:
        count_runs([True, True, False, True])   # 1
    """
    runs = 0
    current = 0
    for flag in flags + [False]:
        if flag:
            current += 1
            continue
        if current >= minimum:
            runs += 1
        current = 0
    return runs


def face_measures(readings, cues, duration):
    """
    Describe the faces in one clip.

    Args:
        readings (list[dict]): Per-picture readings (happiness, surprise,
            faces, time), sorted by time.
        cues (list): Transcript lines [start, end, text].
        duration (float): Clip length in seconds.

    Returns:
        dict: peak_happiness, peak_surprise, laugh_face_runs_per_30s,
        shock_face_no_words_per_30s (surprised face while nobody talks) and
        two_plus_faces_share. All None when there are no readings.
    """
    empty = {"peak_happiness": None, "peak_surprise": None, "laugh_face_runs_per_30s": None,
             "shock_face_no_words_per_30s": None, "two_plus_faces_share": None}
    if not readings or not duration:
        return empty
    per_30 = 30 / max(duration, 1)
    laughing = []
    shocked_quiet = []
    two_plus = 0
    peak_happiness = 0.0
    peak_surprise = 0.0
    for reading in readings:
        happiness = reading.get("happiness", 0.0)
        surprise = reading.get("surprise", 0.0)
        peak_happiness = max(peak_happiness, happiness)
        peak_surprise = max(peak_surprise, surprise)
        laughing.append(happiness >= LAUGH_FACE)
        shocked_quiet.append(surprise >= SHOCK_FACE and not someone_talking(cues, reading.get("time", 0.0)))
        if reading.get("faces", 0) >= 2:
            two_plus += 1
    return {"peak_happiness": peak_happiness, "peak_surprise": peak_surprise,
            "laugh_face_runs_per_30s": count_runs(laughing) * per_30,
            "shock_face_no_words_per_30s": count_runs(shocked_quiet) * per_30,
            "two_plus_faces_share": two_plus / len(readings)}


def someone_talking(cues, time_seconds):
    """
    Check whether a transcript line covers a moment (with 0.2 s slack).

    Args:
        cues (list): Transcript lines [start, end, text].
        time_seconds (float): The moment.

    Returns:
        bool: True when someone is talking then.
    """
    for start, end, text in cues:
        if start - 0.2 <= time_seconds <= end + 0.2:
            return True
    return False


def clip_measures(row, readings):
    """
    Work out every measure for one watched clip.

    Args:
        row (dict): A watch row (cues, audio_env, duration, rel, author).
        readings (list[dict]): Its face readings (may be empty).

    Returns:
        dict or None: author, rel and the measures; None when the clip has
        too little speech or no sound to measure.
    """
    cues = row.get("cues") or []
    levels = row.get("audio_env") or []
    if not cues or not levels:
        return None
    duration = row.get("duration") or len(levels) / 10
    wordless = content.wordless_measures(cues, duration, levels)
    if wordless["wordless_share"] is None:
        return None
    speech = content.transcript_measures(cues, duration)
    measures = {"author": row["author"], "rel": row["rel"], "duration": duration,
                "speech_coverage": speech["speech_coverage"],
                "words_first_3s": speech["words_first_3s"],
                "quiet_end_seconds": speech["quiet_end_seconds"]}
    measures.update(wordless)
    measures.update(face_measures(readings, cues, duration))
    return measures


def compare(clips, measure, hit_at, flop_at):
    """
    Compare each account's hits with its own flops on one measure.

    Args:
        clips (list[dict]): From clip_measures.
        measure (str): The measure's name.
        hit_at (float): A clip is a hit at this many times usual views or more.
        flop_at (float): A clip is a flop at this many times usual or less.

    Returns:
        dict or None: accounts, hit, flop (medians of account averages),
        hits_higher, flops_higher. None when no account qualifies.
    """
    by_account = {}
    for clip in clips:
        value = clip.get(measure)
        if value is None:
            continue
        if clip["rel"] >= hit_at:
            group = "hit"
        elif clip["rel"] <= flop_at:
            group = "flop"
        else:
            continue
        by_account.setdefault(clip["author"], {"hit": [], "flop": []})[group].append(float(value))
    hits = []
    flops = []
    hits_higher = 0
    flops_higher = 0
    for groups in by_account.values():
        if len(groups["hit"]) < MIN_PER_GROUP or len(groups["flop"]) < MIN_PER_GROUP:
            continue
        hit_value = statistics.mean(groups["hit"])
        flop_value = statistics.mean(groups["flop"])
        hits.append(hit_value)
        flops.append(flop_value)
        if hit_value > flop_value:
            hits_higher += 1
        elif flop_value > hit_value:
            flops_higher += 1
    if not hits:
        return None
    return {"accounts": len(hits), "hit": statistics.median(hits), "flop": statistics.median(flops),
            "hits_higher": hits_higher, "flops_higher": flops_higher}


# (measure, label, number format)
MEASURES = [
    ("speech_coverage", "Share of the clip with talking", "{:.0%}"),
    ("wordless_share", "Share of the clip with no words (2 s+ gaps)", "{:.0%}"),
    ("loud_wordless_per_30s", "Loud wordless stretches per 30 s", "{:.2f}"),
    ("quiet_wordless_per_30s", "Quiet wordless stretches per 30 s", "{:.2f}"),
    ("words_first_3s", "Words in the first 3 s", "{:.1f}"),
    ("quiet_end_seconds", "Time after the last words (s)", "{:.1f}"),
    ("duration", "Length (s)", "{:.0f}"),
    ("peak_happiness", "Highest happiness on a face", "{:.2f}"),
    ("laugh_face_runs_per_30s", "Laughing-face moments per 30 s", "{:.2f}"),
    ("peak_surprise", "Highest surprise on a face", "{:.2f}"),
    ("shock_face_no_words_per_30s", "Shocked face while nobody talks, per 30 s", "{:.3f}"),
    ("two_plus_faces_share", "Share of pictures with 2+ faces", "{:.0%}"),
]


def build_report(clips):
    """
    Write the hits-versus-flops tables for the watched clips.

    Args:
        clips (list[dict]): From clip_measures.

    Returns:
        str: Markdown.
    """
    parts = ["# TikTok watching rounds: tables\n",
             f"Generated by `python -m src.clips.tiktok_watch_research` from {len(clips)} "
             "streamer clips. Each account's hits are compared with its own flops; values "
             "are medians of account averages.\n"]
    for label, hit_at, flop_at in LEVELS:
        parts.append(f"## {label}\n")
        parts.append("| Measure | Hits | Flops | Accounts | Hits higher | Flops higher |")
        parts.append("|---|---|---|---|---|---|")
        for name, title, pattern in MEASURES:
            result = compare(clips, name, hit_at, flop_at)
            if not result:
                continue
            parts.append(f"| {title} | {pattern.format(result['hit'])} | {pattern.format(result['flop'])} | "
                         f"{result['accounts']} | {result['hits_higher']} | {result['flops_higher']} |")
        parts.append("")
    return "\n".join(parts)


def main():
    """
    Read the watch and face rows, write the tables, print where they went.

    Command-line inputs: --watch, --faces and --output.
    """
    parser = argparse.ArgumentParser(description="Compare watched TikTok streamer-clip hits and flops.")
    parser.add_argument("--watch", default="cache/tiktok_watch_raw.jsonl", help="watch rows (.jsonl)")
    parser.add_argument("--faces", default="docs/research/tiktok_faces_2026-11-21.jsonl.gz",
                        help="face rows (.jsonl or .jsonl.gz)")
    parser.add_argument("--output", default="docs/research/tiktok_watch_analysis.md",
                        help="where to write the Markdown tables")
    arguments = parser.parse_args()

    faces = faces_by_video(read_jsonl(arguments.faces)) if Path(arguments.faces).exists() else {}
    clips = []
    seen = set()
    for row in read_jsonl(arguments.watch):
        if row.get("error") or row.get("id") in seen or "rel" not in row:
            continue
        seen.add(row["id"])
        measures = clip_measures(row, faces.get(row["id"], []))
        if measures:
            clips.append(measures)
    Path(arguments.output).write_text(build_report(clips), encoding="utf-8")
    print(f"{len(clips)} clips measured → {arguments.output}")


if __name__ == "__main__":
    main()

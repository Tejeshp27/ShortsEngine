# =============================================================
# Optional removal of long, genuinely silent gaps before rendering.
# Game sound, laughter and spoken filler are kept. Caption times are
# remapped to match cuts; original selected timestamps remain in notes.
# =============================================================

import re
import subprocess


def keep_ranges(duration, silent_spans, minimum_gap=2.5, padding=0.4):
    """
    Convert duration and silent start/end pairs into kept intervals; preserve padding around long silent gaps.

    Args:
        duration: The recording or selected interval length, in seconds.
        silent_spans: Pairs of silence start and end times relative to the selected moment.
        minimum_gap: The shortest silence duration to remove, in seconds.
        padding: Seconds of quiet audio to keep on each side of a removed silence.

    Returns:
        Kept start/end intervals with padding around removed silence.
    """
    ranges = []
    cursor = 0.0
    for start, end in sorted(silent_spans):
        if end - start < minimum_gap:
            continue
        cut_start = max(cursor, start + padding)
        cut_end = min(duration, end - padding)
        if cut_start > cursor:
            ranges.append((cursor, cut_start))
        cursor = max(cursor, cut_end)
    if cursor < duration:
        ranges.append((cursor, duration))
    return ranges or [(0, duration)]


def remap_words(words, start, ranges):
    """
    Move absolute timed words into concatenated kept intervals; return captions aligned to the edited video.

    Args:
        words: Timed speech entries containing word, start and end values in seconds.
        start: The selected start time, in seconds in the original recording.
        ranges: Kept start/end intervals, relative to the selected moment.

    Returns:
        Timed words aligned with the concatenated edited video.
    """
    kept = []
    offset = 0.0
    for begin, end in ranges:
        for word in words:
            relative_start = word["start"] - start
            relative_end = word["end"] - start
            if relative_end <= begin or relative_start >= end:
                continue
            # Copy every mark (speaker, bleep...) and move only the times.
            moved = dict(word)
            moved["start"] = offset + max(begin, relative_start) - begin
            moved["end"] = offset + min(end, relative_end) - begin
            kept.append(moved)
        offset += end - begin
    return kept


def remove_long_silence(source, start, end, words, destination, settings):
    """
    Optionally cut true silence from a selected local moment; return video path, start/end, captions and temporary path.

    Args:
        source: The path to the original local video file.
        start: The selected start time, in seconds in the original recording.
        end: The selected end time, in seconds in the original recording.
        words: Timed speech entries containing word, start and end values in seconds.
        destination: The path where the edited temporary video will be written.
        settings: The optional editing settings dictionary from the channel YAML.

    Returns:
        The source path, edited start/end, remapped captions and optional cleanup path.
    """
    if not settings.get("remove_long_silence", False):
        return source, start, end, words, None
    from src.video.clip_render import probe_video
    if not probe_video(source)["has_audio"]:
        return source, start, end, words, None
    duration = end - start
    noise = min(-20, max(-70, float(settings.get("silence_db", -45))))
    gap = min(10, max(1, float(settings.get("minimum_silence_seconds", 2.5))))
    detected = subprocess.run(["ffmpeg", "-nostdin", "-v", "info", "-ss", str(start), "-t", str(duration),
                               "-i", source, "-vn", "-af", f"silencedetect=noise={noise}dB:d={gap}",
                               "-f", "null", "-"], capture_output=True, text=True, check=True)
    silent = []
    begin = None
    for kind, number in re.findall(r"silence_(start|end): ([\d.]+)", detected.stderr):
        if kind == "start":
            begin = float(number)
        elif begin is not None:
            silent.append((begin, float(number)))
            begin = None
    if begin is not None:
        silent.append((begin, duration))
    ranges = keep_ranges(duration, silent, gap)
    if ranges == [(0, duration)]:
        return source, start, end, words, None
    return cut_and_join(source, start, end, ranges, words, destination)


def cut_and_join(source, start, end, ranges, words, destination):
    """
    Keep only some parts of a moment and join them into one new video.

    Args:
        source: The original video file.
        start: The moment's start in the source, in seconds.
        end: The moment's end.
        ranges: Kept (begin, finish) pairs, counted from the moment's start.
        words: Timed words in source time.
        destination: Where the joined video is written (a temporary file).

    Returns:
        The edited video's path, its start (0) and end (total kept length),
        the captions moved to the new timeline, and the temporary path to delete.
    """
    duration = end - start
    filters = []
    streams = []
    for index, (begin, finish) in enumerate(ranges):
        filters.append(f"[0:v]trim=start={begin}:end={finish},setpts=PTS-STARTPTS[v{index}]")
        filters.append(f"[0:a]atrim=start={begin}:end={finish},asetpts=PTS-STARTPTS[a{index}]")
        streams.append(f"[v{index}][a{index}]")
    filters.append("".join(streams) + f"concat=n={len(ranges)}:v=1:a=1[v][a]")
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-ss", str(start), "-t", str(duration),
                    "-i", source, "-filter_complex", ";".join(filters), "-map", "[v]", "-map", "[a]",
                    "-c:v", "libx264", "-preset", "fast", "-crf", "18", "-c:a", "aac", destination], check=True)
    total = 0.0
    for begin, finish in ranges:
        total += finish - begin
    return destination, 0.0, total, remap_words(words, start, ranges), destination


def trim_quiet_start(start, end, words, lead_seconds=0.25, minimum_length=5.0):
    """
    Move a clip's start to just before the first spoken word, so it doesn't open on dead air.

    A viewer scrolling a feed decides in the first second or two; a
    silent opening loses them before anything happens.

    Args:
        start (float): The chosen start in the source, in seconds.
        end (float): The chosen end in the source, in seconds.
        words (list[dict]): The source's timed words.
        lead_seconds (float): How much sound to keep before the first word.
        minimum_length (float): Never trim a clip shorter than this many seconds.

    Returns:
        float: The new start (unchanged when nobody speaks, the first word is
               already near the start, or trimming would make the clip too short).

    Example:
        trim_quiet_start(10.0, 40.0, [{"word": "wait", "start": 12.0, "end": 12.3}])
        # → 11.75
    """
    first_spoken = None
    for word in words:
        if word["end"] <= start or word["start"] >= end:
            continue
        if first_spoken is None or word["start"] < first_spoken:
            first_spoken = word["start"]
    if first_spoken is None:
        return start
    new_start = first_spoken - lead_seconds
    if new_start <= start or end - new_start < minimum_length:
        return start
    return round(new_start, 2)


def trim_quiet_end(start, end, words, tail_seconds=0.8, max_quiet_seconds=2.5, minimum_length=5.0):
    """
    End a clip shortly after the last spoken word when a long quiet stretch follows it.

    A Short that trails off for many seconds after the payoff gets swiped
    away; ending soon after the last line also makes it loop cleanly.
    A short quiet ending (up to max_quiet_seconds) is kept as it is.

    Args:
        start (float): The chosen start in the source, in seconds.
        end (float): The chosen end in the source, in seconds.
        words (list[dict]): The source's timed words.
        tail_seconds (float): How much sound to keep after the last word.
        max_quiet_seconds (float): Only trim when the quiet ending is longer than this.
        minimum_length (float): Never make the clip shorter than this many seconds.

    Returns:
        float: The new end (unchanged when there is no long quiet ending).

    Example:
        trim_quiet_end(0.0, 30.0, [{"word": "done.", "start": 20.0, "end": 21.0}])
        # → 21.8
    """
    last_spoken = None
    for word in words:
        if word["end"] <= start or word["start"] >= end:
            continue
        if last_spoken is None or word["end"] > last_spoken:
            last_spoken = word["end"]
    if last_spoken is None or end - last_spoken <= max_quiet_seconds:
        return end
    new_end = last_spoken + tail_seconds
    if new_end - start < minimum_length:
        return end
    return round(new_end, 2)

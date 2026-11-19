# =============================================================
# src/clips/live_recorder.py — the rolling live recorder
#
# The clips supervisor (src/clips/runtime.py) calls
# manage_live_recording() every few seconds. Each call:
#
#   1. notices recordings whose capture process has finished,
#   2. stops recordings that must stop (recording switched off,
#      low disk, streamer removed from the approved list),
#   3. checks who is live (one Twitch API call for every Twitch
#      streamer) and starts a capture process for the most-watched
#      live streamers, up to max_concurrent at once and at most
#      max_hours_per_streamer_per_day each,
#   4. turns every finished chunk into a normal recording: it adds the
#      last couple of minutes of the previous chunk (so a moment on the
#      boundary is not cut in half), works out how much chat and how
#      many viewer-made clips each part of the chunk got, and imports
#      it with the existing importer. The clip pipeline then picks the
#      best moments, and src/clips/retention.py deletes the chunk.
#
# Settings: the live_recording section of channels/<channel>.yaml.
# Each capture runs as its own process (src/clips/live_capture.py),
# so a recording keeps going if the supervisor restarts.
# =============================================================

import bisect
import json
import os
import re
import shutil
import signal
import sqlite3
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from src.clips.live_capture import CHAT_FILE, CLOCK_FILE, DONE_FILE, SEGMENT_LIST, stream_url
from src.clips.settings import load_clip_settings
from src.storage.database import get_connection

ROOT = Path(__file__).resolve().parents[2]

# A streamer whose last recording failed straight away (for example the
# stream went offline) is not retried for this long, so a broken stream
# does not start a new process every minute.
FAILED_START_WAIT_SECONDS = 600

# A recording that lasted less than this is treated as a failed start.
SHORT_SESSION_SECONDS = 120

# Extra free space (1 GB) required beyond a chunk's own size before joining it.
SPARE_DISK_BYTES = 1024 ** 3

# A viewer clip counts as "made live" when its place in the broadcast is
# within this many seconds of when it was created. Clips made later from
# the past broadcast are far off and are ignored.
LIVE_CLIP_TOLERANCE_SECONDS = 120

# When each channel last checked who is live. Kept in memory: after a
# restart the first tick simply checks again, which is harmless.
LAST_LIVE_CHECK = {}

# Capture processes started by this supervisor, by session ID. Polling
# them lets the operating system clean up finished processes.
CAPTURE_PROCESSES = {}


# ---------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------

def read_jsonl(path):
    """
    Read a file with one JSON object per line, skipping damaged lines.

    Args:
        path (Path): The .jsonl file (it may not exist yet).

    Returns:
        list[dict]: The objects in file order (empty if the file is missing).
    """
    rows = []
    if not path.is_file():
        return rows
    with open(path, encoding="utf-8") as file:
        for line in file:
            try:
                rows.append(json.loads(line))
            except ValueError:
                # A crash can leave half a line at the end; ignore it.
                continue
    return rows


def media_duration(path):
    """
    Measure how long a video file is, using ffprobe.

    Args:
        path (Path): The video file.

    Returns:
        float: Its length in seconds (0 when ffprobe cannot read it).
    """
    result = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
                            capture_output=True, text=True)
    try:
        return float(result.stdout.strip())
    except ValueError:
        return 0.0


def free_disk_gb():
    """Return the free space on the project's disk, in gigabytes."""
    return shutil.disk_usage(ROOT).free / 1024 ** 3


def rfc3339(seconds):
    """
    Turn a clock time into Twitch's date format.

    Args:
        seconds (float): A time from time.time().

    Returns:
        str: For example "2026-11-19T04:30:00Z".
    """
    return datetime.fromtimestamp(seconds, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_rfc3339(text):
    """
    Turn a Twitch date such as "2026-11-19T04:30:00Z" into a clock time.

    Args:
        text (str): The date text.

    Returns:
        float: Seconds since 1970, like time.time().
    """
    return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()


# ---------------------------------------------------------------
# Matching chat (clock time) to moments in the video (stream time)
# ---------------------------------------------------------------

def stream_time_at(wall, clock):
    """
    Work out which moment of the recording was being saved at a given clock time.

    FFmpeg logs (clock time, stream time) every few seconds. Between two
    log lines the stream advances with the clock, but never past the next
    logged stream time (it pauses during an ad break, for example).

    Args:
        wall (float): A clock time, e.g. when a chat message arrived.
        clock (list[dict]): {"wall", "stream"} entries sorted by "wall".

    Returns:
        float or None: Seconds into the recorded stream, or None before recording began.

    Example:
        stream_time_at(105, [{"wall": 100, "stream": 0}, {"wall": 110, "stream": 10}])  # → 5
    """
    walls = []
    for sample in clock:
        walls.append(sample["wall"])
    # bisect finds where `wall` would go in the sorted list without
    # checking every entry (a fast search; chat has thousands of seconds).
    position = bisect.bisect_right(walls, wall) - 1
    if position < 0:
        return None
    before = clock[position]
    estimate = before["stream"] + (wall - before["wall"])
    if position + 1 < len(clock):
        estimate = min(estimate, clock[position + 1]["stream"])
    return estimate


def wall_time_at(stream, clock):
    """
    Work out the clock time at which a given moment of the stream was saved.

    Args:
        stream (float): Seconds into the recorded stream.
        clock (list[dict]): {"wall", "stream"} entries sorted by "wall".

    Returns:
        float or None: The clock time, or None when the clock log is empty.
    """
    if not clock:
        return None
    for sample in clock:
        if sample["stream"] >= stream:
            return sample["wall"] - (sample["stream"] - stream)
    last = clock[-1]
    return last["wall"] + (stream - last["stream"])


def build_engagement(stream_start, duration, clock, chat, clip_spans, live):
    """
    Count audience reactions in each few-second slice of one chunk.

    Each slice gets the number of chat messages sent in reaction to it,
    plus a bonus for every viewer-made clip that covers it. The clip
    scorer uses these counts to find the moments people reacted to.

    Args:
        stream_start (float): Stream time of the chunk's first second.
        duration (float): The chunk's length in seconds.
        clock (list[dict]): {"wall", "stream"} entries from FFmpeg.
        chat (list[dict]): {"wall", "count"} chat messages per clock second.
        clip_spans (list[tuple]): (start clock time, end clock time) of viewer clips.
        live (LiveRecordingSettings): Bucket size, chat delay and clip weight.

    Returns:
        list[dict]: [{"time": seconds into the chunk, "count": reactions}, ...],
                    or an empty list when there was no chat or clip data.
    """
    size = live.chat_bin_seconds
    buckets = [0.0] * max(1, int(duration // size) + 1)
    for row in chat:
        stream = stream_time_at(row["wall"], clock)
        if stream is None:
            continue
        # Chat reacts a few seconds after the moment it is about.
        seconds = stream - stream_start - live.chat_reaction_seconds
        if 0 <= seconds < duration:
            buckets[int(seconds // size)] += row["count"]
    chat_seen = sum(buckets) > 0
    clips_seen = False
    if clip_spans and live.viewer_clip_weight > 0:
        # One viewer clip counts as `viewer_clip_weight` times a normal bucket of chat.
        bonus = max(1.0, statistics.median(buckets)) * live.viewer_clip_weight
        for wall_start, wall_end in clip_spans:
            start = stream_time_at(wall_start, clock)
            end = stream_time_at(wall_end, clock)
            if start is None or end is None:
                continue
            first = max(0, int((start - stream_start) // size))
            last = min(len(buckets) - 1, int((end - stream_start) // size))
            for index in range(first, last + 1):
                buckets[index] += bonus
                clips_seen = True
    if not chat_seen and not clips_seen:
        return []
    samples = []
    for index, count in enumerate(buckets):
        seconds = index * size
        if seconds <= duration:
            samples.append({"time": seconds, "count": round(count, 2)})
    return samples


def viewer_clip_spans(broadcaster_id, wall_start, wall_end, stream_started_at):
    """
    List the viewer-made Twitch clips that were made live during part of a stream.

    A clip made while watching live covers the seconds just before it was
    created, so it becomes the clock span (created − clip length, created).
    Twitch also returns clips made later from the past broadcast (their
    vod_offset, the place in the broadcast, is hours away from their
    creation time); those are skipped. Very new clips have no vod_offset
    yet and are treated as made live.

    Args:
        broadcaster_id (str): The streamer's numeric Twitch ID.
        wall_start (float): Start of the period (clock time).
        wall_end (float): End of the period (clock time).
        stream_started_at (float): When the broadcast began (0 if unknown).

    Returns:
        list[tuple]: (start clock time, end clock time) for each clip.
    """
    from src.sources import twitch

    found = twitch.discover_clips(broadcaster_id, rfc3339(wall_start - 60), rfc3339(wall_end + 60), max_pages=3)
    spans = []
    for item in found["items"]:
        created = parse_rfc3339(item["created_at"])
        # Twitch's date filter is loose, so check the creation time ourselves.
        if created < wall_start or created > wall_end + 30:
            continue
        offset = item.get("vod_offset")
        if offset is not None and stream_started_at:
            place_in_broadcast = stream_started_at + float(offset)
            if abs(created - place_in_broadcast) > LIVE_CLIP_TOLERANCE_SECONDS:
                continue
        spans.append((created - float(item.get("duration") or 30), created))
    return spans


# ---------------------------------------------------------------
# Sessions (one per recorded stream) in the database
# ---------------------------------------------------------------

def sessions_with_status(channel, statuses):
    """
    Read this channel's recording sessions that have one of the given statuses.

    Args:
        channel (str): The clips channel.
        statuses (list[str]): For example ["recording"] or ["recording", "ended"].

    Returns:
        list[dict]: The session rows, oldest first.
    """
    marks = ",".join("?" * len(statuses))
    connection = get_connection()
    try:
        rows = connection.execute(f"SELECT * FROM live_sessions WHERE channel=? AND status IN ({marks}) ORDER BY id",
                                  (channel, *statuses)).fetchall()
        result = []
        for row in rows:
            result.append(dict(row))
        return result
    finally:
        connection.close()


def update_session(session_id, **fields):
    """
    Change some fields of one session row.

    Args:
        session_id (int): The session.
        **fields: Column names and their new values, e.g. status="ended".

    Returns:
        Nothing.
    """
    allowed = {"pid", "status", "ended_at", "stop_reason", "broadcaster_id", "error"}
    names = []
    values = []
    for name, value in fields.items():
        if name not in allowed:
            raise ValueError(f"Unknown session field: {name}")
        names.append(f"{name}=?")
        values.append(value)
    connection = get_connection()
    try:
        connection.execute(f"UPDATE live_sessions SET {', '.join(names)} WHERE id=?", (*values, session_id))
        connection.commit()
    finally:
        connection.close()


def capture_running(session):
    """
    Check whether a session's capture process is still running.

    Args:
        session (dict): A live_sessions row.

    Returns:
        bool: True while the process is alive and really is this session's capture.
    """
    process = CAPTURE_PROCESSES.get(session["id"])
    if process is not None:
        # poll() also lets the operating system clean up a finished process.
        return process.poll() is None
    if session["pid"] <= 0:
        return False
    try:
        os.kill(session["pid"], 0)   # signal 0 only checks that the process exists
    except (ProcessLookupError, PermissionError):
        return False
    # After a restart the ID could belong to another program; check its command.
    command = subprocess.run(["ps", "-o", "command=", "-p", str(session["pid"])],
                             capture_output=True, text=True).stdout
    return "src.clips.live_capture" in command and session["folder"] in command


def mark_ended(session):
    """
    Record that a session's capture process has finished.

    Args:
        session (dict): A live_sessions row whose process has stopped.

    Returns:
        Nothing.
    """
    CAPTURE_PROCESSES.pop(session["id"], None)
    folder = Path(session["folder"])
    ended_at = time.time()
    reason = session["stop_reason"] or "capture process stopped"
    done = folder / DONE_FILE
    if done.is_file():
        try:
            details = json.loads(done.read_text())
            ended_at = details.get("ended_at", ended_at)
            reason = session["stop_reason"] or details.get("reason", reason)
        except ValueError:
            pass
    update_session(session["id"], status="ended", ended_at=ended_at, stop_reason=reason)


def stop_session(session, reason):
    """
    Ask a session's capture process to finish its current chunk and stop.

    Args:
        session (dict): A live_sessions row that is recording.
        reason (str): Why it is stopping (saved for the dashboard and logs).

    Returns:
        Nothing.
    """
    if not session["stop_reason"]:
        update_session(session["id"], stop_reason=reason)
    try:
        os.kill(session["pid"], signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        pass


def recorded_seconds_today(channel, platform, streamer, now=None):
    """
    Add up how long a streamer has been recorded since local midnight.

    Args:
        channel (str): The clips channel.
        platform (str): "twitch" or "kick".
        streamer (str): The login name (lower case).
        now (float or None): The current time (tests pass a fixed value).

    Returns:
        float: Seconds recorded today, including a recording still running.
    """
    if now is None:
        now = time.time()
    midnight = datetime.fromtimestamp(now).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    connection = get_connection()
    try:
        rows = connection.execute("""SELECT started_at, ended_at FROM live_sessions
            WHERE channel=? AND platform=? AND streamer=? AND (ended_at IS NULL OR ended_at > ?)""",
                                  (channel, platform, streamer, midnight)).fetchall()
    finally:
        connection.close()
    total = 0.0
    for row in rows:
        ended = row["ended_at"] if row["ended_at"] is not None else now
        total += max(0.0, min(now, ended) - max(row["started_at"], midnight))
    return total


def recently_failed(channel, platform, streamer, now=None):
    """
    Check whether this streamer's last recording failed straight away a short time ago.

    Args:
        channel (str): The clips channel.
        platform (str): "twitch" or "kick".
        streamer (str): The login name (lower case).
        now (float or None): The current time (tests pass a fixed value).

    Returns:
        bool: True to wait before trying again.
    """
    if now is None:
        now = time.time()
    connection = get_connection()
    try:
        row = connection.execute("""SELECT started_at, ended_at FROM live_sessions
            WHERE channel=? AND platform=? AND streamer=? AND ended_at IS NOT NULL
            ORDER BY id DESC LIMIT 1""", (channel, platform, streamer)).fetchone()
    finally:
        connection.close()
    if row is None:
        return False
    short = row["ended_at"] - row["started_at"] < SHORT_SESSION_SECONDS
    return short and now - row["ended_at"] < FAILED_START_WAIT_SECONDS


# ---------------------------------------------------------------
# Who is live
# ---------------------------------------------------------------

def save_stream_status(channel, platform, streamer, state, details):
    """
    Save one streamer's live/offline state for the dashboard.

    Args:
        channel (str): The clips channel.
        platform (str): "twitch" or "kick".
        streamer (str): The login name.
        state (str): "live", "offline" or "unknown".
        details (dict): Safe extra facts such as the viewer count.

    Returns:
        Nothing.
    """
    from src.storage.finance import utc_now
    connection = get_connection()
    try:
        connection.execute("""
            INSERT INTO stream_status VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(channel, platform, streamer) DO UPDATE SET
                state=excluded.state, details=excluded.details, checked_at=excluded.checked_at
        """, (channel, platform, streamer.lower(), state, json.dumps(details), utc_now()))
        connection.commit()
    finally:
        connection.close()


def twitch_live_streams(usernames):
    """
    Ask Twitch which of these streamers are live, in one request.

    Args:
        usernames (list[str]): Up to 100 Twitch login names.

    Returns:
        dict: login (lower case) → {"viewers", "broadcaster_id", "title", "started_at"}
              for each live streamer (started_at is a clock time).
    """
    from src.sources import twitch

    if not usernames:
        return {}
    params = [("first", "100")]
    for name in usernames:
        params.append(("user_login", name.lower()))
    data = twitch.twitch_get("/streams", params, twitch.get_app_token())
    live = {}
    for stream in data.get("data", []):
        live[stream["user_login"].lower()] = {"viewers": stream.get("viewer_count", 0),
                                              "broadcaster_id": stream.get("user_id", ""),
                                              "title": stream.get("title", ""),
                                              "started_at": parse_rfc3339(stream["started_at"])
                                              if stream.get("started_at") else 0.0}
    return live


def kick_is_live(username):
    """
    Ask Streamlink whether a Kick channel has a playable live stream.

    Kick credentials are not configured, so this uses the same public
    page Streamlink records from.

    Args:
        username (str): The Kick login name.

    Returns:
        bool: True when a stream can be played now.
    """
    result = subprocess.run(["streamlink", "--json", stream_url("kick", username)],
                            capture_output=True, text=True, timeout=60)
    try:
        return bool(json.loads(result.stdout).get("streams"))
    except ValueError:
        return False


def check_who_is_live(channel, settings):
    """
    Check every enabled approved streamer and return the ones who are live.

    Args:
        channel (str): The clips channel.
        settings (ClipChannelSettings): The checked channel settings.

    Returns:
        list[dict]: {"streamer": ApprovedStreamer, "viewers": int, "broadcaster_id": str,
                    "started_at": float} for each live streamer, most viewers first.
    """
    from src.dashboard.accounts import safe_error

    twitch_names = []
    for streamer in settings.streamers:
        if streamer.enabled and streamer.platform == "twitch":
            twitch_names.append(streamer.username)
    twitch_live = {}
    twitch_error = ""
    try:
        twitch_live = twitch_live_streams(twitch_names)
    except Exception as error:
        twitch_error = safe_error(error)
    live = []
    for streamer in settings.streamers:
        if not streamer.enabled:
            continue
        name = streamer.username.lower()
        if streamer.platform == "twitch":
            if twitch_error:
                save_stream_status(channel, "twitch", name, "unknown", {"error": twitch_error})
                continue
            details = twitch_live.get(name)
            if details is None:
                save_stream_status(channel, "twitch", name, "offline", {"live": False})
                continue
            save_stream_status(channel, "twitch", name, "live",
                               {"live": True, "viewers": details["viewers"], "title": details["title"]})
            live.append({"streamer": streamer, "viewers": details["viewers"],
                         "broadcaster_id": details["broadcaster_id"], "started_at": details["started_at"]})
        else:
            try:
                is_live = kick_is_live(name)
            except Exception as error:
                save_stream_status(channel, "kick", name, "unknown", {"error": safe_error(error)})
                continue
            save_stream_status(channel, "kick", name, "live" if is_live else "offline", {"live": is_live})
            if is_live:
                live.append({"streamer": streamer, "viewers": 0, "broadcaster_id": "", "started_at": 0.0})
    # More viewers means more chat to find moments with and a wider audience.
    live.sort(key=viewer_count, reverse=True)
    return live


def viewer_count(entry):
    """Return a live entry's viewer count, used to sort the most-watched streamers first."""
    return entry["viewers"]


# ---------------------------------------------------------------
# Starting a recording
# ---------------------------------------------------------------

def session_folder(live, platform, username, started):
    """
    Choose the folder a new recording session writes its chunks to.

    Args:
        live (LiveRecordingSettings): The recorder settings (base folder).
        platform (str): "twitch" or "kick".
        username (str): The streamer's login name.
        started (float): When the session starts (time.time()).

    Returns:
        Path: For example <project>/recordings/live/twitch/jasontheween/20261119-043000.
    """
    stamp = datetime.fromtimestamp(started).strftime("%Y%m%d-%H%M%S")
    return (ROOT / live.folder / platform / username.lower() / stamp).resolve()


def start_session(channel, live, streamer, broadcaster_id, max_seconds, stream_started_at=0.0):
    """
    Start a background capture process for one live streamer.

    Args:
        channel (str): The clips channel.
        live (LiveRecordingSettings): The recorder settings.
        streamer (ApprovedStreamer): The approved, live streamer.
        broadcaster_id (str): Their numeric Twitch ID ("" for Kick).
        max_seconds (float): Most seconds this session may record (today's allowance).
        stream_started_at (float): When the broadcast began, used to place viewer clips (0 if unknown).

    Returns:
        int or None: The new session ID, or None when one is already recording.
    """
    started = time.time()
    folder = session_folder(live, streamer.platform, streamer.username, started)
    connection = get_connection()
    try:
        cursor = connection.execute("""INSERT INTO live_sessions
            (channel, platform, streamer, folder, status, started_at, broadcaster_id, stream_started_at)
            VALUES (?, ?, ?, ?, 'recording', ?, ?, ?)""",
                                    (channel, streamer.platform, streamer.username.lower(), str(folder),
                                     started, broadcaster_id, stream_started_at))
        connection.commit()
        session_id = cursor.lastrowid
    except sqlite3.IntegrityError:
        # The database allows only one active session per streamer.
        return None
    finally:
        connection.close()
    folder.mkdir(parents=True, exist_ok=True)
    command = [sys.executable, "-m", "src.clips.live_capture",
               "--streamer", streamer.username.lower(), "--platform", streamer.platform,
               "--folder", str(folder), "--quality", live.quality,
               "--chunk-seconds", str(int(live.chunk_minutes * 60)),
               "--max-seconds", str(int(max_seconds))]
    if live.chat:
        command.append("--chat")
    try:
        with open(folder / "process.log", "a", encoding="utf-8") as log:
            # start_new_session=True keeps the recording running if the
            # supervisor itself is restarted.
            process = subprocess.Popen(command, cwd=ROOT, stdin=subprocess.DEVNULL,
                                       stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    except OSError as error:
        update_session(session_id, status="ended", ended_at=time.time(),
                       stop_reason="could not start", error=str(error)[:300])
        return None
    CAPTURE_PROCESSES[session_id] = process
    update_session(session_id, pid=process.pid)
    return session_id


def recording_blocked_reason(channel, config, settings):
    """
    Explain why new recordings may not start right now, if anything blocks them.

    Args:
        channel (str): The clips channel.
        config (dict): The channel YAML.
        settings (ClipChannelSettings): The checked settings.

    Returns:
        str: A plain reason, or "" when recording is allowed.
    """
    from src.clips.acquisition import acquisition_only

    live = settings.live_recording
    if not live.enabled:
        return "live recording is switched off (live_recording.enabled)"
    if not config.get("enabled", True):
        return "the channel is disabled"
    connection = get_connection()
    try:
        row = connection.execute("SELECT enabled FROM clip_automation WHERE channel=?", (channel,)).fetchone()
    finally:
        connection.close()
    automation_on = bool(row["enabled"]) if row is not None else settings.automation.enabled
    if not automation_on:
        return "clip automation is stopped (Start/Stop on the clips page)"
    if acquisition_only(config) and live.delete_raw_after_processing:
        return ("editing is paused (acquisition.only: true), so recorded chunks would never be "
                "turned into clips and deleted")
    return ""


def approved_now(settings, session):
    """
    Check that a session's streamer is still on the approved, enabled list.

    Args:
        settings (ClipChannelSettings): The current settings.
        session (dict): A live_sessions row.

    Returns:
        bool: True when the streamer may still be recorded.
    """
    for streamer in settings.streamers:
        same = streamer.username.lower() == session["streamer"] and streamer.platform == session["platform"]
        if same and streamer.enabled:
            return True
    return False


# ---------------------------------------------------------------
# Turning finished chunks into recordings
# ---------------------------------------------------------------

def part_index(name):
    """
    Read the chunk number from a chunk file name.

    Args:
        name (str): For example "part_00003.ts".

    Returns:
        int or None: 3 for the example, or None for other names.
    """
    match = re.fullmatch(r"part_(\d+)\.ts", name)
    if match is None:
        return None
    return int(match.group(1))


def finished_parts(folder, session_ended):
    """
    List the chunks of a session that are complete and safe to process.

    FFmpeg adds a line to segments.csv as each chunk finishes. When the
    session has ended, a last chunk missing from the list (for example
    after a crash) is still used: MPEG-TS files stay readable.

    Args:
        folder (Path): The session folder.
        session_ended (bool): True once the capture process has stopped.

    Returns:
        list[dict]: {"index", "path", "start", "end"} for each chunk, in order.
                    start/end are seconds of stream time.
    """
    parts = []
    listed = set()
    segment_list = folder / SEGMENT_LIST
    if segment_list.is_file():
        for line in segment_list.read_text().splitlines():
            pieces = line.split(",")
            if len(pieces) != 3 or part_index(pieces[0]) is None:
                continue
            index = part_index(pieces[0])
            listed.add(index)
            parts.append({"index": index, "path": folder / pieces[0],
                          "start": float(pieces[1]), "end": float(pieces[2])})
    if session_ended:
        for path in sorted(folder.glob("part_*.ts")):
            index = part_index(path.name)
            if index is None or index in listed:
                continue
            length = media_duration(path)
            if length <= 0:
                continue
            start = parts[-1]["end"] if parts else 0.0
            parts.append({"index": index, "path": path, "start": start, "end": start + length})
    parts.sort(key=part_number)
    return parts


def part_number(part):
    """Return a chunk's number, used to keep chunks in recording order."""
    return part["index"]


def chunk_rows(session_id):
    """
    Read what has already happened to each chunk of a session.

    Args:
        session_id (int): The session.

    Returns:
        dict: chunk number → its live_chunks row.
    """
    connection = get_connection()
    try:
        rows = connection.execute("SELECT * FROM live_chunks WHERE session_id=?", (session_id,)).fetchall()
    finally:
        connection.close()
    result = {}
    for row in rows:
        result[row["part_index"]] = dict(row)
    return result


def save_chunk(session_id, index, status, recording_id=None, stream_start=0.0, duration=0.0, note=""):
    """
    Record what happened to one chunk.

    Args:
        session_id (int): The session.
        index (int): The chunk number.
        status (str): "finalizing", "imported", "skipped" or "failed".
        recording_id (int or None): The recording made from it.
        stream_start (float): Stream time of the recording's first second.
        duration (float): The recording's length.
        note (str): A short explanation for skipped/failed chunks.

    Returns:
        Nothing.
    """
    connection = get_connection()
    try:
        connection.execute("""
            INSERT INTO live_chunks (session_id, part_index, status, recording_id, stream_start, duration, note)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(session_id, part_index) DO UPDATE SET status=excluded.status,
                recording_id=excluded.recording_id, stream_start=excluded.stream_start,
                duration=excluded.duration, note=excluded.note
        """, (session_id, index, status, recording_id, stream_start, duration, note[:300]))
        connection.commit()
    finally:
        connection.close()


def recording_from_path(path):
    """
    Find a recording that was already imported from this exact file path.

    Used after a crash between importing a chunk and noting that it was imported.

    Args:
        path (Path): The chunk's MP4 path in the session folder.

    Returns:
        int or None: The recording ID.
    """
    connection = get_connection()
    try:
        row = connection.execute("SELECT id FROM recordings WHERE original_path=?", (str(path),)).fetchone()
    finally:
        connection.close()
    if row is None:
        return None
    return row["id"]


def cut_tail(previous, overlap_seconds, destination):
    """
    Copy the last part of the previous chunk, to put in front of the next one.

    Args:
        previous (dict): The previous chunk ({"path", "start", "end"}).
        overlap_seconds (float): How many seconds to copy.
        destination (Path): Where the short piece is written.

    Returns:
        float: The copied piece's actual length (it begins on a whole video frame group).
    """
    length = previous["end"] - previous["start"]
    begin = max(0.0, length - overlap_seconds)
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-ss", f"{begin:.3f}",
                    "-i", str(previous["path"]), "-map", "0", "-c", "copy", "-f", "mpegts", str(destination)],
                   check=True)
    return media_duration(destination)


def join_to_mp4(pieces, destination):
    """
    Join MPEG-TS pieces into one MP4 without re-encoding.

    Args:
        pieces (list[Path]): The pieces, in playing order.
        destination (Path): The MP4 to write (written to a temporary name first).

    Returns:
        Nothing.
    """
    listing = destination.with_suffix(".txt")
    lines = []
    for piece in pieces:
        lines.append(f"file '{piece.resolve()}'")
    listing.write_text("\n".join(lines) + "\n")
    partial = destination.with_suffix(".mp4.part")
    try:
        subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "concat", "-safe", "0",
                        "-i", str(listing), "-map", "0", "-c", "copy",
                        # Put the index at the front so the file plays while it loads.
                        "-movflags", "+faststart", "-f", "mp4", str(partial)], check=True)
        os.replace(partial, destination)
    finally:
        listing.unlink(missing_ok=True)
        partial.unlink(missing_ok=True)


def finalize_part(channel, config, settings, session, part, previous, clock, chat):
    """
    Turn one finished chunk into a recording the clip pipeline can edit.

    Args:
        channel (str): The clips channel.
        config (dict): The channel YAML.
        settings (ClipChannelSettings): The checked settings.
        session (dict): The live_sessions row.
        part (dict): This chunk ({"index", "path", "start", "end"}).
        previous (dict or None): The chunk before it, if its file still exists.
        clock (list[dict]): FFmpeg's (clock time, stream time) log.
        chat (list[dict]): Chat messages per clock second.

    Returns:
        int or None: The new recording ID, or None when the chunk was too short.
    """
    from src.clips.recordings import import_recording

    live = settings.live_recording
    folder = Path(session["folder"])
    output = folder / f"chunk_{part['index']:05d}.mp4"
    save_chunk(session["id"], part["index"], "finalizing")
    already = recording_from_path(output)
    if already is not None:
        save_chunk(session["id"], part["index"], "imported", already)
        return already

    pieces = []
    tail_seconds = 0.0
    tail = folder / f"tail_{part['index']:05d}.ts"
    if previous is not None and live.overlap_seconds > 0 and previous["path"].is_file():
        tail_seconds = cut_tail(previous, live.overlap_seconds, tail)
        if tail_seconds > 0:
            pieces.append(tail)
    pieces.append(part["path"])
    try:
        join_to_mp4(pieces, output)
    finally:
        tail.unlink(missing_ok=True)

    duration = media_duration(output)
    if duration < settings.clips.moment_min_seconds:
        output.unlink(missing_ok=True)
        save_chunk(session["id"], part["index"], "skipped", duration=duration, note="too short to clip")
        return None

    stream_start = part["start"] - tail_seconds
    clip_spans = []
    if session["platform"] == "twitch" and session["broadcaster_id"] and live.viewer_clip_weight > 0:
        wall_start = wall_time_at(stream_start, clock)
        wall_end = wall_time_at(stream_start + duration, clock)
        if wall_start is not None and wall_end is not None:
            try:
                clip_spans = viewer_clip_spans(session["broadcaster_id"], wall_start, wall_end,
                                               session["stream_started_at"])
            except Exception:
                # Viewer clips only improve the score; recording carries on without them.
                clip_spans = []
    engagement = build_engagement(stream_start, duration, clock, chat, clip_spans, live)
    sidecar = output.with_suffix(".engagement.json")
    sidecar.write_text(json.dumps(engagement))
    try:
        recording_id = import_recording(channel, config, output, session["streamer"], session["platform"],
                                        True, move=True, source_kind="live")
    finally:
        sidecar.unlink(missing_ok=True)
    save_chunk(session["id"], part["index"], "imported", recording_id, stream_start, duration,
               f"{len(clip_spans)} viewer clips; chat samples {len(engagement)}")
    return recording_id


def overlaps_neighbouring_chunk(recording_id, start, end):
    """
    Check whether a moment was already clipped from the overlapping part of another chunk.

    Chunks share their boundary minutes on purpose, so the same moment can
    appear in two recordings. Times are compared in stream time.

    Args:
        recording_id (int): The live recording the new moment comes from.
        start (float): The moment's start, in seconds into that recording.
        end (float): The moment's end, in seconds into that recording.

    Returns:
        bool: True when it covers more than a quarter of an already chosen clip
              from another chunk of the same stream (the same rule the scorer
              uses inside one recording).
    """
    connection = get_connection()
    try:
        chunk = connection.execute("SELECT session_id, stream_start FROM live_chunks WHERE recording_id=?",
                                   (recording_id,)).fetchone()
        if chunk is None:
            return False
        others = connection.execute("""
            SELECT k.stream_start, c.signals_json FROM clips c
            JOIN live_chunks k ON k.recording_id = c.recording_id
            WHERE k.session_id=? AND c.recording_id != ? AND c.status IN ('picked', 'rendered')
        """, (chunk["session_id"], recording_id)).fetchall()
    finally:
        connection.close()
    new_start = chunk["stream_start"] + start
    new_end = chunk["stream_start"] + end
    for other in others:
        # signals_json keeps the original times even after the clip moves to an excerpt.
        signals = json.loads(other["signals_json"] or "{}")
        if "start" not in signals:
            continue
        other_start = other["stream_start"] + signals["start"]
        other_end = other["stream_start"] + signals["end"]
        shared = min(new_end, other_end) - max(new_start, other_start)
        shorter = min(new_end - new_start, other_end - other_start)
        if shared > 0.25 * max(1.0, shorter):
            return True
    return False


def remove_used_parts(parts, done, still_recording):
    """
    Delete chunk files that no later step needs any more.

    A chunk is kept until the chunk after it has been processed, because
    its last minutes are copied into the start of that next chunk.

    Args:
        parts (list[dict]): The session's finished chunks, in order.
        done (set[int]): Chunk numbers that have been processed.
        still_recording (bool): True while more chunks may follow.

    Returns:
        Nothing.
    """
    for position, part in enumerate(parts):
        if part["index"] not in done:
            continue
        if position + 1 < len(parts):
            next_needs_it = parts[position + 1]["index"] not in done
        else:
            next_needs_it = still_recording
        if not next_needs_it:
            part["path"].unlink(missing_ok=True)


def safe_to_delete_folder(folder, live):
    """
    Check that a folder really is a session folder inside the recorder's base folder.

    Args:
        folder (Path): The folder about to be deleted.
        live (LiveRecordingSettings): The recorder settings.

    Returns:
        bool: True only for a folder inside the live recording base folder.
    """
    base = (ROOT / live.folder).resolve()
    resolved = folder.resolve()
    return resolved != base and resolved.is_relative_to(base) and not folder.is_symlink()


def finalize_session(channel, config, settings, session):
    """
    Process every finished chunk of one session, then tidy up once it has ended.

    Args:
        channel (str): The clips channel.
        config (dict): The channel YAML.
        settings (ClipChannelSettings): The checked settings.
        session (dict): A live_sessions row ("recording" or "ended").

    Returns:
        dict: {"imported": [recording IDs], "errors": [messages]}.
    """
    from src.dashboard.accounts import safe_error

    folder = Path(session["folder"])
    ended = session["status"] == "ended"
    result = {"imported": [], "errors": []}
    if not folder.is_dir():
        if ended:
            update_session(session["id"], status="finalized")
        return result
    parts = finished_parts(folder, ended)
    rows = chunk_rows(session["id"])
    clock = read_jsonl(folder / CLOCK_FILE)
    chat = read_jsonl(folder / CHAT_FILE)
    done = set()
    for index, row in rows.items():
        if row["status"] != "finalizing":
            done.add(index)
    for position, part in enumerate(parts):
        if part["index"] in done:
            continue
        previous = None
        if position > 0:
            previous = parts[position - 1]
        # Joining writes a new copy of the chunk before the old one is
        # deleted, so wait (rather than fail and lose it) when space is short.
        needed = part["path"].stat().st_size + SPARE_DISK_BYTES if part["path"].is_file() else 0
        if shutil.disk_usage(ROOT).free < needed:
            result["errors"].append(f"{session['streamer']} chunk {part['index']}: waiting for free disk space")
            break
        try:
            recording_id = finalize_part(channel, config, settings, session, part, previous, clock, chat)
            if recording_id is not None:
                result["imported"].append(recording_id)
        except Exception as error:
            message = safe_error(error)
            save_chunk(session["id"], part["index"], "failed", note=message)
            result["errors"].append(f"{session['streamer']} chunk {part['index']}: {message}")
        done.add(part["index"])
    remove_used_parts(parts, done, not ended)
    if ended:
        if not parts:
            # Nothing was recorded; keep the last log lines to explain why.
            log = folder / "capture.log"
            if log.is_file():
                update_session(session["id"], error=log.read_text()[-300:])
        if safe_to_delete_folder(folder, settings.live_recording):
            shutil.rmtree(folder, ignore_errors=True)
        update_session(session["id"], status="finalized")
    return result


# ---------------------------------------------------------------
# The supervisor's entry point
# ---------------------------------------------------------------

def manage_live_recording(channel, config, now=None):
    """
    Start, stop and tidy up live recordings for one clips channel.

    Args:
        channel (str): The clips channel.
        config (dict): The channel YAML.
        now (float or None): The current time (tests pass a fixed value).

    Returns:
        dict: What happened: "recording" (streamers being recorded), "started",
              "stopped", "imported" (new recording IDs), "blocked" (why new
              recordings cannot start, or "") and "errors".
    """
    if now is None:
        now = time.time()
    settings = load_clip_settings(config)
    live = settings.live_recording
    summary = {"recording": [], "started": [], "stopped": [], "imported": [], "blocked": "", "errors": []}

    # 1. Notice capture processes that have finished.
    for session in sessions_with_status(channel, ["recording"]):
        if not capture_running(session):
            mark_ended(session)

    # 2. Stop recordings that must stop.
    blocked = recording_blocked_reason(channel, config, settings)
    free = free_disk_gb()
    for session in sessions_with_status(channel, ["recording"]):
        reason = blocked
        if not reason and free < live.stop_below_free_gb:
            reason = f"free disk fell below {live.stop_below_free_gb:g} GB"
        if not reason and not approved_now(settings, session):
            reason = "streamer is no longer approved or enabled"
        if reason:
            stop_session(session, reason)
            summary["stopped"].append(session["streamer"])
    # Between the two disk limits, running recordings continue but no new one starts.
    if not blocked and free < live.min_free_gb_to_start:
        blocked = f"only {free:.1f} GB free; new recordings need {live.min_free_gb_to_start:g} GB"
    summary["blocked"] = blocked

    # 3. Start recordings for live streamers, most-watched first.
    due = now - LAST_LIVE_CHECK.get(channel, 0) >= live.check_seconds
    if not blocked and due:
        LAST_LIVE_CHECK[channel] = now
        active = sessions_with_status(channel, ["recording"])
        recording_now = set()
        for session in active:
            recording_now.add((session["platform"], session["streamer"]))
        for entry in check_who_is_live(channel, settings):
            if len(active) + len(summary["started"]) >= live.max_concurrent:
                break
            streamer = entry["streamer"]
            key = (streamer.platform, streamer.username.lower())
            if key in recording_now or recently_failed(channel, *key, now=now):
                continue
            allowance = live.max_hours_per_streamer_per_day * 3600 - recorded_seconds_today(channel, *key, now=now)
            if allowance < 300:
                continue   # less than five minutes left today
            session_id = start_session(channel, live, streamer, entry["broadcaster_id"], allowance,
                                       entry["started_at"])
            if session_id is not None:
                summary["started"].append(streamer.username.lower())

    # 4. Turn finished chunks into recordings (also after recording stops).
    for session in sessions_with_status(channel, ["recording", "ended"]):
        result = finalize_session(channel, config, settings, session)
        summary["imported"].extend(result["imported"])
        summary["errors"].extend(result["errors"])
    for session in sessions_with_status(channel, ["recording"]):
        summary["recording"].append(session["streamer"])
    return summary

# =============================================================
# src/clips/live_capture.py — record one live stream in chunks
#
# The live recorder (src/clips/live_recorder.py) starts this file as
# its own background process for each streamer who is live:
#
#   python -m src.clips.live_capture --streamer jasontheween --platform twitch \
#       --folder recordings/live/twitch/jasontheween/20261119-043000
#
# What it does:
#   1. Streamlink reads the public stream and passes the video on
#      unchanged (no re-encoding, so it uses very little CPU).
#   2. FFmpeg cuts that video into chunk files (part_00000.ts, ...)
#      of a fixed length, and lists each finished chunk in segments.csv.
#      MPEG-TS chunks stay playable even if the computer crashes.
#   3. FFmpeg also reports how much stream time it has saved; that is
#      logged with the clock time to clock.jsonl, so chat messages
#      (clock time) can be matched to moments in the video.
#   4. For Twitch, chat message counts are saved to chat.jsonl.
#
# It stops when the stream ends, when the daily time limit is used up,
# or when the recorder asks it to stop (SIGTERM). The recorder then
# turns the chunks into recordings that the clip pipeline edits.
# =============================================================

import argparse
import json
import signal
import subprocess
import threading
import time
from pathlib import Path

from src.clips.live_chat import count_chat
from src.clips.settings import STREAM_PLATFORM_ADDRESSES

CLOCK_FILE = "clock.jsonl"
CHAT_FILE = "chat.jsonl"
SEGMENT_LIST = "segments.csv"
DONE_FILE = "done.json"
PROGRESS_EVERY_SECONDS = 5   # how often FFmpeg reports how much it has saved
STOP_GRACE_SECONDS = 30      # time FFmpeg gets to close the last chunk properly


def stream_url(platform, username):
    """
    Build the public web address of a streamer's channel.

    Args:
        platform (str): "twitch", "kick" or "youtube".
        username (str): The streamer's login name (the @handle on YouTube).

    Returns:
        str: For example "https://twitch.tv/jasontheween" or
             "https://www.youtube.com/@ishowspeed/live" (YouTube's live page for a channel).
    """
    if platform == "youtube":
        return f"https://www.youtube.com/@{username.lower()}/live"
    return f"https://{STREAM_PLATFORM_ADDRESSES[platform]}/{username.lower()}"


def streamlink_command(platform, username, quality):
    """
    Build the Streamlink command that writes the live stream to its output.

    Args:
        platform (str): "twitch" or "kick".
        username (str): The streamer's login name.
        quality (str): Quality names to try, best first, e.g. "1080p60,1080p,best".

    Returns:
        list[str]: The command and its options.
    """
    return ["streamlink", "--stdout", "--loglevel", "warning",
            # Retry briefly if the first connection fails.
            "--retry-open", "3",
            # Fetch two pieces at once so a slow piece does not cause a gap.
            "--stream-segment-threads", "2",
            stream_url(platform, username), quality]


def segmenter_command(folder, chunk_seconds):
    """
    Build the FFmpeg command that splits the incoming stream into chunk files.

    Args:
        folder (Path): The session folder that receives the chunks.
        chunk_seconds (int): The length of each chunk.

    Returns:
        list[str]: The command and its options.
    """
    return ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
            "-i", "pipe:0",
            # Keep the main picture and sound; Twitch also sends a data track we do not need.
            "-map", "0:v:0", "-map", "0:a:0?",
            # Copy the video as it is (no re-encoding).
            "-c", "copy",
            "-f", "segment", "-segment_time", str(chunk_seconds),
            "-segment_format", "mpegts", "-reset_timestamps", "1",
            # A line "part_00000.ts,start,end" is added as each chunk finishes.
            "-segment_list", str(folder / SEGMENT_LIST), "-segment_list_type", "csv",
            # Progress reports ("out_time_us=...") go to standard output.
            "-progress", "pipe:1", "-stats_period", str(PROGRESS_EVERY_SECONDS),
            str(folder / "part_%05d.ts")]


def parse_progress_time(line):
    """
    Read the saved-stream time from one FFmpeg progress line.

    Args:
        line (str): One progress line, e.g. "out_time_us=12500000".

    Returns:
        float or None: Seconds of stream saved so far, or None for other lines.

    Example:
        parse_progress_time("out_time_us=12500000")   # → 12.5
    """
    if not line.startswith("out_time_us="):
        return None
    value = line.split("=", 1)[1].strip()
    if not value.lstrip("-").isdigit():
        return None   # FFmpeg prints "N/A" before the first frame arrives
    return int(value) / 1_000_000


def log_clock(progress_output, folder):
    """
    Save (clock time, stream time) pairs from FFmpeg's progress reports.

    Args:
        progress_output: FFmpeg's standard output (a text stream).
        folder (Path): The session folder that holds clock.jsonl.

    Returns:
        Nothing; runs until FFmpeg closes its output.
    """
    latest = None
    for line in progress_output:
        seconds = parse_progress_time(line)
        if seconds is not None:
            latest = seconds
        # Each report block ends with a "progress=" line.
        if line.startswith("progress=") and latest is not None:
            with open(folder / CLOCK_FILE, "a", encoding="utf-8") as file:
                file.write(json.dumps({"wall": round(time.time(), 2), "stream": round(latest, 3)}) + "\n")


def stop_streamlink(streamlink):
    """
    Ask Streamlink to stop; FFmpeg then sees the end of the stream and closes the last chunk.

    Args:
        streamlink (subprocess.Popen): The running Streamlink process.

    Returns:
        Nothing.
    """
    if streamlink.poll() is None:
        streamlink.terminate()


def record(platform, username, folder, quality, chunk_seconds, max_seconds, chat):
    """
    Record one live stream into chunk files until it ends or must stop.

    Args:
        platform (str): "twitch" or "kick".
        username (str): The streamer's login name.
        folder (Path): The session folder (created if missing).
        quality (str): Streamlink quality names, best first.
        chunk_seconds (int): Length of each chunk file.
        max_seconds (int): Stop after this many seconds (the daily allowance left).
        chat (bool): Count Twitch chat messages while recording.

    Returns:
        dict: What happened, also saved to done.json.
    """
    folder.mkdir(parents=True, exist_ok=True)
    log_file = open(folder / "capture.log", "a", encoding="utf-8")

    def log(message):
        """Write one time-stamped line to the session's capture.log; return nothing."""
        log_file.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}\n")
        log_file.flush()

    stop_requested = threading.Event()

    def request_stop(signal_number, frame):
        """Remember that the recorder asked us to stop; return nothing."""
        stop_requested.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)

    started = time.time()
    # Keep the Mac awake while this recording runs (ends with this process).
    from src.clips.keep_awake import keep_awake_while_running
    keep_awake_while_running()
    log(f"recording {stream_url(platform, username)} quality={quality} chunk={chunk_seconds}s max={max_seconds}s")
    streamlink = subprocess.Popen(streamlink_command(platform, username, quality),
                                  stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=log_file)
    segmenter = subprocess.Popen(segmenter_command(folder, chunk_seconds), stdin=streamlink.stdout,
                                 stdout=subprocess.PIPE, stderr=log_file, text=True)
    # Close our copy of the pipe so FFmpeg sees the end when Streamlink exits.
    streamlink.stdout.close()

    clock_thread = threading.Thread(target=log_clock, args=(segmenter.stdout, folder), daemon=True)
    clock_thread.start()
    chat_stop = threading.Event()
    chat_thread = None
    if chat and platform == "twitch":
        chat_thread = threading.Thread(target=count_chat, args=(username, folder / CHAT_FILE, chat_stop, log),
                                       daemon=True)
        chat_thread.start()

    reason = "stream ended"
    while segmenter.poll() is None:
        if stop_requested.is_set():
            reason = "stop requested"
            stop_streamlink(streamlink)
        elif time.time() - started >= max_seconds:
            reason = "daily time limit reached"
            stop_streamlink(streamlink)
        if streamlink.poll() is not None:
            # Streamlink has finished; give FFmpeg time to close the last chunk.
            try:
                segmenter.wait(timeout=STOP_GRACE_SECONDS)
            except subprocess.TimeoutExpired:
                log("ffmpeg did not finish in time; stopping it")
                segmenter.kill()
            break
        time.sleep(1)

    stop_streamlink(streamlink)
    streamlink.wait()
    segmenter.wait()
    chat_stop.set()
    clock_thread.join(timeout=10)
    if chat_thread is not None:
        chat_thread.join(timeout=10)
    summary = {"ended_at": time.time(), "reason": reason,
               "streamlink_exit": streamlink.returncode, "ffmpeg_exit": segmenter.returncode,
               "recorded_seconds": round(time.time() - started, 1)}
    log(f"finished: {json.dumps(summary)}")
    partial = folder / (DONE_FILE + ".part")
    partial.write_text(json.dumps(summary))
    partial.replace(folder / DONE_FILE)
    log_file.close()
    return summary


def main():
    """Read the command-line options and record one stream; return nothing."""
    parser = argparse.ArgumentParser(description="Record one live stream in chunks (started by the live recorder).")
    parser.add_argument("--streamer", required=True, help="the streamer's login name")
    parser.add_argument("--platform", required=True, choices=sorted(STREAM_PLATFORM_ADDRESSES))
    parser.add_argument("--folder", required=True, help="session folder for chunks and logs")
    parser.add_argument("--quality", default="best", help="Streamlink quality names, best first")
    parser.add_argument("--chunk-seconds", type=int, default=3600, help="length of each chunk")
    parser.add_argument("--max-seconds", type=int, default=6 * 3600, help="stop after this many seconds")
    parser.add_argument("--chat", action="store_true", help="count Twitch chat messages")
    args = parser.parse_args()
    record(args.platform, args.streamer, Path(args.folder), args.quality,
           args.chunk_seconds, args.max_seconds, args.chat)


if __name__ == "__main__":
    main()

# =============================================================
# src/clips/live_chat.py — count Twitch chat messages while a stream
#                          is being recorded
#
# A sudden burst of chat is the clearest sign that something funny
# or exciting just happened on stream. While the recorder saves a
# stream, this file reads that channel's public chat and writes how
# many messages arrived in each second to chat.jsonl.
#
# It joins chat anonymously and read-only (the "justinfan" guest
# name that Twitch's chat servers accept without an account), so no
# login or token is needed. Only message COUNTS are saved — never
# the text or the usernames.
#
# Used by src/clips/live_capture.py, which runs it in a background
# thread beside the video recording.
# =============================================================

import json
import random
import socket
import ssl
import time

CHAT_HOST = "irc.chat.twitch.tv"
CHAT_PORT = 6697                 # the encrypted (TLS) chat port
FLUSH_EVERY_SECONDS = 5          # how often counts are written to disk
LONGEST_RETRY_WAIT_SECONDS = 60  # longest pause before reconnecting


def classify_chat_line(line):
    """
    Decide what one line from the Twitch chat server means.

    Args:
        line (str): One line of the chat protocol, without the line ending.

    Returns:
        str: "message" for a viewer's chat message, "ping" when the server
             checks we are still connected, or "other" for everything else.

    Example:
        classify_chat_line(":viewer!viewer@viewer.tmi.twitch.tv PRIVMSG #jason :LOL")
        # → "message"
    """
    if line.startswith("PING"):
        return "ping"
    if " PRIVMSG #" in line:
        return "message"
    return "other"


def add_message(counts, when):
    """
    Add one chat message to the count for the second it arrived in.

    Args:
        counts (dict): Whole wall-clock second → number of messages.
        when (float): The time the message arrived (time.time()).

    Returns:
        Nothing; counts is updated in place.
    """
    second = int(when)
    counts[second] = counts.get(second, 0) + 1


def write_counts(counts, output_path):
    """
    Append the waiting per-second counts to the chat file and clear them.

    Args:
        counts (dict): Whole wall-clock second → number of messages.
        output_path (Path): The session's chat.jsonl file.

    Returns:
        Nothing. Each line looks like {"wall": 1795117923, "count": 14}.
    """
    if not counts:
        return
    with open(output_path, "a", encoding="utf-8") as file:
        for second in sorted(counts):
            file.write(json.dumps({"wall": second, "count": counts[second]}) + "\n")
    counts.clear()


def read_chat_once(username, output_path, stop_event):
    """
    Connect to one channel's chat and count messages until disconnected or stopped.

    Args:
        username (str): The Twitch login whose chat to read.
        output_path (Path): Where per-second counts are appended.
        stop_event (threading.Event): Set by the recorder when recording ends.

    Returns:
        Nothing. Network errors are raised to the caller, which reconnects.
    """
    raw = socket.create_connection((CHAT_HOST, CHAT_PORT), timeout=30)
    connection = ssl.create_default_context().wrap_socket(raw, server_hostname=CHAT_HOST)
    counts = {}
    try:
        # Anonymous read-only guest login: "justinfan" plus any number.
        connection.sendall(f"NICK justinfan{random.randint(10000, 99999)}\r\n".encode())
        connection.sendall(f"JOIN #{username.lower()}\r\n".encode())
        # A short timeout lets the loop notice stop_event and flush regularly.
        connection.settimeout(1)
        waiting = b""
        last_flush = time.time()
        while not stop_event.is_set():
            try:
                data = connection.recv(65536)
            except socket.timeout:
                data = None
            if data == b"":
                raise ConnectionError("Twitch chat closed the connection.")
            if data:
                waiting += data
                lines = waiting.split(b"\r\n")
                # The last piece may be an unfinished line; keep it for next time.
                waiting = lines.pop()
                for line in lines:
                    kind = classify_chat_line(line.decode("utf-8", errors="replace"))
                    if kind == "message":
                        add_message(counts, time.time())
                    elif kind == "ping":
                        connection.sendall(b"PONG :tmi.twitch.tv\r\n")
            if time.time() - last_flush >= FLUSH_EVERY_SECONDS:
                write_counts(counts, output_path)
                last_flush = time.time()
    finally:
        write_counts(counts, output_path)
        connection.close()


def count_chat(username, output_path, stop_event, log=print):
    """
    Keep counting a channel's chat messages until recording stops, reconnecting after errors.

    Args:
        username (str): The Twitch login whose chat to read.
        output_path (Path): The session's chat.jsonl file.
        stop_event (threading.Event): Set when recording ends.
        log (callable): Where short status messages are written.

    Returns:
        Nothing.
    """
    wait = 2
    while not stop_event.is_set():
        try:
            read_chat_once(username, output_path, stop_event)
            wait = 2
        except (OSError, ConnectionError) as error:
            log(f"chat disconnected ({type(error).__name__}); retrying in {wait} s")
            stop_event.wait(wait)
            wait = min(LONGEST_RETRY_WAIT_SECONDS, wait * 2)

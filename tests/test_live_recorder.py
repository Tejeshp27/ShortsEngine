# =============================================================
# Tests for the rolling live recorder, chat counting and footage clean-up.
# No network: live checks, Twitch clips and capture processes are replaced
# with fakes. The chunking tests use real FFmpeg on generated test video.
# =============================================================

import json
import shutil
import subprocess
import time
from datetime import date, datetime
from pathlib import Path

import pytest

from src.clips import live_capture, live_chat, live_recorder, maker, retention
from src.clips.settings import load_clip_settings
from src.storage import database
from src.storage.database import get_connection

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg is not installed")


def make_config(**live):
    """
    Build a small clips-channel config with one approved Twitch streamer.

    Args:
        **live: Settings to put in the live_recording section.

    Returns:
        dict: A channel config like the YAML file would give.
    """
    section = {"enabled": True, "chunk_minutes": 2, "overlap_seconds": 5, "max_concurrent": 2,
               "min_free_gb_to_start": 2, "stop_below_free_gb": 1}
    section.update(live)
    return {"name": "Test clips", "type": "clips", "enabled": True,
            "streamers": [{"username": "teststreamer", "platform": "twitch",
                           "permission_note": "Test streamer for automated tests",
                           "permission_date": date.today().isoformat()},
                          {"username": "otherstreamer", "platform": "twitch",
                           "permission_note": "Second test streamer for automated tests",
                           "permission_date": date.today().isoformat()}],
            "clips": {"moment_min_seconds": 10, "moment_max_seconds": 22},
            "acquisition": {"only": False},
            "automation": {"enabled": True},
            "platforms": ["youtube", "tiktok"],
            "live_recording": section}


@pytest.fixture
def space(tmp_path, monkeypatch):
    """Use a temporary project folder and empty database; return the folder."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setattr(live_recorder, "ROOT", tmp_path)
    monkeypatch.setattr(retention, "ROOT", tmp_path)
    monkeypatch.setattr(live_recorder, "LAST_LIVE_CHECK", {})
    monkeypatch.setattr(live_recorder, "CAPTURE_PROCESSES", {})
    database.create_tables()
    return tmp_path


# ---------------------------------------------------------------
# Chat counting and FFmpeg progress
# ---------------------------------------------------------------

def test_chat_lines_are_classified_and_counted_per_second(tmp_path):
    """Chat messages are counted by second and written as counts only."""
    assert live_chat.classify_chat_line(":a!a@a.tmi.twitch.tv PRIVMSG #x :LOL") == "message"
    assert live_chat.classify_chat_line("PING :tmi.twitch.tv") == "ping"
    assert live_chat.classify_chat_line(":tmi.twitch.tv 001 justinfan1 :Welcome") == "other"
    counts = {}
    live_chat.add_message(counts, 100.2)
    live_chat.add_message(counts, 100.9)
    live_chat.add_message(counts, 101.1)
    output = tmp_path / "chat.jsonl"
    live_chat.write_counts(counts, output)
    assert counts == {}
    rows = [json.loads(line) for line in output.read_text().splitlines()]
    assert rows == [{"wall": 100, "count": 2}, {"wall": 101, "count": 1}]


def test_progress_lines_give_saved_stream_time():
    """Only real out_time_us values are read; N/A is ignored."""
    assert live_capture.parse_progress_time("out_time_us=12500000") == 12.5
    assert live_capture.parse_progress_time("out_time_us=N/A") is None
    assert live_capture.parse_progress_time("progress=continue") is None


def test_commands_copy_video_without_reencoding(tmp_path):
    """Streamlink writes to the pipe and FFmpeg only copies into MPEG-TS chunks."""
    streamlink = live_capture.streamlink_command("twitch", "TestStreamer", "best")
    assert streamlink[-2:] == ["https://twitch.tv/teststreamer", "best"]
    assert "--stdout" in streamlink
    segmenter = live_capture.segmenter_command(tmp_path, 3600)
    assert segmenter[segmenter.index("-c") + 1] == "copy"
    assert segmenter[segmenter.index("-segment_time") + 1] == "3600"


# ---------------------------------------------------------------
# Matching chat to the video
# ---------------------------------------------------------------

def test_stream_time_follows_clock_and_pauses_during_stalls():
    """Between log lines the stream follows the clock, but never passes the next logged time."""
    clock = [{"wall": 100, "stream": 0}, {"wall": 110, "stream": 10},
             {"wall": 140, "stream": 15}]   # 30 s of clock, only 5 s of stream: an ad break
    assert live_recorder.stream_time_at(99, clock) is None
    assert live_recorder.stream_time_at(105, clock) == 5
    assert live_recorder.stream_time_at(130, clock) == 15
    assert live_recorder.stream_time_at(150, clock) == 25
    assert live_recorder.wall_time_at(5, clock) == 105


def test_engagement_buckets_chat_with_reaction_delay_and_viewer_clips():
    """Chat lands in the bucket it reacts to, and a viewer clip adds a bonus across its span."""
    settings = load_clip_settings(make_config(chat_bin_seconds=5, chat_reaction_seconds=4, viewer_clip_weight=3))
    clock = [{"wall": 1000, "stream": 0}, {"wall": 1100, "stream": 100}]
    chat = [{"wall": 1000 + second, "count": 1} for second in range(100)]
    chat.append({"wall": 1064, "count": 50})   # burst 64 s in → reacts to ~60 s
    samples = live_recorder.build_engagement(0, 100, clock, chat, [(1030, 1040)], settings.live_recording)
    by_time = {sample["time"]: sample["count"] for sample in samples}
    assert by_time[60] == max(by_time.values())
    # The viewer clip (30–40 s) adds 3 × the typical bucket (5 messages) = 15.
    assert by_time[30] == by_time[20] + 15
    assert live_recorder.build_engagement(0, 100, clock, [], [], settings.live_recording) == []


def test_only_clips_made_live_inside_the_chunk_count(monkeypatch):
    """Clips of earlier parts of the broadcast, or created outside the chunk, are ignored."""
    from src.sources import twitch

    started = 1_000_000.0   # broadcast start (clock time)

    def clip(created, offset, duration=30):
        """Build one Get Clips item created `created` seconds after the broadcast began."""
        return {"created_at": live_recorder.rfc3339(started + created), "vod_offset": offset, "duration": duration}

    items = [clip(5000, 5000),        # made live: offset matches creation time
             clip(5100, 120),         # made later from the start of the broadcast
             clip(5200, None),        # brand new, no offset yet: treated as live
             clip(9000, 9000)]        # outside the chunk (Twitch's filter is loose)
    monkeypatch.setattr(twitch, "discover_clips", lambda *args, **kwargs: {"items": items, "truncated": False})
    spans = live_recorder.viewer_clip_spans("1", started + 4800, started + 5400, started)
    assert spans == [(started + 4970, started + 5000), (started + 5170, started + 5200)]


def test_chat_spike_wins_highlight_selection():
    """With real reaction counts, the moment chat reacted to outranks a quiet stretch."""
    config = make_config()
    config["analysis"] = {"minimum_score": 0, "candidate_seconds": 16, "max_clips_per_recording": 1}
    settings = load_clip_settings(config)
    words = []
    for second in range(0, 120, 2):
        words.append({"word": "talking", "start": second, "end": second + 1})
    media = {"volume_db": [-30] * 120, "motion": [0.02] * 120}
    engagement = [{"time": seconds, "count": 5} for seconds in range(0, 120, 5)]
    engagement[18]["count"] = 80   # 90 s
    chosen = best_highlight(120, words, media, settings, engagement)
    assert chosen["start"] <= 90 <= chosen["end"]
    assert chosen["components"]["audience"] == 1


def best_highlight(duration, words, media, settings, engagement):
    """Run the normal highlight picker and return its best candidate."""
    from src.clips.signals import select_highlights
    return select_highlights(duration, words, media, settings, engagement)[0]


# ---------------------------------------------------------------
# Settings
# ---------------------------------------------------------------

def test_disk_limits_and_overlap_are_checked():
    """Impossible disk limits or an overlap longer than a chunk are refused."""
    with pytest.raises(ValueError, match="stop_below_free_gb"):
        load_clip_settings(make_config(min_free_gb_to_start=5, stop_below_free_gb=5))
    with pytest.raises(ValueError, match="overlap_seconds"):
        load_clip_settings(make_config(chunk_minutes=2, overlap_seconds=120))


# ---------------------------------------------------------------
# Starting and stopping recordings
# ---------------------------------------------------------------

def fake_live(settings, viewers):
    """Build check_who_is_live results for the configured streamers with these viewer counts."""
    entries = []
    for streamer in settings.streamers:
        if streamer.username in viewers:
            entries.append({"streamer": streamer, "viewers": viewers[streamer.username],
                            "broadcaster_id": "1", "started_at": 0.0})
    entries.sort(key=live_recorder.viewer_count, reverse=True)
    return entries


def test_most_watched_streamers_start_up_to_the_limit(space, monkeypatch):
    """Only max_concurrent recordings start, most viewers first, and never twice for one streamer."""
    config = make_config(max_concurrent=1)
    settings = load_clip_settings(config)
    started = []
    monkeypatch.setattr(live_recorder, "free_disk_gb", lambda: 100)
    monkeypatch.setattr(live_recorder, "check_who_is_live",
                        lambda channel, s: fake_live(settings, {"teststreamer": 10, "otherstreamer": 900}))

    def fake_start(channel, live, streamer, broadcaster_id, max_seconds, stream_started_at=0.0):
        """Record the start and save an active session row instead of launching a process."""
        started.append((streamer.username, max_seconds))
        connection = get_connection()
        connection.execute("""INSERT INTO live_sessions (channel, platform, streamer, folder, status, started_at, pid)
            VALUES (?, 'twitch', ?, ?, 'recording', ?, 0)""", (channel, streamer.username, str(space / "x"), time.time()))
        connection.commit()
        connection.close()
        return 1

    monkeypatch.setattr(live_recorder, "start_session", fake_start)
    monkeypatch.setattr(live_recorder, "capture_running", lambda session: True)
    summary = live_recorder.manage_live_recording("clips", config)
    assert summary["started"] == ["otherstreamer"]
    assert started[0][1] == pytest.approx(6 * 3600, abs=5)
    live_recorder.LAST_LIVE_CHECK.clear()
    assert live_recorder.manage_live_recording("clips", config)["started"] == []


def test_recording_is_blocked_while_editing_is_paused(space, monkeypatch):
    """With acquisition.only on, chunks could never be clipped and deleted, so nothing starts."""
    config = make_config()
    config["acquisition"]["only"] = True
    monkeypatch.setattr(live_recorder, "free_disk_gb", lambda: 100)
    checked = []
    monkeypatch.setattr(live_recorder, "check_who_is_live", lambda channel, s: checked.append(1) or [])
    summary = live_recorder.manage_live_recording("clips", config)
    assert "acquisition.only" in summary["blocked"]
    assert checked == []


def test_low_disk_stops_running_recordings(space, monkeypatch):
    """Below stop_below_free_gb a running recording is asked to stop; between limits it continues."""
    config = make_config(min_free_gb_to_start=20, stop_below_free_gb=10)
    connection = get_connection()
    connection.execute("""INSERT INTO live_sessions (channel, platform, streamer, folder, status, started_at, pid)
        VALUES ('clips', 'twitch', 'teststreamer', ?, 'recording', ?, 0)""", (str(space / "s"), time.time()))
    connection.commit()
    connection.close()
    stopped = []
    monkeypatch.setattr(live_recorder, "capture_running", lambda session: True)
    monkeypatch.setattr(live_recorder, "stop_session", lambda session, reason: stopped.append(reason))
    monkeypatch.setattr(live_recorder, "check_who_is_live", lambda channel, s: [])
    monkeypatch.setattr(live_recorder, "free_disk_gb", lambda: 15)
    summary = live_recorder.manage_live_recording("clips", config)
    assert stopped == [] and "GB free" in summary["blocked"]
    monkeypatch.setattr(live_recorder, "free_disk_gb", lambda: 5)
    live_recorder.manage_live_recording("clips", config)
    assert "free disk" in stopped[0]


def test_daily_allowance_and_failed_start_wait(space):
    """Recorded time is counted from local midnight, and a quick failure pauses retries."""
    # Noon today, so the ten-minute window never crosses midnight.
    now = datetime.now().replace(hour=12, minute=0, second=0, microsecond=0).timestamp()
    connection = get_connection()
    connection.execute("""INSERT INTO live_sessions (channel, platform, streamer, folder, status, started_at, ended_at)
        VALUES ('clips', 'twitch', 'teststreamer', 'a', 'finalized', ?, ?)""", (now - 600, now - 300))
    connection.commit()
    connection.close()
    assert live_recorder.recorded_seconds_today("clips", "twitch", "teststreamer", now) == pytest.approx(300)
    assert not live_recorder.recently_failed("clips", "twitch", "teststreamer", now)
    connection = get_connection()
    connection.execute("""INSERT INTO live_sessions (channel, platform, streamer, folder, status, started_at, ended_at)
        VALUES ('clips', 'twitch', 'teststreamer', 'b', 'finalized', ?, ?)""", (now - 100, now - 90))
    connection.commit()
    connection.close()
    assert live_recorder.recently_failed("clips", "twitch", "teststreamer", now)


# ---------------------------------------------------------------
# Chunks → recordings (real FFmpeg)
# ---------------------------------------------------------------

def record_fake_stream(folder, seconds, chunk_seconds):
    """
    Run the real chunking FFmpeg command on a generated test stream.

    Args:
        folder (Path): The session folder.
        seconds (int): Length of the generated stream.
        chunk_seconds (int): Chunk length.

    Returns:
        Nothing; part_*.ts files and segments.csv are written to the folder.
    """
    folder.mkdir(parents=True, exist_ok=True)
    source = subprocess.Popen(["ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi",
                               "-i", "testsrc=size=320x180:rate=30", "-f", "lavfi", "-i", "sine=frequency=440",
                               "-t", str(seconds), "-c:v", "libx264", "-g", "30", "-c:a", "aac",
                               "-f", "mpegts", "pipe:1"], stdout=subprocess.PIPE)
    command = live_capture.segmenter_command(folder, chunk_seconds)
    progress = command.index("-progress")
    del command[progress:progress + 4]   # progress reports are not needed here
    subprocess.run(command, stdin=source.stdout, check=True)
    source.wait()


def add_session(space, folder, status):
    """Save a session row for a test folder and return it as a dictionary."""
    connection = get_connection()
    cursor = connection.execute("""INSERT INTO live_sessions (channel, platform, streamer, folder, status, started_at)
        VALUES ('clips', 'twitch', 'teststreamer', ?, ?, ?)""", (str(folder), status, time.time()))
    connection.commit()
    row = dict(connection.execute("SELECT * FROM live_sessions WHERE id=?", (cursor.lastrowid,)).fetchone())
    connection.close()
    return row


@needs_ffmpeg
def test_chunks_become_overlapping_recordings_and_files_are_cleared(space):
    """Each finished chunk is imported once with the previous chunk's tail, then the folder is removed."""
    config = make_config(overlap_seconds=5)
    settings = load_clip_settings(config)
    folder = space / "recordings/live/twitch/teststreamer/session"
    record_fake_stream(folder, 52, 25)
    # Chat burst 30 s into the stream; clock maps stream time to clock time 1:1.
    (folder / "clock.jsonl").write_text(json.dumps({"wall": 5000, "stream": 0}) + "\n")
    (folder / "chat.jsonl").write_text(json.dumps({"wall": 5034, "count": 40}) + "\n")
    session = add_session(space, folder, "recording")

    # 52 s in 25 s chunks: two full chunks and a 2 s piece that is too short to clip.
    # While recording, the newest chunk stays because the next chunk needs its tail.
    result = live_recorder.finalize_session("clips", config, settings, session)
    assert len(result["imported"]) == 2 and result["errors"] == []
    assert (folder / "part_00002.ts").exists()
    assert not (folder / "part_00000.ts").exists() and not (folder / "part_00001.ts").exists()

    session = add_session_status(session, "ended")
    result = live_recorder.finalize_session("clips", config, settings, session)
    assert not folder.exists()
    connection = get_connection()
    rows = [dict(row) for row in connection.execute("SELECT * FROM recordings ORDER BY id")]
    status = connection.execute("SELECT status FROM live_sessions WHERE id=?", (session["id"],)).fetchone()[0]
    connection.close()
    assert status == "finalized"
    assert all(row["source_kind"] == "live" for row in rows)
    # Chunk 2 starts with ~5 s of chunk 1, so it is longer than its own 25 s.
    assert rows[1]["duration"] > 27
    engagement = json.loads(rows[1]["engagement_json"])
    peak = max(engagement, key=lambda sample: sample["count"])
    # 30 s into the stream is (30 − chunk 2's stream start) seconds into chunk 2.
    chunk_rows = live_recorder.chunk_rows(session["id"])
    expected = 30 - chunk_rows[1]["stream_start"]
    assert abs(peak["time"] - expected) <= settings.live_recording.chat_bin_seconds
    # Running again never imports anything twice.
    assert live_recorder.finalize_session("clips", config, settings, session)["imported"] == []


def add_session_status(session, status):
    """Change a test session's status and return the updated row."""
    live_recorder.update_session(session["id"], status=status, ended_at=time.time())
    connection = get_connection()
    row = dict(connection.execute("SELECT * FROM live_sessions WHERE id=?", (session["id"],)).fetchone())
    connection.close()
    return row


# ---------------------------------------------------------------
# Retention
# ---------------------------------------------------------------

def test_recording_waits_for_every_clip():
    """A recording is kept while any chosen clip still has render attempts left."""
    done = {"status": "complete", "attempts": 1}
    assert retention.recording_finished(done, [{"status": "rendered", "render_attempts": 1}], 3)
    assert not retention.recording_finished(done, [{"status": "picked", "render_attempts": 1}], 3)
    assert retention.recording_finished(done, [{"status": "picked", "render_attempts": 3}], 3)
    assert not retention.recording_finished({"status": "pending", "attempts": 0}, [], 3)


def test_words_inside_are_shifted_to_the_excerpt():
    """Only words fully inside the excerpt are kept, timed from its start."""
    words = [{"word": "a", "start": 4, "end": 5}, {"word": "b", "start": 12, "end": 12.5},
             {"word": "c", "start": 19.8, "end": 20.4}]
    assert retention.words_inside(words, 10, 20) == [{"word": "b", "start": 2.0, "end": 2.5}]


@needs_ffmpeg
def test_processed_chunk_is_replaced_by_an_excerpt_that_still_re_cuts(space, monkeypatch):
    """After clipping, the chunk is deleted; the clip points at an excerpt with matching words and times."""
    from src.clips.store import get_clip, save_found_clip, update_clip

    config = make_config(keep_source_padding_seconds=5)
    settings = load_clip_settings(config)
    source = space / "cache/clips/recordings/chunk.mp4"
    source.parent.mkdir(parents=True)
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=320x180:rate=30",
                    "-f", "lavfi", "-i", "sine=frequency=440", "-t", "60", "-c:v", "libx264", "-g", "60",
                    "-c:a", "aac", "-shortest", str(source)], check=True)
    connection = get_connection()
    cursor = connection.execute("""INSERT INTO recordings (channel, platform, streamer, fingerprint, file_path,
        duration, status, created_at, source_kind) VALUES ('clips', 'twitch', 'teststreamer', 'abc', ?, 60,
        'complete', 'now', 'live')""", (str(source),))
    connection.commit()
    recording_id = cursor.lastrowid
    connection.close()
    clip_id = save_found_clip("clips", {"source": "upload", "source_clip_id": "recording:abc:30:40",
                                        "streamer": "teststreamer", "platform": "twitch",
                                        "credit_text": "twitch.tv/teststreamer", "title": "t",
                                        "duration_seconds": 60, "ownership_note": "test"})
    update_clip(clip_id, recording_id=recording_id, file_path=str(source), status="rendered",
                start_seconds=30.0, end_seconds=40.0)
    words = [{"word": "hello", "start": 31.0, "end": 31.5}, {"word": "far", "start": 2.0, "end": 2.5}]
    monkeypatch.setattr("src.clips.recordings.recording_words", lambda recording, s: words)

    assert retention.release_processed_recordings("clips", config) == [recording_id]
    assert not source.exists()
    clip = get_clip(clip_id)
    excerpt = Path(clip["file_path"])
    assert excerpt.is_file() and excerpt.parent.name == "excerpts"
    # Excerpt starts on a keyframe at or before 25 s (2-second keyframe spacing).
    offset = 30.0 - clip["start_seconds"]
    assert 23 <= offset <= 25
    assert clip["end_seconds"] - clip["start_seconds"] == pytest.approx(10)
    # The re-cut path reads the copied words without running Whisper.
    loaded = maker.words_for(clip, settings)
    assert [word["word"] for word in loaded] == ["hello"]
    assert loaded[0]["start"] == pytest.approx(31.0 - offset, abs=0.01)
    assert retention.release_processed_recordings("clips", config) == []


def test_posted_videos_lose_their_files_after_the_wait(space, monkeypatch):
    """Files go only for live-recorder videos delivered everywhere (or rejected) and older than the wait."""
    from src.clips.store import save_found_clip, update_clip
    from src.dashboard import media

    monkeypatch.setattr(media, "PROJECT_ROOT", space)
    monkeypatch.setattr(media, "OUTPUT_ROOT", space / "output")
    monkeypatch.setattr(media, "MEDIA_CACHE", space / "cache/dashboard")

    config = make_config(delete_posted_after_hours=24)
    video_folder = space / "output/videos/clips"
    video_folder.mkdir(parents=True)
    connection = get_connection()
    cursor = connection.execute("""INSERT INTO recordings (channel, platform, streamer, fingerprint, file_path,
        duration, status, created_at, source_kind) VALUES ('clips', 'twitch', 'teststreamer', 'f1', 'x', 60,
        'complete', 'now', 'live')""")
    recording_id = cursor.lastrowid
    connection.commit()
    connection.close()
    old = "2026-01-01T00:00:00+00:00"
    video_ids = {}
    for name, status, platforms in [("posted", "posted", ["youtube", "tiktok"]),
                                    ("half", "posted", ["youtube"]), ("draft", "drafted", [])]:
        path = video_folder / f"{name}.mp4"
        path.write_bytes(b"video")
        connection = get_connection()
        cursor = connection.execute("""INSERT INTO videos (channel, story_title, story_url, video_path, status,
            created_at, posted_at) VALUES ('clips', ?, 'u', ?, ?, ?, ?)""", (name, str(path), status, old, old))
        video_id = cursor.lastrowid
        for platform in platforms:
            connection.execute("INSERT INTO publishing_deliveries VALUES (?, ?, 'succeeded', 'id')", (video_id, platform))
        connection.commit()
        connection.close()
        clip_id = save_found_clip("clips", {"source": "upload", "source_clip_id": name, "streamer": "teststreamer",
                                            "platform": "twitch", "credit_text": "c", "title": name,
                                            "duration_seconds": 30, "ownership_note": "test"})
        update_clip(clip_id, recording_id=recording_id, video_id=video_id, status="rendered")
        video_ids[name] = (video_id, path)
    released = retention.release_published_videos("clips", config)
    assert released == [video_ids["posted"][0]]
    assert not video_ids["posted"][1].exists()
    assert video_ids["half"][1].exists() and video_ids["draft"][1].exists()
    assert retention.release_published_videos("clips", config) == []


def test_moment_in_the_shared_boundary_is_not_clipped_twice(space):
    """A moment already clipped from the previous chunk's end is skipped in the next chunk."""
    from src.clips.store import save_found_clip, update_clip

    connection = get_connection()
    session = connection.execute("""INSERT INTO live_sessions (channel, platform, streamer, folder, started_at)
        VALUES ('clips', 'twitch', 'teststreamer', 'f', 0)""").lastrowid
    ids = []
    for index, fingerprint in enumerate(["first", "second"]):
        recording = connection.execute("""INSERT INTO recordings (channel, platform, streamer, fingerprint,
            file_path, duration, created_at, source_kind) VALUES ('clips', 'twitch', 'teststreamer', ?, 'x', 120,
            'now', 'live')""", (fingerprint,)).lastrowid
        # The second chunk starts 30 s before the first one ends (stream time 90).
        connection.execute("INSERT INTO live_chunks (session_id, part_index, status, recording_id, stream_start) "
                           "VALUES (?, ?, 'imported', ?, ?)", (session, index, recording, index * 90.0))
        ids.append(recording)
    connection.commit()
    connection.close()
    clip_id = save_found_clip("clips", {"source": "upload", "source_clip_id": "a", "streamer": "teststreamer",
                                        "platform": "twitch", "credit_text": "c", "title": "t",
                                        "duration_seconds": 120, "ownership_note": "test"})
    update_clip(clip_id, recording_id=ids[0], status="rendered",
                signals_json=json.dumps({"start": 80.0, "end": 118.0}))   # stream 80–118
    assert live_recorder.overlaps_neighbouring_chunk(ids[1], 0.0, 40.0)        # stream 90–130
    assert not live_recorder.overlaps_neighbouring_chunk(ids[1], 40.0, 80.0)   # stream 130–170

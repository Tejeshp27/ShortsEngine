# =============================================================
# Raw source library for acquisition before editing begins.
# Reuse existing clip/recording rows and the fingerprinted media cache.
# Discovered links are never presented as acquired local video files.
# =============================================================

from pathlib import Path
from urllib.parse import urlsplit

from src.clips.settings import StreamerNotApproved, find_approved_streamer, load_clip_settings
from src.storage.database import get_connection


def acquisition_only(config):
    """Read channel settings and return whether this channel currently stops before analysis/editing/posting."""
    return bool(config.get("acquisition", {}).get("only", False))


def local_source(channel, config, kind, source_id):
    """
    Locate a permitted acquired video using database identity, never a browser-supplied path.

    Args:
        channel: The clips channel configuration name.
        config: Current channel settings including approved creators.
        kind: clip or recording, selecting the existing database table.
        source_id: The source's integer database ID.

    Returns:
        A resolved video path within cache/clips, or None if no usable local file exists.
    """
    tables = {"clip":"clips", "recording":"recordings"}
    if kind not in tables:
        return None
    connection = get_connection()
    try:
        row = connection.execute(f"SELECT * FROM {tables[kind]} WHERE id=? AND channel=?",
                                 (source_id, channel)).fetchone()
    finally:
        connection.close()
    if row is None:
        return None
    find_approved_streamer(load_clip_settings(config), row["streamer"], channel, row["platform"])
    path = Path(row["file_path"] or "").resolve()
    root = Path("cache/clips").resolve()
    if not path.is_file() or not path.is_relative_to(root):
        return None
    if path.suffix.lower() not in (".mp4", ".mov", ".mkv", ".webm"):
        return None
    return path


def source_library(channel, config, page=1, streamer=""):
    """
    Read this channel's saved discoveries/recordings and return their actual acquisition states.

    Args:
        channel: The clips channel configuration name.
        config: Current channel settings used to enforce source permissions.
        page: One-based page number, with up to 100 entries per existing source table.
        streamer: Optional configured creator login used to filter the library.

    Returns:
        Rows with local playback links only when permitted media really exists, plus stored/discovered counts.
    """
    page = max(1, int(page))
    offset = (page - 1) * 100
    creator_filter = ""
    values = [channel]
    if streamer:
        creator_filter = " AND streamer=?"
        values.append(streamer.lower())
    recording_filter = """channel=? AND NOT EXISTS
        (SELECT 1 FROM clips WHERE clips.recording_id=recordings.id AND clips.external_id IS NOT NULL)""" + creator_filter
    clip_filter = "channel=? AND (recording_id IS NULL OR external_id IS NOT NULL)" + creator_filter
    connection = get_connection()
    try:
        recordings = connection.execute(f"SELECT * FROM recordings WHERE {recording_filter} ORDER BY id DESC LIMIT 100 OFFSET ?",
                                        values + [offset]).fetchall()
        clips = connection.execute(f"SELECT * FROM clips WHERE {clip_filter} ORDER BY id DESC LIMIT 100 OFFSET ?",
                                   values + [offset]).fetchall()
        recording_count = connection.execute(f"SELECT COUNT(*) FROM recordings WHERE {recording_filter}", values).fetchone()[0]
        clip_count = connection.execute(f"SELECT COUNT(*) FROM clips WHERE {clip_filter}", values).fetchone()[0]
    finally:
        connection.close()
    rows = []
    stored = 0
    for kind, items in (("recording", recordings), ("clip", clips)):
        for item in items:
            item = dict(item)
            path = None
            try:
                path = local_source(channel, config, kind, item["id"])
            except (ValueError, StreamerNotApproved):
                pass
            if path:
                stored += 1
            url = item.get("clip_url", "")
            parts = urlsplit(url)
            if parts.scheme != "https" or parts.hostname not in ("www.twitch.tv", "twitch.tv", "clips.twitch.tv", "kick.com", "www.kick.com"):
                url = ""
            rows.append({"id":item["id"], "kind":kind, "streamer":item["streamer"],
                         "platform":item["platform"], "title":item.get("title") or "Completed recording",
                         "duration":item.get("duration", item.get("duration_seconds", 0)),
                         "external_url":url, "available":bool(path),
                         "source_type":item.get("source_type", "recording"),
                         "external_id":item.get("external_id", ""),
                         "credit_text":item.get("credit_text", ""),
                         "clipper":item.get("clipper", ""),
                         "streamer_display_name":item.get("streamer_display_name", ""),
                         "view_count":item.get("view_count"),
                         "permission_confirmed":bool(item.get("permission_confirmed", False)),
                         "playback_url":f"/clips/{channel}/sources/{kind}/{item['id']}/file" if path else "",
                         "state":"Stored locally" if path else "Source discovered; video not acquired"})
    return {"sources":rows, "stored_count":stored, "listed_count":len(rows),
            "total_count":recording_count + clip_count, "page":page, "selected_streamer":streamer.lower(),
            "has_next":max(recording_count, clip_count) > offset + 100}


def discovery_for_upload(channel, source_id, username, platform):
    """
    Validate the selected discovery before accepting an attributed upload.

    Args:
        channel: The upload's target source library.
        source_id: The existing clips-table discovery ID.
        username: The creator selected in the upload form.
        platform: The creator's platform selected in the upload form.

    Returns:
        The matching source dictionary, or raises ValueError for mismatched attribution.
    """
    connection = get_connection()
    try:
        row = connection.execute("SELECT * FROM clips WHERE id=? AND channel=? AND external_id IS NOT NULL",
                                 (source_id, channel)).fetchone()
    finally:
        connection.close()
    if row is None or row["streamer"] != username.lower() or row["platform"] != platform:
        raise ValueError("Choose a discovery from this channel matching the uploaded video's creator and platform.")
    return dict(row)


def attach_source_recording(channel, source_id, recording_id, username, platform):
    """
    Attach an imported recording to its original discovery without creating a second library entry.

    Args:
        channel: The source library's channel key.
        source_id: The existing discovered clip/VOD row ID.
        recording_id: The fingerprinted recording ID returned by the existing importer.
        username: The confirmed creator login.
        platform: The confirmed platform.

    Returns:
        Nothing; marks the same source acquired and permission-confirmed, preserving all public metadata.
    """
    connection = get_connection()
    try:
        connection.execute("BEGIN IMMEDIATE")
        source = connection.execute("SELECT * FROM clips WHERE id=? AND channel=? AND external_id IS NOT NULL",
                                    (source_id, channel)).fetchone()
        recording = connection.execute("SELECT * FROM recordings WHERE id=? AND channel=?",
                                       (recording_id, channel)).fetchone()
        if source is None or recording is None:
            raise ValueError("This source or imported recording is no longer available.")
        for row in (source, recording):
            if row["streamer"] != username.lower() or row["platform"] != platform:
                raise ValueError("The recording must match this discovery's creator and platform.")
        if source["recording_id"] is not None:
            if source["recording_id"] != recording_id:
                raise ValueError("This discovery already has a different recording attached.")
            return
        if source["file_path"]:
            raise ValueError("This source already has media attached.")
        linked = connection.execute("SELECT id FROM clips WHERE recording_id=? AND external_id IS NOT NULL",
                                    (recording_id,)).fetchone()
        if linked:
            raise ValueError("These exact bytes are already attached to another discovery.")
        connection.execute("""UPDATE clips SET recording_id=?, file_path=?, status='acquired',
            permission_confirmed=1, last_error='' WHERE id=?""", (recording_id, recording["file_path"], source_id))
        connection.commit()
    finally:
        connection.close()

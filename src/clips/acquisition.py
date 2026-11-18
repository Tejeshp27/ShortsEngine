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


def source_library(channel, config):
    """
    Read this channel's saved discoveries/recordings and return their actual acquisition states.

    Args:
        channel: The clips channel configuration name.
        config: Current channel settings used to enforce source permissions.

    Returns:
        Rows with local playback links only when permitted media really exists, plus stored/discovered counts.
    """
    connection = get_connection()
    try:
        recordings = connection.execute("SELECT * FROM recordings WHERE channel=? ORDER BY id DESC LIMIT 100", (channel,)).fetchall()
        clips = connection.execute("SELECT * FROM clips WHERE channel=? AND recording_id IS NULL ORDER BY id DESC LIMIT 100", (channel,)).fetchall()
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
                         "playback_url":f"/clips/{channel}/sources/{kind}/{item['id']}/file" if path else "",
                         "state":"Stored locally" if path else "Source discovered; video not acquired"})
    return {"sources":rows, "stored_count":stored, "listed_count":len(rows)}

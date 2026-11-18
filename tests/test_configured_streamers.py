# =============================================================
# Validate the real clips channel's source configuration offline.
# Separate recording inboxes prevent attributing one creator's files
# to another creator when automatic folder ingestion is enabled.
# =============================================================

from pathlib import Path

import yaml

from src.clips.settings import credit_for, find_approved_streamer, load_clip_settings

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_configured_streamers_have_distinct_attributed_inboxes():
    """
    Validate every configured source and ensure its recording inbox cannot overlap another source's inbox.

    Returns:
        Nothing; assertions check the real YAML without calling any platform API.
    """
    path = PROJECT_ROOT / "channels" / "twitch_clips.yaml"
    config = yaml.safe_load(path.read_text())
    settings = load_clip_settings(config)
    assert settings.streamers, "The configured clips channel should have approved sources."
    folders = []
    for streamer in settings.streamers:
        if not streamer.enabled:
            continue
        approved = find_approved_streamer(settings, streamer.username, "twitch_clips", streamer.platform)
        assert approved.permission_note.strip()
        assert streamer.username.lower() in credit_for(approved)
        assert streamer.recording_directory, "Each enabled source needs its own completed-recording inbox."
        folder = (PROJECT_ROOT / streamer.recording_directory).resolve()
        for other in folders:
            assert not folder.is_relative_to(other)
            assert not other.is_relative_to(folder)
        folders.append(folder)

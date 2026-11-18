# =============================================================
# Source acquisition tests: real raw-file import/playback and no edits.
# External platform calls are mocked; media tests use original test patterns.
# =============================================================

import shutil
import subprocess
from unittest.mock import Mock

import pytest
import yaml

from src.clips import acquisition, automation, maker, monitor, recordings, store
from src.clips.demo import demo_config
from src.dashboard import jobs
from src.generation.llm import LLMClient
from src.publishing import scheduler
from tests.test_control_center import portal, token


def configure_sources(root):
    """Write acquisition-only demo settings under a temporary test root; return that configuration."""
    config=demo_config()
    config["acquisition"]={"only":True,"max_upload_mb":1}
    (root / "channels/clips_demo.yaml").write_text(yaml.safe_dump(config))
    return config


@pytest.mark.skipif(not shutil.which("ffmpeg"),reason="FFmpeg is required for original media integration")
def test_browser_import_stores_original_and_private_playback_without_edits(portal, monkeypatch):
    """Upload original FFmpeg media through a logged-in test browser; assert byte-identical raw playback, deduplication and no jobs."""
    client,root=portal
    configure_sources(root)
    path=root / "original.mp4"
    subprocess.run(["ffmpeg","-nostdin","-v","error","-y","-f","lavfi","-i","testsrc2=s=320x180:r=10:d=2",
                    "-f","lavfi","-i","sine=frequency=440:duration=2","-c:v","libx264","-c:a","aac",
                    "-shortest",str(path)],check=True)
    data={"form_token":token(client),"username":"shortsengine_demo","platform":"kick","confirmed":"true"}
    contents=path.read_bytes()
    post=client.post("/clips/clips_demo/sources/upload",data=data,files={"file":("../original.mp4",contents,"video/mp4")},follow_redirects=False)
    assert post.status_code==303
    page=client.get(post.headers["location"])
    assert "1 stored locally" in page.text and "Acquisition phase" in page.text
    playback="/clips/clips_demo/sources/recording/1/file"
    assert client.get(playback).content==contents
    assert client.get(playback,headers={"Range":"bytes=0-15"}).status_code==206
    assert client.get("/clips/clips_demo/sources/recording/999/file").status_code==404
    assert jobs.recent_jobs()==[]
    assert client.post("/clips/clips_demo/sources/upload",data=data,files={"file":("copy.mp4",contents,"video/mp4")},follow_redirects=False).status_code==303
    assert "1 stored locally" in client.get("/clips/clips_demo/sources").text
    assert list((root / "cache/clips/incoming").iterdir())==[]
    client.cookies.clear()
    assert client.get(playback,follow_redirects=False).status_code==303


@pytest.mark.parametrize("filename,content,expected", [("empty.mp4",b"","empty"),
    ("huge.mp4",b"x"*(1024*1024+1),"upload limit"),("script.exe",b"no","MP4")])
def test_invalid_uploads_leave_no_fragments(portal, filename, content, expected):
    """Try empty, oversized and unsupported uploads in an isolated browser; assert clear errors and no staging fragments."""
    client,root=portal
    configure_sources(root)
    result=client.post("/clips/clips_demo/sources/upload",data={"form_token":token(client),"username":"shortsengine_demo",
        "platform":"kick","confirmed":"true"},files={"file":(filename,content)})
    assert result.status_code==400 and expected in result.text
    assert not list((root / "cache").rglob("*.part"))


def test_source_upload_requires_csrf_and_permission(portal):
    """Try a missing form token and missing permission confirmation; assert neither creates a raw recording."""
    client,root=portal
    configure_sources(root)
    url="/clips/clips_demo/sources/upload"
    assert client.post(url,data={"username":"shortsengine_demo","platform":"kick"},files={"file":("test.mp4",b"x")}).status_code==403
    assert client.post(url,data={"form_token":token(client),"username":"shortsengine_demo","platform":"kick"},files={"file":("test.mp4",b"x")}).status_code==400


def test_discovery_rows_are_not_presented_as_downloaded_media(portal):
    """Save public clip metadata without bytes; assert the source page exposes an official link but no local video player."""
    client,root=portal
    configure_sources(root)
    store.save_found_clip("clips_demo",{"source":"twitch","source_clip_id":"remote","streamer":"shortsengine_demo",
        "platform":"kick","credit_text":"example.com/synthetic","title":"Remote discovery",
        "clip_url":"https://www.twitch.tv/example/clip/remote"})
    page=client.get("/clips/clips_demo/sources")
    assert page.status_code==200 and "0 stored locally" in page.text
    assert "video not acquired" in page.text and "View official source" in page.text
    assert "<video" not in page.text


def test_acquisition_cycle_never_analyzes_or_renders(portal, monkeypatch):
    """Run a free acquisition-only cycle with mocked sources; assert existing import/discovery is reused and editing never starts."""
    client,root=portal
    config=configure_sources(root)
    monkeypatch.setattr(monitor,"check_live_sources",Mock(return_value=[]))
    monkeypatch.setattr(recordings,"scan_inboxes",Mock(return_value=[]))
    analyze=Mock(side_effect=AssertionError("must not analyze"))
    render=Mock(side_effect=AssertionError("must not render"))
    monkeypatch.setattr(recordings,"process_recordings",analyze)
    monkeypatch.setattr(maker,"make_clip_video",render)
    result=automation.run_pipeline("clips_demo",config,LLMClient(dry_run=True))
    assert "Acquisition-only" in result["message"] and result["video_ids"]==[]
    analyze.assert_not_called()
    render.assert_not_called()


def test_acquisition_only_channel_never_enters_publishing_schedule(portal, monkeypatch):
    """Supply a due posting slot for acquisition-only settings; assert no upload can be selected or attempted."""
    client,root=portal
    config=configure_sources(root)
    config["schedule"]={"post_times":["12:00"]}
    monkeypatch.setattr(scheduler,"_load_all_channels",Mock(return_value=[("clips_demo",config)]))
    select=Mock(side_effect=AssertionError("must not select uploads"))
    monkeypatch.setattr(scheduler,"select_slot_video",select)
    scheduler.run_scheduler()
    select.assert_not_called()

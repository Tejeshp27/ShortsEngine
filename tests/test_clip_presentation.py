# =============================================================
# Facecam preservation, optional on-video credit and contextual hook tests.
# Metadata and rendering are mocked; no provider or paid API is contacted.
# =============================================================

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from PIL import Image
import pytest
import yaml

from src.clips import editing, maker, metadata, quality, store
from src.clips.demo import demo_config
from src.clips.settings import ClipEncodingSettings, CreditSettings, FacecamBox, load_clip_settings
from src.dashboard import jobs
from src.video import clip_render
from tests.test_clips import workspace
from tests.test_control_center import portal


def test_split_layout_keeps_the_measured_facecam():
    """Build the split layout with Jason's measured camera box; assert the facecam crop fills most of it without leaving it."""
    config = demo_config()
    config["layout"] = {"mode": "split"}
    settings = load_clip_settings(config)
    facecam = FacecamBox(x=0.77734375, y=0.55972222, width=0.22265625, height=0.26527778)
    plan, video_filter, _ = clip_render.layout_for_source({"width": 1280, "height": 720, "fps": 60}, facecam, settings)
    camera = plan["panels"][1]["crop"]
    assert plan["mode"] == "split" and "vstack=inputs=2" in video_filter
    assert camera["x"] >= 995 and camera["y"] >= 402
    assert camera["x"] + camera["width"] <= 1280 and camera["y"] + camera["height"] <= 594
    assert camera["width"] >= 0.85 * 285 or camera["height"] >= 0.85 * 191


def test_split_without_facecam_falls_back_to_fit_blur():
    """Ask for split for a streamer without a camera box; assert the whole picture is kept and the reason is noted."""
    config = demo_config()
    config["layout"] = {"mode": "split"}
    plan, video_filter, _ = clip_render.layout_for_source({"width": 1280, "height": 720, "fps": 30}, None,
                                                          load_clip_settings(config))
    assert plan["mode"] == "fit-blur" and "facecam" in plan["note"]
    assert "[back][front]overlay" in video_filter


def test_disabled_credit_leaves_overlay_empty(monkeypatch):
    """Disable the video credit using checked settings; assert no pixels or icon/text calls are produced."""
    icon = Mock(side_effect=AssertionError("Credit icon must not be drawn"))
    monkeypatch.setattr(clip_render, "credit_icon", icon)
    layer = Image.new("RGBA", (1080, 1920), (0, 0, 0, 0))
    clip_render.draw_credit(layer, "twitch.tv/example", CreditSettings(enabled=False))
    assert layer.getbbox() is None
    icon.assert_not_called()


def test_hook_generation_does_not_enable_paid_scoring(workspace):
    """Generate contextual metadata with a fake model; assert the title changes while local score and post credit remain."""
    config = demo_config()
    config["models"] = {"clip_pick": "claude-haiku-4-5"}
    config["hook_title"] = {"use_llm": True}
    config["credit"] = {"enabled": False}
    clip_id = store.save_found_clip("clips_demo", {"source": "upload", "source_clip_id": "demo-hook",
        "streamer": "shortsengine_demo", "platform": "kick", "credit_text": "kick.com/shortsengine_demo"})
    store.update_clip(clip_id, start_seconds=0, end_seconds=30, hook_title="First random words",
                      highlight_score=79, signals_json=json.dumps({"components": {"hook": 0.85}}))
    llm = SimpleNamespace(ask=Mock(return_value=json.dumps({
        "hook_title": "The Chat Question That Started This Reaction",
        "description": "The streamer reacts to a question from chat.",
        "scores": {"hook": 0}})))
    words = [{"word": "What?", "start": 1, "end": 2}]
    result = metadata.create_metadata(store.get_clip(clip_id), words, config, load_clip_settings(config), llm)
    # Every hook names the streamer (here the demo streamer's username, as it has no display name).
    assert result["hook_text"] == "shortsengine_demo: The Chat Question That Started This Reaction"
    assert "The streamer is shortsengine_demo" in llm.ask.call_args.kwargs["system_prompt"]
    assert store.get_clip(clip_id)["highlight_score"] == 79
    assert "kick.com/shortsengine_demo" in result["tiktok_caption"]
    assert "Do not just copy" in llm.ask.call_args.kwargs["system_prompt"]
    assert "scores:" not in llm.ask.call_args.kwargs["system_prompt"]
    assert llm.ask.call_count == 1


def test_contextual_hooks_reserve_existing_job_budget(portal):
    """Enable only model-written hooks; assert the existing pipeline job reserves a positive spending allowance."""
    config = demo_config()
    config["models"] = {"clip_pick": "claude-haiku-4-5"}
    config["hook_title"] = {"use_llm": True}
    config["llm"]["max_calls_per_run"] = 2
    assert not config["analysis"].get("use_llm", False)
    assert jobs.paid_ai_steps(config, "clip_pipeline", {}) == [("clip_pick", 10000), ("clip_pick", 10000)]
    assert jobs.estimate_job(config, "clip_pipeline", {}) > 0


def test_streamer_facecam_and_layout_reach_the_renderer(tmp_path, monkeypatch):
    """Render with a fake renderer; assert the saved camera box and per-streamer layout reach it intact."""
    config = demo_config()
    config["streamers"][0]["facecam"] = {"x": 0.75, "y": 0.5, "width": 0.25, "height": 0.25}
    config["streamers"][0]["layout"] = "split"
    clip = {"id": 1, "streamer": "shortsengine_demo", "platform": "kick", "file_path": "original.mp4",
            "credit_text": "kick.com/shortsengine_demo"}
    moment = {"start": 0, "end": 20, "hook_title": "The reaction"}
    monkeypatch.setattr(editing, "remove_long_silence", Mock(return_value=("original.mp4", 0, 20, [], None)))

    def render(*arguments, **options):
        """Check renderer arguments preserve the facecam and layout; write a temporary output."""
        assert arguments[6] == load_clip_settings(config).streamers[0].facecam
        assert options["layout_mode"] == "split"
        Path(arguments[-1]).write_bytes(b"checked output")

    monkeypatch.setattr(clip_render, "render_clip_video", render)
    monkeypatch.setattr(quality, "check_export", Mock(return_value={"passed": True, "warnings": []}))
    monkeypatch.setattr(maker, "update_clip", Mock())
    monkeypatch.setattr(maker, "write_notes", Mock())
    maker.render_and_save(config, load_clip_settings(config), clip, moment, [],
                          str(tmp_path / "result.mp4"), str(tmp_path / "notes.md"))
    assert (tmp_path / "result.transcript.json").is_file()
    assert (tmp_path / "result.bleeps.json").is_file()


def test_configured_channel_uses_new_quality_defaults():
    """Read the actual channel YAML; assert fit-blur, source frame rate, bleeping and the corner credit tag."""
    path = Path(__file__).resolve().parents[1] / "channels/twitch_clips.yaml"
    settings = load_clip_settings(yaml.safe_load(path.read_text()))
    jason = None
    for streamer in settings.streamers:
        if streamer.username.lower() == "jasontheween":
            jason = streamer
    assert jason is not None and jason.facecam is not None
    assert settings.layout.mode == "fit-blur"
    assert settings.encoding.frame_rate == "source" and settings.encoding.crf == 16
    assert settings.layout.split_facecam_position == "top"
    assert settings.encoding.preset == "slow" and settings.encoding.profile == "high"
    assert settings.bleep.enabled and settings.bleep.precise_timing and settings.bleep.volume < 0.8
    # The on-screen credit tag is off by owner choice; the post caption always credits the streamer.
    assert not settings.credit.enabled


def test_source_frame_rate_is_kept_and_matches_the_encoder():
    """A 60 fps source gives a 60 fps decoder filter and encoder; a 29.97 fps source gives 30."""
    settings = load_clip_settings(demo_config())
    _, video_filter, frame_rate = clip_render.layout_for_source({"width": 1920, "height": 1080, "fps": 60.0},
                                                                None, settings)
    command = clip_render.encoder_command("audio.wav", 10, "result.mp4", frame_rate, settings.encoding)
    assert frame_rate == 60 and "fps=60" in video_filter
    assert command[command.index("-framerate") + 1] == "60"
    assert clip_render.output_frame_rate(29.97, settings.encoding) == 30
    assert clip_render.output_frame_rate(0, settings.encoding) == 30
    assert clip_render.output_frame_rate(120, settings.encoding) == 60


def test_encoder_uses_requested_h264_and_aac_settings():
    """Build the encoder command with defaults; assert slow/CRF 18/high/yuv420p, AAC 192k, faststart and loudnorm."""
    settings = load_clip_settings(demo_config())
    command = clip_render.encoder_command("audio.wav", 10, "result.mp4", 60, settings.encoding, "loudnorm=I=-14")
    pairs = {"-preset": "slow", "-crf": "18", "-profile:v": "high", "-pix_fmt": "yuv420p",
             "-c:v": "libx264", "-c:a": "aac", "-b:a": "192k", "-movflags": "+faststart", "-af": "loudnorm=I=-14"}
    for option, value in pairs.items():
        # The LAST use of an option is the output's (-pix_fmt rgb24 describes the input).
        last = len(command) - 1 - command[::-1].index(option)
        assert command[last + 1] == value


@pytest.mark.parametrize("values", [{"frame_rate": 0}, {"frame_rate": 120}, {"frame_rate": "fast"},
                                    {"crf": -1}, {"preset": "invalid"}, {"profile": "ultra"}])
def test_invalid_quality_settings_are_refused(values):
    """Pass invalid rate, quality, preset or profile values; assert each is rejected before FFmpeg runs."""
    with pytest.raises(ValueError):
        ClipEncodingSettings(**values)


def test_low_resolution_source_is_flagged_or_refused():
    """A 480p source gets a warning by default and is refused when the channel says so."""
    from src.clips.settings import SourceQualitySettings
    small = {"width": 854, "height": 480}
    assert "below 720p" in clip_render.check_source_resolution(small, SourceQualitySettings())
    assert clip_render.check_source_resolution({"width": 1920, "height": 1080}, SourceQualitySettings()) is None
    with pytest.raises(ValueError):
        clip_render.check_source_resolution(small, SourceQualitySettings(reject_below_min=True))


def test_credit_tag_sits_in_the_top_left_corner(monkeypatch):
    """Draw the credit tag; assert it stays in the top-left area, clear of the bottom app buttons."""
    monkeypatch.setattr(clip_render, "credit_icon", Mock(return_value=None))
    layer = Image.new("RGBA", (1080, 1920), (0, 0, 0, 0))
    clip_render.draw_credit(layer, "twitch.tv/example", CreditSettings())
    left, top, right, bottom = layer.getbbox()
    assert left >= 80 and right < 700 and top >= 180 and bottom < 330



def test_titles_always_name_the_streamer():
    """'Streamer' becomes the name; a title without the name gets it in front; a named title is kept."""
    from src.clips.moments import title_with_name
    assert title_with_name("Streamer LOSES IT at Unexpected Interrupt", "Kai Cenat") == \
        "Kai Cenat LOSES IT at Unexpected Interrupt"
    assert title_with_name("Why the streamer quit", "Lacy") == "Why Lacy quit"
    assert title_with_name("PERFECT DRIVE TURNS INTO CHAOS", "StableRonaldo") == \
        "StableRonaldo: PERFECT DRIVE TURNS INTO CHAOS"
    assert title_with_name("ONE Roblox Question Set Jason OFF", "Jason") == "ONE Roblox Question Set Jason OFF"

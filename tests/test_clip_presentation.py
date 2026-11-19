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


@pytest.mark.parametrize("template", ["gaming", "reaction", "conversation"])
def test_known_facecam_is_preserved_in_every_template(template):
    """Build each template with a measured camera box; assert a camera/game split containing the whole camera."""
    config = demo_config()
    config["layout"]["template"] = template
    settings = load_clip_settings(config)
    facecam = FacecamBox(x=0.77734375, y=0.55972222, width=0.22265625, height=0.26527778)
    plan, video_filter = clip_render.layout_for_source({"width": 1280, "height": 720}, facecam, settings)
    camera = plan["panels"][0]["crop"]
    assert plan["mode"] == "split" and "vstack=inputs=2" in video_filter
    assert camera["x"] <= 995 and camera["y"] <= 403
    assert camera["x"] + camera["width"] >= 1278
    assert camera["y"] + camera["height"] >= 592
    assert "[front]" not in video_filter


@pytest.mark.parametrize("template", ["gaming", "reaction", "conversation"])
def test_unknown_camera_keeps_full_source_visible(template):
    """Build each template without a saved camera box; assert the foreground fits the entire source instead of cropping."""
    config = demo_config()
    config["layout"]["template"] = template
    plan, video_filter = clip_render.layout_for_source({"width": 1280, "height": 720}, None, load_clip_settings(config))
    assert plan["mode"] == "full"
    assert "[fg]scale=1080:1920:force_original_aspect_ratio=decrease:flags=lanczos[front]" in video_filter
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
    assert result["hook_text"] == "The Chat Question That Started This Reaction"
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


def test_reaction_template_does_not_discard_creator_camera(tmp_path, monkeypatch):
    """Render a reaction-template request with a fake renderer; assert the saved camera box reaches the renderer intact."""
    config = demo_config()
    config["layout"]["template"] = "reaction"
    config["streamers"][0]["facecam"] = {"x": 0.75, "y": 0.5, "width": 0.25, "height": 0.25}
    clip = {"id": 1, "streamer": "shortsengine_demo", "platform": "kick", "file_path": "original.mp4",
            "credit_text": "kick.com/shortsengine_demo"}
    moment = {"start": 0, "end": 20, "hook_title": "The reaction"}
    monkeypatch.setattr(editing, "remove_long_silence", Mock(return_value=("original.mp4", 0, 20, [], None)))

    def render(*arguments):
        """Check renderer arguments preserve the facecam; write a temporary output and return nothing."""
        assert arguments[6] == load_clip_settings(config).streamers[0].facecam
        Path(arguments[-1]).write_bytes(b"checked output")

    monkeypatch.setattr(clip_render, "render_clip_video", render)
    monkeypatch.setattr(quality, "check_export", Mock(return_value={"passed": True, "warnings": []}))
    monkeypatch.setattr(maker, "update_clip", Mock())
    monkeypatch.setattr(maker, "write_notes", Mock())
    maker.render_and_save(config, load_clip_settings(config), clip, moment, [],
                          str(tmp_path / "result.mp4"), str(tmp_path / "notes.md"))


def test_configured_channel_has_measured_camera_and_no_video_url():
    """Read actual channel YAML; assert Jason's measured box, readable hook duration and optional URL-overlay setting."""
    path = Path(__file__).resolve().parents[1] / "channels/twitch_clips.yaml"
    settings = load_clip_settings(yaml.safe_load(path.read_text()))
    jason = None
    for streamer in settings.streamers:
        if streamer.username.lower() == "jasontheween":
            jason = streamer
    assert jason is not None and jason.facecam is not None
    assert not settings.credit.enabled
    assert settings.hook_title.seconds == 8 and settings.hook_title.use_llm


def test_quality_settings_keep_encoder_and_decoder_frame_rates_equal():
    """Configure a 60 fps export and assert layout decoding and encoding use that same rate with the chosen quality."""
    config = demo_config()
    config["encoding"] = {"frame_rate": 60, "crf": 17, "preset": "medium"}
    settings = load_clip_settings(config)
    _, video_filter = clip_render.layout_for_source({"width": 1920, "height": 1080}, None, settings)
    command = clip_render.encoder_command("audio.wav", 10, "result.mp4", settings.encoding)
    assert "fps=60" in video_filter
    assert command[command.index("-framerate") + 1] == "60"
    assert command[command.index("-crf") + 1] == "17"
    assert command[command.index("-preset") + 1] == "medium"


def test_lanczos_is_used_for_both_split_panels():
    """Build a configured facecam split; assert both picture panels use explicit high-quality scaling."""
    settings = load_clip_settings(demo_config())
    facecam = FacecamBox(x=0.75, y=0.5, width=0.25, height=0.25)
    _, video_filter = clip_render.layout_for_source({"width": 1920, "height": 1080}, facecam, settings)
    assert video_filter.count("flags=lanczos") == 2


def test_encoder_defaults_preserve_older_callers():
    """Build an encoder command without optional settings; assert the older 30 fps/CRF20/fast defaults remain."""
    command = clip_render.encoder_command(None, 10, "result.mp4")
    assert command[command.index("-framerate") + 1] == "30"
    assert command[command.index("-crf") + 1] == "20"
    assert command[command.index("-preset") + 1] == "fast"


@pytest.mark.parametrize("values", [{"frame_rate": 0}, {"frame_rate": 120}, {"crf": -1}, {"preset": "invalid"}])
def test_invalid_quality_settings_are_refused(values):
    """Pass invalid rate, quality or preset values into encoding validation; assert each is rejected before FFmpeg runs."""
    with pytest.raises(ValueError):
        ClipEncodingSettings(**values)

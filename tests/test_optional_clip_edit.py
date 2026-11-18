# =============================================================
# Optional editing failures must preserve a usable original-audio draft.
# Rendering and probing are replaced here; the media smoke uses real FFmpeg.
# =============================================================

from pathlib import Path
from unittest.mock import Mock

from src.clips import editing, maker, quality
from src.clips.demo import demo_config
from src.clips.settings import load_clip_settings
from src.video import clip_render


def test_failed_silence_edit_uses_original_audio_and_caption_times(tmp_path, monkeypatch):
    """Simulate an optional edit failure in a temporary folder; assert rendering still uses original media and word timing."""
    config=demo_config()
    config["editing"]={"remove_long_silence":True}
    clip={"id":1,"streamer":"shortsengine_demo","platform":"kick","file_path":"original.mp4",
          "credit_text":"example.com/synthetic"}
    words=[{"word":"Wow!","start":0,"end":1}]
    moment={"start":0,"end":20,"hook_title":"A test"}
    monkeypatch.setattr(editing,"remove_long_silence",Mock(side_effect=ValueError("optional failure")))

    def render_source(*arguments):
        """Receive the renderer's positional arguments; create a fake output and return nothing."""
        assert arguments[0] == "original.mp4"
        assert arguments[3] == words
        Path(arguments[-1]).write_bytes(b"checked export")

    monkeypatch.setattr(clip_render,"render_clip_video",render_source)
    monkeypatch.setattr(quality,"check_export",Mock(return_value={"passed":True,"warnings":[]}))
    save=Mock()
    monkeypatch.setattr(maker,"update_clip",save)
    monkeypatch.setattr(maker,"write_notes",Mock())
    maker.render_and_save(config,load_clip_settings(config),clip,moment,words,
                          str(tmp_path / "result.mp4"),str(tmp_path / "notes.md"))
    assert (tmp_path / "result.mp4").is_file()
    assert "original audio" in save.call_args.kwargs["quality_json"]

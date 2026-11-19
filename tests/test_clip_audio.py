# =============================================================
# Offline speech-priority clip audio tests. Small generated WAVs
# check voice/background balance and timing with real FFmpeg;
# mocked failures check the renderer retains usable sound.
# =============================================================

import math
from pathlib import Path
import shutil
import subprocess
import wave
from unittest.mock import Mock

import numpy as np
import pytest
import yaml

from src.clips.settings import ClipAudioSettings, load_clip_settings
from src.video import clip_audio, clip_render
from src.video.sound_design import measure_loudness


def write_wave(path, samples):
    """Write a stereo sample array to a temporary 44.1 kHz WAV path; return nothing."""
    with wave.open(str(path), "wb") as output:
        output.setnchannels(2)
        output.setsampwidth(2)
        output.setframerate(44100)
        output.writeframes((samples * 32767).astype("<i2").tobytes())


def read_wave(path):
    """Decode an audio path as a floating-point stereo sample array; return the array for measurement."""
    decoded = subprocess.run([
        "ffmpeg", "-nostdin", "-v", "error", "-i", str(path),
        "-f", "f32le", "-ac", "2", "-ar", "44100", "-",
    ], capture_output=True, check=True)
    return np.frombuffer(decoded.stdout, dtype="<f4").reshape(-1, 2)


def tone_level(samples, frequency):
    """Measure one tone's strength in a sample array at the given frequency; return its amplitude."""
    times = np.arange(len(samples)) / 44100
    return abs(np.sum(samples * np.exp(-2j * np.pi * frequency * times))) / len(samples)


def test_channel_enables_speech_priority_and_old_configs_keep_their_sound():
    """Load actual and minimal YAML settings; assert the clips channel opts in and older channels stay unchanged."""
    path = Path(__file__).resolve().parents[1] / "channels/twitch_clips.yaml"
    settings = load_clip_settings(yaml.safe_load(path.read_text()))
    assert settings.audio.speech_enhancement
    assert settings.audio.dialogue_boost == 2
    assert settings.audio.noise_reduction == 0.8
    assert not load_clip_settings({"type": "clips"}).audio.speech_enhancement


@pytest.mark.parametrize("value", [-1, 4, float("nan"), float("inf")])
def test_invalid_dialogue_strength_is_rejected(value):
    """Pass an out-of-range or nonfinite emphasis factor; assert validation refuses it before a render."""
    with pytest.raises(ValueError):
        ClipAudioSettings(dialogue_boost=value)


@pytest.mark.parametrize("value", [-0.1, 1.1, float("nan"), float("inf")])
def test_invalid_noise_reduction_is_rejected(value):
    """Pass an invalid suppression blend; assert validation refuses it before audio processing."""
    with pytest.raises(ValueError):
        ClipAudioSettings(noise_reduction=value)


def fake_extraction(command, **kwargs):
    """Create the requested temporary raw WAV for a mocked FFmpeg command; return a successful process result."""
    Path(command[-1]).write_bytes(b"original stereo")
    return subprocess.CompletedProcess(command, 0)


def fake_normalization(source, destination, settings):
    """Copy fake audio from source to destination for a normalization test; return nothing."""
    Path(destination).write_bytes(Path(source).read_bytes())


def test_disabled_enhancement_only_normalizes(tmp_path, monkeypatch):
    """Prepare sound without enhancement settings; assert the older normalization path runs and removes its temporary raw file."""
    enhance = Mock()
    monkeypatch.setattr(clip_audio, "enhance_speech", enhance)
    monkeypatch.setattr(clip_render.subprocess, "run", fake_extraction)
    monkeypatch.setattr(clip_render, "normalize_loudness", fake_normalization)
    result = clip_render.prepare_audio("source.mp4", 5, 20, str(tmp_path / "clip"))
    assert Path(result).read_bytes() == b"original stereo"
    enhance.assert_not_called()
    assert sorted(path.name for path in tmp_path.iterdir()) == ["clip_audio.wav"]


def test_failed_enhancement_warns_and_normalizes_original(tmp_path, monkeypatch, caplog):
    """Simulate an unavailable dialogue filter with a partial WAV; assert original sound survives, a warning is logged and partials are removed."""
    def fail_enhancement(source, destination, settings):
        """Write a partial output then raise a fake FFmpeg error; return no audio."""
        Path(destination).write_bytes(b"partial")
        raise subprocess.CalledProcessError(1, ["ffmpeg"])

    monkeypatch.setattr(clip_audio, "enhance_speech", fail_enhancement)
    monkeypatch.setattr(clip_render.subprocess, "run", fake_extraction)
    monkeypatch.setattr(clip_render, "normalize_loudness", fake_normalization)
    result = clip_render.prepare_audio("source.mp4", 5, 20, str(tmp_path / "clip"),
                                       ClipAudioSettings(speech_enhancement=True))
    assert Path(result).read_bytes() == b"original stereo"
    assert "Speech enhancement failed" in caplog.text
    assert sorted(path.name for path in tmp_path.iterdir()) == ["clip_audio.wav"]


def test_failed_normalization_keeps_enhanced_audio(tmp_path, monkeypatch):
    """Fail loudness measurement after successful enhancement; assert enhanced sound is retained and other temporary files are removed."""
    def enhance(source, destination, settings):
        """Write fake enhanced sound at the requested path; return nothing."""
        Path(destination).write_bytes(b"enhanced stereo")

    def fail_normalization(source, destination, settings):
        """Write an incomplete normalized file then raise a measurement error; return nothing."""
        Path(destination).write_bytes(b"partial")
        raise ValueError("Cannot measure silent audio")

    monkeypatch.setattr(clip_audio, "enhance_speech", enhance)
    monkeypatch.setattr(clip_render.subprocess, "run", fake_extraction)
    monkeypatch.setattr(clip_render, "normalize_loudness", fail_normalization)
    monkeypatch.setattr(clip_audio, "level_speech", fail_normalization)
    result = clip_render.prepare_audio("source.mp4", 5, 20, str(tmp_path / "clip"),
                                       ClipAudioSettings(speech_enhancement=True))
    assert Path(result).read_bytes() == b"enhanced stereo"
    assert sorted(path.name for path in tmp_path.iterdir()) == ["clip_audio_speech.wav"]


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg required")
def test_centered_voice_becomes_stronger_relative_to_stereo_background(tmp_path):
    """Enhance a quiet centered tone over a louder left-only tone; assert a relative improvement, full duration, stereo and safe output loudness."""
    times = np.arange(44100 * 4) / 44100
    voice = 0.06 * np.sin(2 * np.pi * 1000 * times)
    game = 0.15 * np.sin(2 * np.pi * 3000 * times)
    samples = np.column_stack((voice + game, voice))
    source = tmp_path / "mixed.wav"
    write_wave(source, samples)
    result = clip_render.prepare_audio(str(source), 0, 4, str(tmp_path / "clip"),
                                       ClipAudioSettings(speech_enhancement=True))
    output = read_wave(result)
    # Ignore the short filter startup. This compares simultaneous signals,
    # so simply turning up the whole recording cannot pass the test.
    before = samples[44100:132300, 0]
    after = output[44100:132300, 0]
    before_ratio = tone_level(before, 1000) / tone_level(before, 3000)
    after_ratio = tone_level(after, 1000) / tone_level(after, 3000)
    assert 20 * math.log10(after_ratio / before_ratio) > 4
    assert output.shape == samples.shape
    assert np.isfinite(output).all()
    loudness = measure_loudness(result)
    assert abs(loudness["lufs"] + 14) < 1
    assert loudness["true_peak"] <= -1.4


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg required")
@pytest.mark.parametrize("channels", [1, 2])
@pytest.mark.parametrize("noise_reduction", [0, 0.8])
def test_speech_processing_preserves_mono_and_stereo_timing(tmp_path, channels, noise_reduction):
    """Enhance brief mono/stereo bursts near the start and end; assert matching duration, finite stereo and no lip-sync shift or lost final sound."""
    samples = np.zeros((44137, channels))
    times = np.arange(2205) / 44100
    burst = 0.2 * np.sin(2 * np.pi * 1000 * times)
    for start in (4410, 41000):
        samples[start:start + len(burst), :] = burst[:, None]
    source = tmp_path / "bursts.wav"
    with wave.open(str(source), "wb") as output:
        output.setnchannels(channels)
        output.setsampwidth(2)
        output.setframerate(44100)
        output.writeframes((samples * 32767).astype("<i2").tobytes())
    destination = tmp_path / "enhanced.wav"
    clip_audio.enhance_speech(str(source), str(destination),
                              ClipAudioSettings(speech_enhancement=True, noise_reduction=noise_reduction))
    final = tmp_path / "levelled.wav"
    clip_audio.level_speech(str(destination), str(final), {"target_lufs": -14, "true_peak": -1.5})
    enhanced = read_wave(final)
    assert enhanced.shape == (44137, 2)
    assert np.isfinite(enhanced).all()
    for start in (4410, 41000):
        # Compare energy in 1 ms blocks; normal EQ ringing is allowed,
        # but the dialogue filter's uncompensated 35 ms delay is not.
        envelope = enhanced[start - 441:start + 2205, 0]
        audible = np.flatnonzero(np.abs(envelope) > 0.02)
        assert audible.size
        assert abs(audible[0] - 441) < 132


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg required")
def test_noise_model_loads_with_special_characters_in_project_path(tmp_path, monkeypatch):
    """Process silent audio from a model folder containing punctuation/spaces; assert valid stereo output with unchanged length."""
    model_folder = tmp_path / "creator's [test], folder"
    model_folder.mkdir()
    shutil.copyfile(clip_audio.NOISE_MODEL, model_folder / "std.rnnn")
    monkeypatch.setattr(clip_audio, "NOISE_MODEL", model_folder / "std.rnnn")
    source = tmp_path / "silent audio.wav"
    write_wave(source, np.zeros((44137, 2)))
    result = tmp_path / "output clip.wav"
    clip_audio.enhance_speech(str(source), str(result),
                              ClipAudioSettings(speech_enhancement=True, noise_reduction=0.8))
    output = read_wave(result)
    assert len(output) == 44137
    assert np.isfinite(output).all()
    assert np.max(np.abs(output)) < 1e-5


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg required")
def test_final_level_control_does_not_raise_quiet_background_separately(tmp_path):
    """Level loud/quiet spans of one generated recording; assert their relative contrast survives instead of lifting the quiet background."""
    times = np.arange(44100 * 4) / 44100
    signal = 0.001 * np.sin(2 * np.pi * 1000 * times)
    signal[:44100] *= 100
    source = tmp_path / "contrasting.wav"
    write_wave(source, np.column_stack((signal, signal)))
    destination = tmp_path / "levelled.wav"
    clip_audio.level_speech(str(source), str(destination), {"target_lufs": -14, "true_peak": -1.5})
    output = read_wave(destination)[:, 0]
    before_ratio = np.std(signal[8820:35280]) / np.std(signal[88200:123480])
    after_ratio = np.std(output[8820:35280]) / np.std(output[88200:123480])
    assert abs(20 * math.log10(after_ratio / before_ratio)) < 0.3
    assert len(output) == len(signal)
    assert measure_loudness(str(destination))["true_peak"] <= -1.4

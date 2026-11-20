# =============================================================
# Offline tests for stream-clip sound: the cleanup filter text,
# bleep maths, peak/loudness decisions, and (with real FFmpeg on
# small generated WAVs) exact timing and the -14 LUFS / -1 dBTP
# result. No network calls.
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

from src.clips.settings import BleepSettings, ClipAudioSettings, load_clip_settings
from src.video import clip_audio, clip_render
from src.video.sound_design import measure_loudness

HAS_FFMPEG = shutil.which("ffmpeg") is not None
RATE = clip_audio.SAMPLE_RATE


def write_wave(path, samples, rate=RATE):
    """Write a (samples, channels) array with values -1..1 as a 16-bit WAV file."""
    with wave.open(str(path), "wb") as output:
        output.setnchannels(samples.shape[1])
        output.setsampwidth(2)
        output.setframerate(rate)
        output.writeframes((samples * 32767).astype("<i2").tobytes())


def tone_level(samples, frequency):
    """Measure how strong one frequency is in a 1-D sample array (a single-frequency Fourier sum)."""
    times = np.arange(len(samples)) / RATE
    return abs(np.sum(samples * np.exp(-2j * np.pi * frequency * times))) / len(samples)


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

def test_channel_audio_settings_follow_platform_targets():
    """The real clips channel cleans speech and aims for -14 LUFS / -1 dBTP; defaults match too."""
    path = Path(__file__).resolve().parents[1] / "channels/twitch_clips.yaml"
    settings = load_clip_settings(yaml.safe_load(path.read_text())).audio
    assert settings.highpass_hz == 80 and settings.noise_reduction_db > 0 and settings.compressor
    assert settings.target_lufs == -14 and settings.true_peak_db == -1
    defaults = ClipAudioSettings()
    assert defaults.target_lufs == -14 and defaults.true_peak_db == -1 and not defaults.speech_enhancement


@pytest.mark.parametrize("values", [{"dialogue_boost": 4}, {"dialogue_boost": float("nan")},
                                    {"noise_reduction_db": -1}, {"highpass_hz": 500},
                                    {"true_peak_db": 1}, {"target_lufs": -5}])
def test_invalid_audio_settings_are_refused(values):
    """Out-of-range cleanup or loudness values are refused before any audio work."""
    with pytest.raises(ValueError):
        ClipAudioSettings(**values)


# ---------------------------------------------------------------------------
# Filter text and loudness decisions (no FFmpeg needed)
# ---------------------------------------------------------------------------

def test_cleanup_filter_has_highpass_denoise_and_compressor_in_order():
    """The default cleanup runs high-pass, then afftdn, then the compressor, and keeps the exact length."""
    text = clip_audio.cleanup_filter(ClipAudioSettings(), 96000)
    assert text.index("highpass=f=80") < text.index("afftdn=nr=10:nf=-50:tn=1") < text.index("acompressor=")
    assert text.endswith("apad,atrim=end_sample=96000")
    assert "dialoguenhance" not in text


def test_cleanup_filter_options_can_be_switched_off():
    """Zero/false settings remove their steps; clean=False only converts the format."""
    off = ClipAudioSettings(highpass_hz=0, noise_reduction_db=0, compressor=False)
    text = clip_audio.cleanup_filter(off, 100)
    assert "highpass" not in text and "afftdn" not in text and "acompressor" not in text
    plain = clip_audio.cleanup_filter(ClipAudioSettings(speech_enhancement=True), 100, clean=False)
    assert plain.startswith("aformat=") and "dialoguenhance" not in plain and "highpass" not in plain
    lifted = clip_audio.cleanup_filter(ClipAudioSettings(speech_enhancement=True, dialogue_boost=1.5), 100)
    assert "dialoguenhance=original=1:enhance=1.5" in lifted
    assert "atrim=start_sample=1536" in lifted   # the filter's delay is removed again


def test_peak_limiting_is_needed_only_when_peaks_lack_room():
    """+10 dB of gain on a -4 dBTP peak would clip; on a -15 dBTP peak it fits under -1 dBTP."""
    assert clip_audio.needs_peak_limiting({"input_i": -24, "input_tp": -4}, -14, -1)
    assert not clip_audio.needs_peak_limiting({"input_i": -24, "input_tp": -15}, -14, -1)
    # The 0.5 dB AAC headroom counts: a peak landing at -0.8 dBTP is too high.
    assert clip_audio.needs_peak_limiting({"input_i": -14, "input_tp": -0.8}, -14, -1)


def test_final_loudness_filter_is_linear_second_pass():
    """Pass 2 reuses every pass-1 measurement, stays linear and widens the range target when needed."""
    measured = {"input_i": -24.5, "input_tp": -12.0, "input_lra": 14.2, "input_thresh": -35.0,
                "target_offset": 0.3}
    text = clip_audio.final_loudness_filter(measured, -14, -1)
    for part in ("I=-14", "TP=-1.5", "measured_I=-24.5", "measured_TP=-12.0", "measured_LRA=14.2",
                 "measured_thresh=-35.0", "offset=0.3", "linear=true", "aresample=48000"):
        assert part in text
    assert "LRA=16" in text   # at least the measured range, so loudnorm stays linear
    calm = dict(measured, input_lra=3.0)
    assert "LRA=11" in clip_audio.final_loudness_filter(calm, -14, -1)


# ---------------------------------------------------------------------------
# Bleeps
# ---------------------------------------------------------------------------

def test_beep_volume_matches_the_replaced_voice():
    """A tone with peak RMS×√2 has the same average level as the speech; extremes are limited."""
    assert abs(clip_audio.beep_amplitude(0.1, 1.0) - 0.1 * math.sqrt(2)) < 1e-9
    assert abs(clip_audio.beep_amplitude(0.1, 0.5) - 0.05 * math.sqrt(2)) < 1e-9
    assert clip_audio.beep_amplitude(0.0, 1.0) == clip_audio.MIN_BEEP_AMPLITUDE
    assert clip_audio.beep_amplitude(5.0, 1.0) == clip_audio.MAX_BEEP_AMPLITUDE


def test_bleep_replaces_only_the_span_with_a_matching_tone():
    """Inside the span the voice is gone and a 1 kHz tone of similar level plays; outside nothing changes."""
    times = np.arange(RATE * 2) / RATE
    voice = (0.1 * np.sin(2 * np.pi * 300 * times)).astype(np.float32)
    samples = np.column_stack((voice, voice))
    result, levels = clip_audio.apply_bleeps(samples, [{"start": 0.5, "end": 1.0}], 1000, 1.0)

    assert result.shape == samples.shape
    assert np.array_equal(result[:int(0.49 * RATE)], samples[:int(0.49 * RATE)])
    assert np.array_equal(result[int(1.01 * RATE):], samples[int(1.01 * RATE):])
    middle = result[int(0.55 * RATE):int(0.95 * RATE), 0]
    assert tone_level(middle, 1000) > 20 * tone_level(middle, 300)
    original_rms = np.sqrt(np.mean(voice[int(0.5 * RATE):RATE] ** 2))
    assert abs(np.sqrt(np.mean(middle ** 2)) - original_rms) < 0.01
    assert len(levels) == 1 and -25 < levels[0] < -15
    # Smooth edges: no sudden jump at the first sample of the bleep.
    edge = int(0.5 * RATE)
    assert abs(result[edge, 0] - samples[edge, 0]) < 0.01


# ---------------------------------------------------------------------------
# Real FFmpeg: timing, cleanup fallback and final loudness
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not HAS_FFMPEG, reason="FFmpeg required")
@pytest.mark.parametrize("channels", [1, 2])
@pytest.mark.parametrize("speech_enhancement", [False, True])
def test_cleanup_keeps_exact_length_and_lip_sync(tmp_path, channels, speech_enhancement):
    """Clean short bursts near the start and end; the length is exact and the sound is not shifted in time."""
    samples = np.zeros((RATE, channels))
    burst = 0.3 * np.sin(2 * np.pi * 1000 * np.arange(2400) / RATE)
    for start in (4800, 44000):
        samples[start:start + len(burst), :] = burst[:, None]
    source = tmp_path / "bursts.wav"
    write_wave(source, samples)
    cleaned = tmp_path / "clean.wav"
    clip_audio.extract_clean_audio(str(source), 0, 1.0, str(cleaned),
                                   ClipAudioSettings(speech_enhancement=speech_enhancement))
    output = clip_audio.read_samples(str(cleaned))
    assert output.shape == (RATE, 2) and np.isfinite(output).all()
    for start in (4800, 44000):
        window = output[start - 480:start + 2400, 0]
        audible = np.flatnonzero(np.abs(window) > 0.02)
        assert audible.size
        assert abs(audible[0] - 480) < 150   # under ~3 ms: no audible lip-sync shift


@pytest.mark.skipif(not HAS_FFMPEG, reason="FFmpeg required")
def test_dialogue_emphasis_lifts_a_centred_voice(tmp_path):
    """A centred 1 kHz 'voice' gains on a louder left-only 3 kHz 'game' sound by more than 4 dB."""
    times = np.arange(RATE * 4) / RATE
    voice = 0.06 * np.sin(2 * np.pi * 1000 * times)
    game = 0.15 * np.sin(2 * np.pi * 3000 * times)
    samples = np.column_stack((voice + game, voice))
    source = tmp_path / "mixed.wav"
    write_wave(source, samples)
    cleaned = tmp_path / "clean.wav"
    only_dialogue = ClipAudioSettings(speech_enhancement=True, highpass_hz=0, noise_reduction_db=0, compressor=False)
    clip_audio.extract_clean_audio(str(source), 0, 4, str(cleaned), only_dialogue)
    output = clip_audio.read_samples(str(cleaned))
    before = samples[RATE:3 * RATE, 0]
    after = output[RATE:3 * RATE, 0]
    before_ratio = tone_level(before, 1000) / tone_level(before, 3000)
    after_ratio = tone_level(after, 1000) / tone_level(after, 3000)
    assert 20 * math.log10(after_ratio / before_ratio) > 4


@pytest.mark.skipif(not HAS_FFMPEG, reason="FFmpeg required")
@pytest.mark.parametrize("peak_gap", ["quiet and smooth", "loud and peaky"])
def test_final_sound_hits_minus_14_lufs_without_clipping(tmp_path, peak_gap):
    """Clean, bleep and encode generated sound to AAC; it measures -14 LUFS (±1) with peaks at or below -1 dBTP."""
    times = np.arange(RATE * 6) / RATE
    level = 0.02
    signal = level * np.sin(2 * np.pi * 440 * times) * (1 + 0.5 * np.sin(2 * np.pi * 2 * times))
    if peak_gap == "loud and peaky":
        # Short, very loud spikes (like a scream or an explosion) over quiet sound.
        for second in range(1, 6):
            signal[second * RATE:second * RATE + 600] = 0.95 * np.sign(np.sin(2 * np.pi * 900 * times[:600]))
    source = tmp_path / "source.wav"
    write_wave(source, np.column_stack((signal, signal)))

    prepared = clip_render.prepare_audio(str(source), 0, 6, str(tmp_path / "clip"), ClipAudioSettings(),
                                         BleepSettings(enabled=True), [{"start": 2.5, "end": 2.8}])
    assert prepared["filter"] and "linear=true" in prepared["filter"]
    assert len(prepared["beep_levels_db"]) == 1
    encoded = tmp_path / "final.m4a"
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", prepared["path"], "-af", prepared["filter"],
                    "-c:a", "aac", "-b:a", "192k", "-ar", "48000", str(encoded)], check=True)
    clip_render.remove_files(prepared["temporary"])

    loudness = measure_loudness(str(encoded))
    assert abs(loudness["lufs"] + 14) <= 1.0
    assert loudness["true_peak"] <= -1.0
    assert sorted(path.name for path in tmp_path.iterdir()) == ["final.m4a", "source.wav"]


def test_failed_cleanup_warns_and_uses_plain_sound(tmp_path, monkeypatch):
    """If this FFmpeg lacks a cleanup filter, the plain sound is used and the report says so."""
    calls = []

    def extract(clip_path, start, duration, output_path, settings, clean=True):
        """Fail the cleaned extraction; succeed (writing a file) for the plain one."""
        calls.append(clean)
        if clean:
            raise subprocess.CalledProcessError(1, ["ffmpeg"])
        Path(output_path).write_bytes(b"plain")

    monkeypatch.setattr(clip_audio, "extract_clean_audio", extract)
    monkeypatch.setattr(clip_audio, "prepare_loudness", Mock(side_effect=ValueError("silent")))
    result = clip_render.prepare_audio("source.mp4", 0, 5, str(tmp_path / "clip"), ClipAudioSettings())
    assert calls == [True, False]
    assert Path(result["path"]).read_bytes() == b"plain"
    assert result["filter"] is None
    assert any("cleanup failed" in warning for warning in result["warnings"])


def test_volume_zero_mutes_the_word_without_a_tone():
    """bleep.volume 0 silences the swear word and adds no beep sound."""
    samples = np.full((48000, 2), 0.3, dtype=np.float32)
    result, levels = clip_audio.apply_bleeps(samples, [{"start": 0.25, "end": 0.75}], 1000, 0)
    middle = result[int(0.3 * 48000):int(0.7 * 48000)]
    assert float(np.abs(middle).max()) == 0.0
    assert levels == [None]
    # Sound outside the muted word is untouched.
    assert np.array_equal(result[:int(0.2 * 48000)], samples[:int(0.2 * 48000)])

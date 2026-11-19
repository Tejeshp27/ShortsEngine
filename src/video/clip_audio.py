# =============================================================
# Speech-first sound for stream clips, using the existing FFmpeg.
# A broadcast already mixes voices and game audio. This enhances
# centered dialogue, reduces bass rumble and controls loud peaks;
# it does not recover separate microphone and gameplay tracks.
# The renderer applies measured gain and peak limiting afterward.
# =============================================================

from pathlib import Path
import math
import subprocess
import wave

from src.video.sound_design import measure_loudness, normalize_loudness


# At 44.1 kHz FFmpeg's dialoguenhance uses a 2048-sample window
# with 512-sample steps. It delays ALL output channels by 1536
# samples. Pad and remove that delay to preserve lips/caption sync
# and the final words. A real-filter regression checks this.
SAMPLE_RATE = 44100
DIALOGUE_DELAY_SAMPLES = 1536
NOISE_MODEL = Path(__file__).resolve().parents[2] / "assets/audio/rnnoise/std.rnnn"


def speech_filter(settings):
    """
    Build a stereo dialogue enhancement filter from checked channel settings.

    Args:
        settings (ClipAudioSettings): Strength of centered-dialogue emphasis (0 to 3).

    Returns:
        str: An FFmpeg audio filter, with the original timing preserved.
    """
    # FFmpeg keeps the original left/right channels and adds enhanced
    # dialogue in FC. Explicitly fold FC into both output channels;
    # otherwise a default stereo conversion can undo the voice emphasis.
    filters = [
        f"aformat=sample_fmts=fltp:sample_rates={SAMPLE_RATE}:channel_layouts=stereo",
        f"apad=pad_len={DIALOGUE_DELAY_SAMPLES}",
        f"dialoguenhance=original=1:enhance={settings.dialogue_boost}:voice=8",
        "pan=stereo|FL=0.5*FL+0.5*FC|FR=0.5*FR+0.5*FC",
        f"atrim=start_sample={DIALOGUE_DELAY_SAMPLES}",
        "asetpts=PTS-STARTPTS",
    ]
    filters += [
        "highpass=f=80",
        "equalizer=f=250:t=q:w=0.7:g=-2",
        "equalizer=f=2500:t=q:w=0.8:g=2",
        # Gentle compression tames sudden loud effects and shouts. It
        # cannot turn down a game effect independently of mixed speech.
        "acompressor=threshold=0.25:ratio=2.5:attack=8:release=150:link=maximum",
    ]
    return ",".join(filters)


def enhance_speech(input_path, output_path, settings):
    """
    Emphasize voices in a mixed recording without changing its length or pitch.

    Args:
        input_path (str): The renderer's extracted stereo WAV.
        output_path (str): Temporary WAV to write; the original stays unchanged.
        settings (ClipAudioSettings): Checked dialogue and noise reduction controls.

    Returns:
        None. Writes a floating-point WAV, leaving headroom for normalization.

    Raises:
        subprocess.CalledProcessError: If this FFmpeg cannot run the filter.
    """
    with wave.open(str(input_path), "rb") as source:
        samples = round(source.getnframes() * SAMPLE_RATE / source.getframerate())
    # RNNoise processes whole 10 ms blocks, so remove any block padding.
    # Keep the selected duration exact, including a final partial block.
    audio_filter = speech_filter(settings) + f",apad,atrim=end_sample={samples}"
    dialogue_path = output_path
    if settings.noise_reduction > 0:
        dialogue_path = str(output_path) + ".dialogue.wav"
    model_input_path = str(output_path) + ".model_input.wav"
    try:
        subprocess.run([
            "ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(input_path),
            "-af", audio_filter, "-c:a", "pcm_f32le", str(dialogue_path),
        ], capture_output=True, text=True, check=True)
        if settings.noise_reduction > 0:
            # Give the speech model consistent input levels. Denoising
            # comes AFTER compression so compression cannot raise the
            # background noise we just removed. The renderer normalizes
            # the cleaned result once more for the final phone mix.
            noise_input = dialogue_path
            try:
                normalize_loudness(dialogue_path, model_input_path,
                                   {"target_lufs": -14, "true_peak": -1.5})
                noise_input = model_input_path
            except (subprocess.CalledProcessError, ValueError, KeyError):
                # Silence cannot always be measured; RNNoise can still
                # process it without the input leveling step.
                pass
            reduce_noise(noise_input, output_path, settings, samples)
    finally:
        for temporary in (dialogue_path, model_input_path):
            if temporary != output_path and Path(temporary).exists():
                Path(temporary).unlink()


def reduce_noise(input_path, output_path, settings, samples):
    """
    Suppress non-speech sound with the bundled small CPU RNNoise model.

    Args:
        input_path (str): Levelled dialogue WAV for consistent model input.
        output_path (str): Where to write the cleaned floating-point WAV.
        settings (ClipAudioSettings): Blend of cleaned/original sound, from 0 to 1.
        samples (int): Exact output length in 44.1 kHz samples.

    Returns:
        None. Writes the cleaned sound, retaining the selected timing.
    """
    # RNNoise works on mono too and delays both the dry/clean blend by
    # 10 ms. Flush and remove that delay, including a final partial block.
    audio_filter = (
        "aresample=48000,apad=pad_len=480,"
        f"arnndn=m=std.rnnn:mix={settings.noise_reduction},"
        "atrim=start_sample=480,asetpts=PTS-STARTPTS,"
        f"aresample={SAMPLE_RATE},apad,atrim=end_sample={samples}"
    )
    # Only this process works in the model folder. A simple model filename
    # avoids FFmpeg escaping for project paths containing quotes or commas.
    subprocess.run([
        "ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(Path(input_path).resolve()),
        "-af", audio_filter, "-c:a", "pcm_f32le", str(Path(output_path).resolve()),
    ], cwd=str(NOISE_MODEL.parent), capture_output=True, text=True, check=True)


def level_speech(input_path, output_path, settings):
    """
    Raise cleaned speech toward phone loudness without raising quiet gaps separately.

    Args:
        input_path (str): The enhanced WAV.
        output_path (str): Where to save the finished WAV.
        settings (dict): Target loudness in LUFS and maximum true peak in dB.

    Returns:
        None. Writes a fixed-gain mix with peak limiting; actual loudness can
        be slightly below the target when many peaks need limiting.

    Raises:
        ValueError: If silence or invalid audio prevents a loudness measurement.
    """
    measured = measure_loudness(input_path)
    if measured["lufs"] is None or not math.isfinite(measured["lufs"]):
        raise ValueError("Cannot measure speech loudness.")
    gain_db = settings["target_lufs"] - measured["lufs"]
    limit = 10 ** (settings["true_peak"] / 20)
    # A dynamic loudness pass can lift the noise floor again after RNNoise.
    # One fixed gain preserves voice/background contrast. Oversampling
    # helps the limiter catch peaks between samples; latency=true retains sync.
    audio_filter = (
        f"aresample=192000,volume={gain_db:.3f}dB,"
        f"alimiter=limit={limit:.8f}:level=false:latency=true,aresample={SAMPLE_RATE}"
    )
    subprocess.run([
        "ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(input_path),
        "-af", audio_filter, "-ar", str(SAMPLE_RATE), str(output_path),
    ], capture_output=True, text=True, check=True)

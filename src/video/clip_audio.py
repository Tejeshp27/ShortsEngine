# =============================================================
# src/video/clip_audio.py — clean, bleep and level a stream clip's
#                           sound for phones (TikTok / YouTube Shorts)
#
# The steps, in order (clip_render.prepare_audio runs them):
#   1. Cut the moment's sound and clean it in ONE FFmpeg run:
#        high-pass (removes rumble below ~80 Hz)
#        optional dialogue emphasis (lifts centred voices)
#        light noise reduction (afftdn)
#        gentle compressor (evens out sudden shouts)
#      The result is saved as a lossless floating-point WAV, so no
#      quality is lost and nothing can clip between steps.
#   2. Bleep: mute each swear word and play a 1 kHz tone instead,
#      at about the same loudness as the voice it replaces.
#   3. Loudness, two-pass "loudnorm" (EBU R128, what platforms use):
#        pass 1 measures the sound;
#        if its peaks would go above -1 dBTP once it is made loud
#        enough, only those peaks are limited first;
#        pass 2 happens inside the final video encode (clip_render.py),
#        so the sound is compressed to AAC exactly once.
#
# A broadcast already mixes voices and game audio together, so this
# can lift and clean speech but cannot separate the two completely.
# =============================================================

import json
import math
import subprocess

import numpy as np

# 48 kHz is the standard sample rate for video sound.
SAMPLE_RATE = 48000
CHANNELS = 2

# FFmpeg's dialoguenhance works in 2048-sample windows with 512-sample
# steps (at rates up to 50 kHz), which delays ALL channels by 1536
# samples. We add and remove that delay to keep lips and captions in sync.
DIALOGUE_DELAY_SAMPLES = 1536

# FFmpeg's afftdn (noise reduction) delays the sound by 25 ms (1200
# samples at 48 kHz, measured; the same for every strength) and drops
# the last 25 ms. It is padded and trimmed like dialoguenhance.
DENOISE_DELAY_SAMPLES = 1200

# AAC encoding can push peaks up by a few tenths of a dB, so peaks are
# kept this much below the target before encoding. Result: the final
# file stays at or under the -1 dBTP target.
AAC_HEADROOM_DB = 0.5

# Fades at each edge of a bleep, in seconds. Switching sound on/off
# instantly makes a "click"; 8 ms is too short to hear as a fade.
BLEEP_FADE_SECONDS = 0.008
# Limiting the loudest peaks also lowers the measured loudness a little,
# so the gain needed afterwards grows. Each extra pass re-measures and
# limits again; a few passes are enough in practice.
MAX_LIMIT_PASSES = 4

# Beep loudness limits (as sample amplitudes; 1.0 is the loudest possible).
MIN_BEEP_AMPLITUDE = 0.02
MAX_BEEP_AMPLITUDE = 0.6


# ---------------------------------------------------------------------------
# Step 1: cut and clean
# ---------------------------------------------------------------------------

def dialogue_steps(dialogue_boost):
    """
    List the FFmpeg steps that lift centred voices above stereo game sound.

    Args:
        dialogue_boost (float): Strength from 0 to 3.

    Returns:
        list[str]: Filter steps, with the filter's delay removed again.
    """
    # dialoguenhance keeps left/right and adds the enhanced voice as a
    # centre channel (FC); "pan" folds the centre back into left and right.
    return [
        f"apad=pad_len={DIALOGUE_DELAY_SAMPLES}",
        f"dialoguenhance=original=1:enhance={dialogue_boost}:voice=8",
        "pan=stereo|FL=0.5*FL+0.5*FC|FR=0.5*FR+0.5*FC",
        f"atrim=start_sample={DIALOGUE_DELAY_SAMPLES}",
        "asetpts=PTS-STARTPTS",
    ]


def cleanup_filter(settings, total_samples, clean=True):
    """
    Build the FFmpeg audio filter for step 1 (cleaning).

    Args:
        settings (ClipAudioSettings): The channel's checked audio settings.
        total_samples (int): Exact length of the moment in samples, so no
                             step can make the sound longer or shorter.
        clean (bool): False only converts the format (used as a fallback
                      if this FFmpeg cannot run one of the cleaning steps).

    Returns:
        str: For example
             "aformat=...,highpass=f=80,afftdn=nr=10:nf=-50:tn=1,acompressor=...,apad,atrim=end_sample=1536000".
    """
    steps = [f"aformat=sample_fmts=fltp:sample_rates={SAMPLE_RATE}:channel_layouts=stereo"]
    if clean:
        if settings.highpass_hz > 0:
            steps.append(f"highpass=f={settings.highpass_hz}")
        if settings.speech_enhancement:
            steps += dialogue_steps(settings.dialogue_boost)
        if settings.noise_reduction_db > 0:
            # nr = how many dB of noise to remove; nf = the noise floor;
            # tn=1 lets the filter keep learning the noise as it changes.
            steps.append(f"apad=pad_len={DENOISE_DELAY_SAMPLES}")
            steps.append(f"afftdn=nr={settings.noise_reduction_db:g}:nf=-50:tn=1")
            steps.append(f"atrim=start_sample={DENOISE_DELAY_SAMPLES},asetpts=PTS-STARTPTS")
        if settings.compressor:
            # threshold=0.125 is -18 dB; ratio 3:1 above that. Attack/release
            # in milliseconds: quick enough for shouts, slow enough to sound natural.
            steps.append("acompressor=threshold=0.125:ratio=3:attack=10:release=200:knee=4")
    # Pad with silence, then cut at the exact length: the moment keeps
    # its exact duration even if a step trimmed or delayed the sound.
    steps.append(f"apad,atrim=end_sample={total_samples}")
    return ",".join(steps)


def extract_clean_audio(clip_path, start, duration, output_path, settings, clean=True):
    """
    Cut a moment's sound from a video and clean it (step 1), in one FFmpeg run.

    Args:
        clip_path (str): The source video.
        start (float): The moment's start in seconds.
        duration (float): The moment's length in seconds.
        output_path (str): Where to write the floating-point WAV.
        settings (ClipAudioSettings): The audio settings.
        clean (bool): False skips the cleaning steps (fallback).

    Raises:
        subprocess.CalledProcessError: If FFmpeg fails.
    """
    total_samples = round(duration * SAMPLE_RATE)
    subprocess.run([
        "ffmpeg", "-nostdin", "-v", "error", "-y",
        "-ss", f"{start:.3f}", "-t", f"{duration:.3f}", "-i", str(clip_path),
        "-vn", "-af", cleanup_filter(settings, total_samples, clean),
        "-ac", str(CHANNELS), "-ar", str(SAMPLE_RATE),
        "-c:a", "pcm_f32le",          # 32-bit float: no clipping between steps
        str(output_path),
    ], capture_output=True, text=True, check=True)


# ---------------------------------------------------------------------------
# Step 2: bleeps
# ---------------------------------------------------------------------------

def read_samples(path):
    """
    Read a sound file as numbers (one row per sample, one column per channel).

    Args:
        path (str): Any audio file FFmpeg can read.

    Returns:
        numpy.ndarray: Shape (samples, 2), values between -1 and 1.
    """
    decoded = subprocess.run([
        "ffmpeg", "-nostdin", "-v", "error", "-i", str(path),
        "-f", "f32le", "-ac", str(CHANNELS), "-ar", str(SAMPLE_RATE), "-",
    ], capture_output=True, check=True)
    return np.frombuffer(decoded.stdout, dtype="<f4").reshape(-1, CHANNELS)


def write_samples(samples, path):
    """
    Save numbers from read_samples() as a floating-point WAV file.

    Args:
        samples (numpy.ndarray): Shape (samples, 2).
        path (str): Where to write the WAV.
    """
    subprocess.run([
        "ffmpeg", "-nostdin", "-v", "error", "-y",
        "-f", "f32le", "-ac", str(CHANNELS), "-ar", str(SAMPLE_RATE), "-i", "-",
        "-c:a", "pcm_f32le", str(path),
    ], input=samples.astype("<f4").tobytes(), capture_output=True, check=True)


def beep_amplitude(speech_rms, volume):
    """
    Choose how loud a beep is, so it matches the voice it replaces.

    A steady tone's average level (RMS) is its peak divided by √2, so a
    tone with peak = RMS × √2 is as loud as the speech it covers.

    Args:
        speech_rms (float): The average level of the muted word.
        volume (float): The channel's bleep.volume (1 = the same as the voice).

    Returns:
        float: The beep's peak amplitude, kept between sensible limits.

    Example:
        beep_amplitude(0.1, 1.0)  # → 0.1414...
    """
    amplitude = speech_rms * math.sqrt(2) * volume
    return min(MAX_BEEP_AMPLITUDE, max(MIN_BEEP_AMPLITUDE, amplitude))


def bleep_envelope(length, sample_rate):
    """
    Make a smooth on/off shape for one bleep: rises from 0 to 1, holds, falls to 0.

    Args:
        length (int): Number of samples in the bleep.
        sample_rate (int): Samples per second.

    Returns:
        numpy.ndarray: `length` values between 0 and 1.
    """
    envelope = np.ones(length, dtype=np.float32)
    fade = min(int(BLEEP_FADE_SECONDS * sample_rate), length // 2)
    if fade > 0:
        ramp = np.linspace(0, 1, fade, dtype=np.float32)
        envelope[:fade] = ramp
        envelope[length - fade:] = ramp[::-1]
    return envelope


def apply_bleeps(samples, spans, tone_hz, volume, sample_rate=SAMPLE_RATE):
    """
    Replace the sound inside each time range with a beep.

    The original fades out as the beep fades in (and back again at the
    end), so there are no clicks.

    Args:
        samples (numpy.ndarray): Shape (samples, channels), from read_samples().
        spans (list[dict]): [{"start": s, "end": s}, ...] from profanity.bleep_spans().
        tone_hz (int): The beep's pitch, e.g. 1000.
        volume (float): Beep loudness compared with the replaced voice.
        sample_rate (int): Samples per second.

    Returns:
        tuple: (new samples, list of the beep levels used in dBFS).
               The input array is not changed.
    """
    result = samples.copy()
    levels = []
    for span in spans:
        first = int(round(span["start"] * sample_rate))
        last = min(int(round(span["end"] * sample_rate)), len(samples))
        if last <= first:
            levels.append(None)
            continue
        original = samples[first:last]
        speech_rms = float(np.sqrt(np.mean(original ** 2)))
        amplitude = beep_amplitude(speech_rms, volume)
        levels.append(round(20 * math.log10(amplitude), 1))

        envelope = bleep_envelope(last - first, sample_rate)
        times = np.arange(last - first) / sample_rate
        tone = (amplitude * np.sin(2 * math.pi * tone_hz * times)).astype(np.float32) * envelope
        # envelope[:, None] turns the 1-D shape into a column so it
        # multiplies every channel of each sample.
        result[first:last] = original * (1 - envelope[:, None]) + tone[:, None]
    return result, levels


def bleep_audio_file(input_path, output_path, spans, bleep_settings):
    """
    Bleep a WAV file's time ranges and save the result (step 2).

    Args:
        input_path (str): The cleaned WAV from step 1.
        output_path (str): Where to write the bleeped WAV.
        spans (list[dict]): The ranges to bleep.
        bleep_settings (BleepSettings): Tone pitch and volume.

    Returns:
        list: The beep level used for each range, in dBFS (for the bleep log).
    """
    samples = read_samples(input_path)
    bleeped, levels = apply_bleeps(samples, spans, bleep_settings.tone_hz, bleep_settings.volume)
    write_samples(bleeped, output_path)
    return levels


# ---------------------------------------------------------------------------
# Step 3: loudness (two-pass loudnorm)
# ---------------------------------------------------------------------------

def measure_loudnorm(path, target_lufs, true_peak_db):
    """
    Loudnorm pass 1: measure a sound file's loudness, range and peaks.

    Args:
        path (str): The WAV to measure.
        target_lufs (float): The loudness we will aim for, e.g. -14.
        true_peak_db (float): The highest allowed peak, e.g. -1.

    Returns:
        dict: {"input_i", "input_tp", "input_lra", "input_thresh", "target_offset"}
              as numbers (LUFS, dBTP, LU).

    Raises:
        ValueError: If the sound is silent and so has no loudness.
    """
    result = subprocess.run([
        "ffmpeg", "-nostdin", "-hide_banner", "-i", str(path),
        "-af", f"loudnorm=I={target_lufs}:TP={true_peak_db}:LRA=11:print_format=json",
        "-f", "null", "-",
    ], capture_output=True, text=True, check=True)
    # The measurements are printed as a JSON block at the end of the log.
    text = result.stderr[result.stderr.rfind("{"):result.stderr.rfind("}") + 1]
    raw = json.loads(text)
    measured = {}
    for key in ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset"):
        measured[key] = float(raw[key])
        if not math.isfinite(measured[key]):
            raise ValueError("The sound is silent, so its loudness cannot be measured.")
    return measured


def needs_peak_limiting(measured, target_lufs, true_peak_db):
    """
    Check whether the loudest peaks would go over the limit once the sound is made loud enough.

    loudnorm can only raise or lower the whole sound evenly ("linear"
    mode) when the peaks have room. Without room it switches to a mode
    that keeps changing the volume, which makes quiet background noise
    pump up and down.

    Args:
        measured (dict): From measure_loudnorm().
        target_lufs (float): For example -14.
        true_peak_db (float): For example -1.

    Returns:
        bool: True when the peaks must be limited first.

    Example:
        needs_peak_limiting({"input_i": -24, "input_tp": -4}, -14, -1)
        # gain is +10 dB, so the peak would be +6 dBTP → True
    """
    gain = target_lufs - measured["input_i"]
    return measured["input_tp"] + gain > true_peak_db - AAC_HEADROOM_DB


def limit_peaks(input_path, output_path, ceiling_db):
    """
    Lower only the loudest peaks so they stay under a ceiling.

    Args:
        input_path (str): The WAV to limit.
        output_path (str): Where to write the limited WAV.
        ceiling_db (float): The highest allowed peak in dB (before the final gain).
    """
    # alimiter's limit is a plain number between 0.0625 (-24 dB) and 1.
    limit = min(1.0, max(0.0625, 10 ** (ceiling_db / 20)))
    # Working at 4x the sample rate lets the limiter catch peaks that fall
    # between samples ("true peaks"). latency=true keeps the timing exact.
    audio_filter = (f"aresample={SAMPLE_RATE * 4},"
                    f"alimiter=limit={limit:.6f}:attack=2:release=50:level=false:latency=true,"
                    f"aresample={SAMPLE_RATE}")
    subprocess.run([
        "ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(input_path),
        "-af", audio_filter, "-c:a", "pcm_f32le", str(output_path),
    ], capture_output=True, text=True, check=True)


def final_loudness_filter(measured, target_lufs, true_peak_db):
    """
    Build loudnorm pass 2, which runs inside the final video encode.

    The peak target given to loudnorm is the real target minus the AAC
    headroom: in the normal (linear) case the peaks already fit, and if
    loudnorm ever has to fall back to its own limiter, it still leaves
    room for the AAC encoder's small overshoot.

    Args:
        measured (dict): From measure_loudnorm() (pass 1).
        target_lufs (float): For example -14.
        true_peak_db (float): For example -1.

    Returns:
        str: The FFmpeg audio filter, ending at 48 kHz.
    """
    # In linear mode the loudness RANGE target only has to be at least the
    # measured range (otherwise loudnorm switches mode); it changes nothing else.
    loudness_range = min(50, max(11, math.ceil(measured["input_lra"]) + 1))
    peak_target = round(true_peak_db - AAC_HEADROOM_DB, 2)
    return (f"loudnorm=I={target_lufs}:TP={peak_target}:LRA={loudness_range}:"
            f"measured_I={measured['input_i']}:measured_TP={measured['input_tp']}:"
            f"measured_LRA={measured['input_lra']}:measured_thresh={measured['input_thresh']}:"
            f"offset={measured['target_offset']}:linear=true,"
            # loudnorm works at 192 kHz internally; bring it back to 48 kHz.
            f"aresample={SAMPLE_RATE}")


def limited_file_names(limited_path):
    """
    Name the two files used when limiting peaks over several passes.

    Each pass reads one file and writes the other (FFmpeg cannot read
    and write the same file at once).

    Args:
        limited_path (str): For example "clip_audio_limited.wav".

    Returns:
        list[str]: ["clip_audio_limited.wav", "clip_audio_limited.2.wav"].
    """
    return [limited_path, limited_path[:-4] + ".2.wav"]


def prepare_loudness(path, limited_path, settings):
    """
    Run loudnorm pass 1 (limiting peaks first if needed) and build pass 2.

    Args:
        path (str): The cleaned (and bleeped) WAV.
        limited_path (str): Where to write peak-limited copies, if needed
                            (see limited_file_names()).
        settings (ClipAudioSettings): target_lufs and true_peak_db.

    Returns:
        tuple: (the WAV to encode, the pass-2 filter for the encoder,
                the pass-1 measurements).

    Raises:
        ValueError: If the sound is silent (then it is used unchanged).
    """
    target = settings.target_lufs
    peak = settings.true_peak_db
    measured = measure_loudnorm(path, target, peak)
    output_names = limited_file_names(limited_path)
    for attempt in range(MAX_LIMIT_PASSES):
        if not needs_peak_limiting(measured, target, peak):
            break
        gain = target - measured["input_i"]
        output_path = output_names[attempt % 2]
        limit_peaks(path, output_path, peak - AAC_HEADROOM_DB - gain)
        path = output_path
        measured = measure_loudnorm(path, target, peak)
    return path, final_loudness_filter(measured, target, peak), measured

# =============================================================
# Verify exported clips before they can reach the publishing queue.
# FFprobe checks streams and FFmpeg decodes the whole file to catch
# truncated renders. Reports are persisted on the clip for review.
# =============================================================

import json
import re
import subprocess


def check_export(path, expected_seconds):
    """
    Check an MP4 path against expected duration; return pass/fail, stream measurements and errors.

    Args:
        path: The local file path to read or create.
        expected_seconds: The expected edited video duration, in seconds.

    Returns:
        The pass/fail result, measured duration, errors and review warnings.
    """
    result = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
                            capture_output=True, text=True, check=True)
    data = json.loads(result.stdout)
    video = None
    audio = None
    for stream in data.get("streams", []):
        if stream["codec_type"] == "video" and video is None:
            video = stream
        if stream["codec_type"] == "audio" and audio is None:
            audio = stream
    errors = []
    if video is None or video.get("width") != 1080 or video.get("height") != 1920:
        errors.append("The picture must be 1080 × 1920.")
    if video is None or video.get("codec_name") != "h264":
        errors.append("The picture must use H.264.")
    if audio is None or audio.get("codec_name") != "aac":
        errors.append("An AAC audio track is required.")
    duration = float(data.get("format", {}).get("duration", 0))
    if not duration > 0 or abs(duration - expected_seconds) > 0.25:
        errors.append("The output duration differs from the selected moment.")
    decoded = subprocess.run(["ffmpeg", "-nostdin", "-v", "info", "-xerror", "-i", str(path),
                              "-vf", "blackdetect=d=1:pix_th=0.10", "-af", "volumedetect",
                              "-f", "null", "-"], capture_output=True, text=True)
    if decoded.returncode:
        errors.append("The file could not be decoded completely.")
    warnings = []
    if "black_duration:" in decoded.stderr:
        warnings.append("At least one second of mostly black picture; inspect the clip.")
    volume = re.search(r"mean_volume: ([\-\w.]+) dB", decoded.stderr)
    if volume and float(volume[1]) < -45:
        warnings.append("Audio is silent or unusually quiet; inspect the clip.")
    return {"passed": not errors, "duration": duration, "errors": errors, "warnings": warnings,
            "note": "Technical checks only; review speech accuracy, framing and content on a phone."}

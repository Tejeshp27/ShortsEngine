# =============================================================
# src/video/animation/quality_gate.py — checks before the final render
#
# Drawing every frame of a video takes a few minutes, so before we
# start, this file checks the plan for the problems that make a
# video look low-effort or put the channel at risk:
#
#   1. A picture stays on screen too long without changing.
#   2. A part of the video has no visual (no graphic or background).
#   3. A clip, photo, music track or sound effect has no licence/credit.
#   4. A caption would fall outside the safe zone (under the app's buttons).
#   5. The soundtrack is too quiet or too loud (target about -14 LUFS).
#   6. The video is longer than the channel's target length.
#
# Each problem is either an ERROR (the render stops, because the video
# would be unusable or legally risky) or a WARNING (the render goes
# ahead, and the warning is printed and saved for the reviewer).
#
# The report is printed and saved next to the video as
# <video>_quality.json, so the dashboard reviewer can read it too.
#
# How it fits in:
#   renderer.py → run_quality_gate() (here) → draw frames, or stop
# =============================================================

import json

from src.video.animation.drawing import SAFE_BOTTOM, SAFE_LEFT, SAFE_RIGHT, SAFE_TOP
from src.video.animation.overlays import caption_box

# Used when the channel YAML does not set its own under video.quality_gate.
DEFAULT_GATE = {
    "max_still_seconds": 3.0,     # longest a picture may stay without a change
    "target_lufs": -14.0,         # social media loudness
    "lufs_tolerance": 1.0,        # allowed difference from target_lufs
    "max_true_peak": -1.0,        # highest allowed peak (dBTP)
    "length_tolerance": 0.10,     # 10% over the target length is a warning
    "stop_on_errors": True,       # False = print errors but render anyway
}


class QualityGateFailed(Exception):
    """Raised when the plan has errors and the channel says to stop on errors."""


def load_gate_settings(channel_video_settings):
    """
    Read the channel's quality-gate settings, filling gaps with defaults.

    Args:
        channel_video_settings (dict or None): The channel's "video" block.

    Returns:
        dict: Every key of DEFAULT_GATE.
    """
    settings = dict(DEFAULT_GATE)
    channel_gate = (channel_video_settings or {}).get("quality_gate") or {}
    for key in settings:
        if key in channel_gate:
            settings[key] = channel_gate[key]
    return settings


def check_visual_changes(shots, max_still_seconds):
    """
    Find background shots that stay on screen too long.

    Every shot's background moves (footage plays and pans, photos zoom,
    the gradient drifts), so "without motion" means "without a cut" here.

    Args:
        shots (list[dict]): From shots.plan_shots().
        max_still_seconds (float): The longest a shot may last.

    Returns:
        list[str]: Warnings, one per shot that is too long.
    """
    warnings = []
    for shot in shots:
        length = shot["end"] - shot["start"]
        if length > max_still_seconds + 0.01:
            warnings.append(f"Shot at {shot['start']:.1f}s lasts {length:.1f}s "
                            f"(limit {max_still_seconds:.1f}s).")
    return warnings


def check_missing_visuals(scenes, shots):
    """
    Find scenes with no graphic, and shots with no real background.

    Args:
        scenes (list[dict]): Storyboard scenes.
        shots (list[dict]): From shots.plan_shots().

    Returns:
        tuple: (errors, warnings). A scene without a graphic template is an
               error. A shot that falls back to the plain gradient is a
               warning (it still moves, but looks less polished).
    """
    errors = []
    warnings = []
    for scene_number, scene in enumerate(scenes, start=1):
        if not scene.get("template"):
            errors.append(f"Scene {scene_number} has no graphic.")
    gradient_seconds = 0.0
    for shot in shots:
        if shot["kind"] == "gradient":
            gradient_seconds += shot["end"] - shot["start"]
    if gradient_seconds > 0:
        warnings.append(f"{gradient_seconds:.1f}s use the plain gradient background "
                        "(no footage or photo found).")
    return errors, warnings


def check_credits(shots, sound_credits):
    """
    Make sure every outside asset (clip, photo, music, effect) has a licence.

    Args:
        shots (list[dict]): From shots.plan_shots(); footage and photo shots
                            carry a "credit" dict.
        sound_credits (list[dict]): Credits from sound_design.build_soundtrack().

    Returns:
        list[str]: Errors, one per asset missing a licence or source.
    """
    errors = []
    for shot in shots:
        if shot["kind"] == "gradient":
            continue
        credit = shot.get("credit") or {}
        if not credit.get("licence") or not credit.get("source"):
            errors.append(f"{shot['kind'].capitalize()} at {shot['start']:.1f}s "
                          f"({shot['path']}) has no licence or credit.")
    for credit in sound_credits:
        if not credit.get("licence") or not credit.get("source"):
            errors.append(f"Sound '{credit.get('title', '?')}' has no licence or credit.")
    return errors


def check_caption_safe_zone(caption_groups, font_size):
    """
    Make sure every caption sits inside the platforms' safe zone.

    Args:
        caption_groups (list[dict]): From overlays.build_caption_groups().
        font_size (int): The channel's caption font size.

    Returns:
        list[str]: Errors, one per caption outside the safe zone.
    """
    errors = []
    for group in caption_groups:
        left, top, right, bottom = caption_box(group, font_size)
        if left < SAFE_LEFT or right > SAFE_RIGHT or top < SAFE_TOP or bottom > SAFE_BOTTOM:
            spoken = []
            for word in group["words"]:
                spoken.append(word["text"])
            words = " ".join(spoken)
            errors.append(f"Caption '{words}' at {group['start']:.1f}s is outside the safe zone.")
    return errors


def check_audio(loudness, settings):
    """
    Check the soundtrack's loudness against the social-media target.

    Args:
        loudness (dict): {"lufs", "true_peak"} from sound_design.measure_loudness().
        settings (dict): Output of load_gate_settings().

    Returns:
        list[str]: Warnings about loudness (empty if it is on target).
    """
    warnings = []
    lufs = loudness.get("lufs")
    peak = loudness.get("true_peak")
    if lufs is None:
        warnings.append("Could not measure the soundtrack's loudness.")
    elif abs(lufs - settings["target_lufs"]) > settings["lufs_tolerance"]:
        warnings.append(f"Soundtrack is {lufs:.1f} LUFS (target {settings['target_lufs']:.0f}).")
    if peak is not None and peak > settings["max_true_peak"]:
        warnings.append(f"Soundtrack peaks at {peak:.1f} dBTP (limit {settings['max_true_peak']:.1f}); "
                        "it may distort on phones.")
    return warnings


def check_length(duration, target_seconds, tolerance):
    """
    Check whether the video is longer than the channel's target length.

    Args:
        duration (float): The video's length in seconds.
        target_seconds (float or None): The channel's target_length_seconds.
        tolerance (float): Fraction allowed over target before warning (0.10 = 10%).

    Returns:
        list[str]: A warning if the video is too long, else [].
    """
    if not target_seconds or duration <= target_seconds * (1 + tolerance):
        return []
    return [f"Video is {duration:.0f}s; the channel's target is {target_seconds}s. "
            "Shorter videos keep more viewers to the end."]


def run_quality_gate(scenes, shots, caption_groups, font_size, soundtrack, duration,
                     channel_config, report_path):
    """
    Run every check, print a short report, save it, and stop on errors.

    Args:
        scenes (list[dict]): Storyboard scenes with times.
        shots (list[dict]): From shots.plan_shots().
        caption_groups (list[dict]): From overlays.build_caption_groups().
        font_size (int): The channel's caption font size.
        soundtrack (dict): From sound_design.build_soundtrack().
        duration (float): The video's length in seconds.
        channel_config (dict): The channel config.
        report_path (str): Where to save the report (.json).

    Returns:
        dict: {"errors": [...], "warnings": [...], "passed": bool}

    Raises:
        QualityGateFailed: If there are errors and stop_on_errors is True.
    """
    video_settings = channel_config.get("video", {})
    settings = load_gate_settings(video_settings)

    errors = []
    warnings = []
    warnings += check_visual_changes(shots, settings["max_still_seconds"])
    missing_errors, missing_warnings = check_missing_visuals(scenes, shots)
    errors += missing_errors
    warnings += missing_warnings
    errors += check_credits(shots, soundtrack["credits"])
    errors += check_caption_safe_zone(caption_groups, font_size)
    warnings += check_audio(soundtrack["loudness"], settings)
    warnings += check_length(duration, channel_config.get("target_length_seconds"),
                             settings["length_tolerance"])

    report = {"errors": errors, "warnings": warnings, "passed": not errors,
              "shots": len(shots), "loudness": soundtrack["loudness"],
              "duration_seconds": round(duration, 1)}
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    print_report(report)
    if errors and settings["stop_on_errors"]:
        raise QualityGateFailed(
            f"Quality gate found {len(errors)} problem(s); the video was not rendered. "
            f"See {report_path}."
        )
    return report


def print_report(report):
    """
    Print the quality gate's findings in a few short lines.

    Args:
        report (dict): The report built by run_quality_gate().
    """
    status = "PASSED" if report["passed"] else "FAILED"
    print(f"  Quality gate {status}: {report['shots']} shots, "
          f"{report['loudness'].get('lufs')} LUFS, {report['duration_seconds']}s, "
          f"{len(report['errors'])} error(s), {len(report['warnings'])} warning(s)")
    for error in report["errors"][:10]:
        print(f"    ERROR: {error}")
    for warning in report["warnings"][:10]:
        print(f"    warning: {warning}")

# =============================================================
# src/clips/qa.py — automated quality control for finished clips
#
# Every rendered clip is measured before it can reach the review queue.
# Four groups of checks (weights in brackets), each pass/fail with the
# measured value so the owner can see why:
#
#   video (30)    1080x1920 H.264, fully decodable, no black or frozen
#                 stretches, the streamer's face really visible (and not
#                 touching a crop edge) in the finished frames
#   audio (20)    loudness near -14 LUFS, no clipping, not mostly silent,
#                 sound and picture the same length (no drift)
#   captions (20) caption confidence, reading speed, captions kept off the face
#   content (30)  strong opening, a real payoff, not padded, clean ending,
#                 title grounded in what was said, length fits the moment type
#
# "Critical" failures reject the clip; others lower the score. A clip
# below qa.minimum_score is rejected, below qa.flag_below it is flagged
# for the owner. Subjective judgements (humor, payoff) come from the
# ranking/editor-model scores and are labelled as estimates.
# =============================================================

import json
import re
import subprocess

import numpy as np

GROUP_WEIGHTS = {"video": 30, "audio": 20, "captions": 20, "content": 30}


class QARejected(Exception):
    """Raised when a finished clip fails quality control (it is skipped, not retried)."""


def add_check(checks, group, name, passed, value, detail, critical=False):
    """
    Record one check result.

    Args:
        checks (list): The list to add to.
        group (str): "video", "audio", "captions" or "content".
        name (str): Short check name.
        passed (bool): Whether it passed.
        value: The measured value (number or text).
        detail (str): Plain-English explanation.
        critical (bool): True = failing it rejects the clip.
    """
    checks.append({"group": group, "name": name, "passed": bool(passed), "value": value,
                   "detail": detail, "critical": critical})


def measure_output(path):
    """
    Measure a finished video with FFmpeg: black/frozen stretches, silence, loudness, stream lengths.

    Args:
        path (str): The MP4.

    Returns:
        dict: {"black_seconds", "frozen_seconds", "silent_seconds", "loudness",
               "true_peak", "video_seconds", "audio_seconds", "decoded"}.
    """
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(path)],
                           capture_output=True, text=True)
    video_seconds = None
    audio_seconds = None
    try:
        for stream in json.loads(probe.stdout or "{}").get("streams", []):
            if stream.get("codec_type") == "video" and stream.get("duration"):
                video_seconds = float(stream["duration"])
            if stream.get("codec_type") == "audio" and stream.get("duration"):
                audio_seconds = float(stream["duration"])
    except ValueError:
        pass
    result = subprocess.run(["ffmpeg", "-nostdin", "-v", "info", "-i", str(path),
                             "-vf", "blackdetect=d=0.5:pix_th=0.10,freezedetect=n=0.003:d=2",
                             "-af", "silencedetect=noise=-45dB:d=1.5,ebur128=peak=true",
                             "-f", "null", "-"], capture_output=True, text=True)
    text = result.stderr
    black = 0.0
    for value in re.findall(r"black_duration:([\d.]+)", text):
        black += float(value)
    frozen = 0.0
    for value in re.findall(r"freeze_duration: ([\d.]+)", text):
        frozen += float(value)
    silent = 0.0
    for value in re.findall(r"silence_duration: ([\d.]+)", text):
        silent += float(value)
    loudness = None
    peak = None
    summary = text[text.rfind("Summary:"):] if "Summary:" in text else ""
    match = re.search(r"I:\s+(-?[\d.]+) LUFS", summary)
    if match:
        loudness = float(match.group(1))
    match = re.search(r"Peak:\s+(-?[\d.]+|-inf) dBFS", summary)
    if match and match.group(1) != "-inf":
        peak = float(match.group(1))
    return {"black_seconds": round(black, 2), "frozen_seconds": round(frozen, 2),
            "silent_seconds": round(silent, 2), "loudness": loudness, "true_peak": peak,
            "video_seconds": video_seconds, "audio_seconds": audio_seconds,
            "decoded": result.returncode == 0}


def face_visible_in_output(path, framing, duration, samples=4):
    """
    Check the finished frames: is the streamer's face where the layout put it?

    Args:
        path (str): The finished MP4.
        framing (dict): From framing.framed_layout() (its plan and mode).
        duration (float): Video length.
        samples (int): Frames to check.

    Returns:
        dict: {"expected": bool, "frames": n, "seen": n, "touching_edge": n}.
    """
    from src.clips import framing as framing_module

    plan = framing.get("plan") or {}
    if plan.get("mode") not in ("split", "face-crop"):
        return {"expected": False, "frames": 0, "seen": 0, "touching_edge": 0}
    detector = framing_module.face_detector()
    if detector is None:
        return {"expected": True, "frames": 0, "seen": 0, "touching_edge": 0}
    panel_top = 0
    panel_height = 1920
    for panel in plan["panels"]:
        if panel["role"] in ("facecam", "picture"):
            panel_height = panel["height"]
            break
        panel_top += panel["height"]
    seen = 0
    touching = 0
    frames = 0
    for time_seconds in framing_module.sample_times(0, duration, samples):
        command = ["ffmpeg", "-nostdin", "-v", "error", "-ss", f"{time_seconds:.2f}", "-i", str(path),
                   "-frames:v", "1", "-vf", "scale=540:960", "-f", "rawvideo", "-pix_fmt", "bgr24", "-"]
        raw = subprocess.run(command, capture_output=True).stdout
        if len(raw) < 540 * 960 * 3:
            continue
        frame = np.frombuffer(raw[:540 * 960 * 3], dtype=np.uint8).reshape(960, 540, 3)
        frames += 1
        for face in framing_module.faces_in_frame(detector, frame):
            top = face["y"] * 1920
            bottom = (face["y"] + face["h"]) * 1920
            if top >= panel_top - 5 and bottom <= panel_top + panel_height + 5:
                seen += 1
                head = framing_module.head_box(face)
                head_top = head["top"] * 1920
                if head_top < panel_top - 10 or face["x"] < 0.005 or face["x"] + face["w"] > 0.995:
                    touching += 1
                break
    return {"expected": True, "frames": frames, "seen": seen, "touching_edge": touching}


def evaluate(video_path, export_report, render_info, framing, candidate, caption_report, caption_words,
             trim, titles, final_seconds, profile, qa_settings):
    """
    Run every quality check on a finished clip and score it.

    Args:
        video_path (str): The finished MP4.
        export_report (dict): From quality.check_export() (technical gate).
        render_info (dict): From render_clip_video().
        framing (dict): From framing.framed_layout().
        candidate (dict): The ranked moment (components, category, review).
        caption_report (dict): From caption_check.check_captions().
        caption_words (list[dict]): The words actually captioned (moment timeline).
        trim (dict): From trimming.plan_trim() (or a stand-in).
        titles (dict): From titles.choose_titles().
        final_seconds (float): The finished clip's length.
        profile (dict): Ranking profile (thresholds, preferred lengths).
        qa_settings (QASettings): Pass/flag thresholds.

    Returns:
        dict: {"score", "passed", "flagged", "checks", "critical_failures", "repairable"}.
    """
    from src.clips.caption_check import caption_words_per_second

    checks = []
    measured = measure_output(video_path)

    # --- video -----------------------------------------------------------
    add_check(checks, "video", "format", export_report.get("passed", False),
              "1080x1920 H.264/AAC" if export_report.get("passed") else "; ".join(export_report.get("errors", [])),
              "Vertical size, codecs, full decode and length (technical gate).", critical=True)
    add_check(checks, "video", "black_frames", measured["black_seconds"] < 1.0, measured["black_seconds"],
              "Seconds of mostly black picture.")
    add_check(checks, "video", "frozen_picture", measured["frozen_seconds"] < 3.0, measured["frozen_seconds"],
              "Seconds where the picture did not change (frozen/corrupt).")
    face = face_visible_in_output(video_path, framing, final_seconds)
    if face["expected"] and face["frames"]:
        visible = face["seen"] >= max(1, face["frames"] // 2)
        add_check(checks, "video", "face_visible", visible, f"{face['seen']}/{face['frames']} frames",
                  "The streamer's face is visible where the layout puts it.", critical=False)
        add_check(checks, "video", "head_not_cut", face["touching_edge"] <= face["frames"] // 4,
                  f"{face['touching_edge']}/{face['frames']} frames",
                  "The head does not touch the edge of its panel.")
    add_check(checks, "video", "layout_safe", not framing.get("fallback", False),
              framing.get("mode", ""), framing.get("reason", ""))

    # --- audio -----------------------------------------------------------
    loudness = measured["loudness"]
    add_check(checks, "audio", "loudness", loudness is not None and -17.5 <= loudness <= -11.0, loudness,
              "Integrated loudness in LUFS (target -14).")
    peak = measured["true_peak"]
    add_check(checks, "audio", "no_clipping", peak is None or peak <= -0.3, peak, "Peak level in dBFS.")
    silent_share = measured["silent_seconds"] / max(1.0, final_seconds)
    add_check(checks, "audio", "not_silent", silent_share < 0.3, round(silent_share, 2),
              "Share of the clip that is silent.")
    drift = None
    if measured["video_seconds"] and measured["audio_seconds"]:
        drift = round(abs(measured["video_seconds"] - measured["audio_seconds"]), 3)
    add_check(checks, "audio", "in_sync", drift is None or drift <= 0.1, drift,
              "Difference between sound and picture length (seconds).", critical=True)

    # --- captions --------------------------------------------------------
    confidence = caption_report.get("confidence", 1.0) if caption_report else 1.0
    add_check(checks, "captions", "confidence", confidence >= qa_settings.min_caption_confidence, confidence,
              "How trustworthy the captions are (removed hallucinations, loops, unheard words).")
    speed = caption_words_per_second(caption_words) if caption_words else 0.0
    add_check(checks, "captions", "reading_speed", speed <= 7.0, round(speed, 1),
              "Peak caption words per second (TikTok guidance: at most ~5-10).")
    off_face = True
    for problem in framing.get("problems", []):
        if "Text" in problem:
            off_face = False
    add_check(checks, "captions", "off_face", off_face, off_face, "Captions and hook do not cover the face.")

    # --- content ---------------------------------------------------------
    components = candidate.get("components", {})
    hook = components.get("hook", 0.5)
    add_check(checks, "content", "opening", hook >= 0.4, hook, "Hook estimate (first seconds).")
    payoff = max(components.get("humor", 0), components.get("surprise", 0))
    add_check(checks, "content", "payoff", payoff >= profile.get("payoff_floor", 0.45), payoff,
              "Humor/surprise estimate (editorial estimate, not a view prediction).", critical=True)
    add_check(checks, "content", "clear_idea", components.get("clarity", 0.5) >= profile.get("clarity_floor", 0.35),
              components.get("clarity"), "Understandable without watching the stream.")
    padding = trim.get("dead_air_left", 0.0)
    add_check(checks, "content", "no_filler", padding <= max(3.0, final_seconds * 0.15), padding,
              "Seconds of dead air left inside the clip.")
    tail = trim.get("tail_after_speech", 0.0)
    add_check(checks, "content", "clean_ending", tail <= 3.5, tail, "Seconds after the last line.")
    title_ok = bool(titles.get("youtube_title"))
    best = 0.0
    final_title = titles.get("youtube_title", "")
    for idea in titles.get("title_candidates", {}).get("youtube", []):
        # The final title may be the idea with the streamer's name put in front.
        if idea["text"] == final_title or final_title.endswith(": " + idea["text"]):
            best = max(best, idea["score"])
    if "quote" in titles.get("title_method", ""):
        best = 60.0   # taken from the clip's own words
    add_check(checks, "content", "title_matches", title_ok and best >= 50, best,
              "Title score: specific, grounded in the speech, no generic clickbait.")
    lengths = profile.get("lengths", {}).get(candidate.get("category", ""))
    if lengths:
        fits = lengths[0] - 3 <= final_seconds <= lengths[1] + 12
        add_check(checks, "content", "length_fits", fits, round(final_seconds, 1),
                  f"Length vs. {lengths[0]}-{lengths[1]} s preferred for {candidate.get('category')}.")

    # --- score -----------------------------------------------------------
    group_scores = {}
    for group in GROUP_WEIGHTS:
        total = 0
        passed = 0
        for check in checks:
            if check["group"] == group:
                total += 1
                if check["passed"]:
                    passed += 1
        group_scores[group] = passed / total if total else 1.0
    score = 0.0
    for group, weight in GROUP_WEIGHTS.items():
        score += group_scores[group] * weight
    score = round(score, 1)
    critical = []
    for check in checks:
        if check["critical"] and not check["passed"]:
            critical.append(check["name"])
    repairable = []
    for check in checks:
        if not check["passed"] and check["name"] in ("face_visible", "head_not_cut", "off_face"):
            repairable.append(check["name"])
    passed_all = not critical and score >= qa_settings.minimum_score
    return {"score": score, "passed": passed_all, "flagged": passed_all and score < qa_settings.flag_below,
            "group_scores": group_scores, "checks": checks, "critical_failures": critical,
            "repairable": repairable, "measured": measured}

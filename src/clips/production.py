# =============================================================
# src/clips/production.py — turn a chosen moment into a finished,
#                           quality-checked clip (the new editing path)
#
# Order of work for one clip:
#   1. Trim      — tighten start/end, cut dead air, keep comedic pauses (trimming.py)
#   2. Captions  — remove Whisper mistakes, choose which voices are captioned
#                  (caption_check.py + speaker_filter.py)
#   3. Cut       — join the kept parts into one temporary video (editing.py)
#   4. Bleeps    — find swear words on the final timeline (profanity.py)
#   5. Framing   — find the face, choose and check the layout (framing.py)
#   6. Render    — the existing FFmpeg + Pillow renderer (clip_render.py)
#   7. QA        — measure everything; one automatic repair (whole-picture
#                  layout) for a framing problem; reject if it still fails (qa.py)
#   8. Record    — decision log + review files next to the video (decisions.py)
#
# Used by maker.render_and_save() when the channel YAML turns on
# framing/trimming; older channels keep the previous path unchanged.
# =============================================================

import json
import os
import subprocess
import time
from pathlib import Path

from src.clips.settings import find_approved_streamer
from src.clips.store import get_clip, update_clip


def motion_for(source):
    """
    Read the per-second picture-change list saved during analysis, if any.

    Args:
        source (str): The video file (its <name>.signals.json sits next to it).

    Returns:
        list[float]: Motion per second (empty when not measured).
    """
    path = Path(source).with_suffix(".signals.json")
    if not path.is_file():
        return []
    try:
        return json.loads(path.read_text()).get("motion", [])
    except ValueError:
        return []


def sounds_for(source):
    """
    Get per-second sound labels (laughter, speech, music...) for a video, never failing.

    Args:
        source (str): The video file.

    Returns:
        list[dict]: Per-second chances, or [] when the sound model is unavailable.
    """
    from src.clips.audio_events import recording_events

    try:
        return recording_events(source)
    except (OSError, ValueError, RuntimeError):
        return []


def payoff_time_for(signals, start, end):
    """
    Estimate when the payoff happens inside a moment.

    Uses the loudest laughter heard (most reliable), else 1.5 s before the
    end chosen by the editor model (it ends just after the reaction).

    Args:
        signals (dict): The clip's saved ranking record.
        start (float): Moment start.
        end (float): Moment end.

    Returns:
        float or None: Seconds in source time.
    """
    laugh = (signals.get("evidence") or {}).get("peak_laughter_time")
    if isinstance(laugh, (int, float)) and start <= laugh <= end:
        return float(laugh)
    if signals.get("review"):
        return max(start, end - 1.5)
    return None


def words_in(words, start, end):
    """
    The words overlapping a window (source time, unchanged).

    Args:
        words (list[dict]): Timed words.
        start (float): Window start.
        end (float): Window end.

    Returns:
        list[dict]: Copies of the overlapping words.
    """
    inside = []
    for word in words:
        if word["end"] > start and word["start"] < end:
            inside.append(dict(word))
    return inside


def pacing_after_edit(words, start, end):
    """
    Measure dead air left inside the edited clip and the quiet tail after the last line.

    Args:
        words (list[dict]): Words on the final timeline.
        start (float): Final start.
        end (float): Final end.

    Returns:
        dict: {"dead_air_left", "tail_after_speech"} in seconds.
    """
    inside = words_in(words, start, end)
    dead = 0.0
    for index in range(len(inside) - 1):
        gap = inside[index + 1]["start"] - inside[index]["end"]
        if gap > 1.5:
            dead += gap - 1.5
    tail = end - inside[-1]["end"] if inside else end - start
    return {"dead_air_left": round(dead, 2), "tail_after_speech": round(max(0.0, tail), 2)}


def to_render_time(source_time, start, ranges):
    """
    Turn a time in the source video into the matching time in the edited clip.

    Args:
        source_time (float): Seconds in the source.
        start (float): The moment's start in the source.
        ranges (list[tuple]): Kept (begin, end) parts, counted from the moment's start.

    Returns:
        float or None: Seconds in the finished clip, or None when that time was cut out.
    """
    relative = source_time - start
    offset = 0.0
    for begin, end in ranges:
        if begin <= relative < end:
            return round(offset + relative - begin, 3)
        offset += end - begin
    return None


def sound_label_spans(sounds, start, end, ranges, words):
    """
    Find seconds of clear laughing or screaming with no speech, as *LAUGHING* / *SCREAMING* labels.

    Args:
        sounds (list[dict]): Per-second sound chances (source time).
        start (float): Moment start in the source.
        end (float): Moment end in the source.
        ranges (list[tuple]): Kept parts (see to_render_time).
        words (list[dict]): Timed words in source time (labels never cover speech).

    Returns:
        list[dict]: {"start", "end", "text"} in finished-clip time.
    """
    labels = []
    for second in range(int(start), int(end) + 1):
        if second >= len(sounds):
            break
        chances = sounds[second]
        text = ""
        if chances.get("laughter", 0) >= 0.35:
            text = "*LAUGHING*"
        elif chances.get("scream", 0) >= 0.45:
            text = "*SCREAMING*"
        if not text:
            continue
        spoken = False
        for word in words:
            if word["end"] > second and word["start"] < second + 1:
                spoken = True
                break
        if spoken:
            continue
        clip_time = to_render_time(max(float(second), start), start, ranges)
        if clip_time is None:
            continue
        if labels and labels[-1]["text"] == text and clip_time - labels[-1]["end"] < 0.1:
            labels[-1]["end"] = clip_time + 1.0
        else:
            labels.append({"start": clip_time, "end": clip_time + 1.0, "text": text})
    return labels


def punch_settings(framing, payoff_time, start, ranges, final_seconds, effects):
    """
    Decide the payoff zoom: when it starts and which part of the screen it zooms.

    Args:
        framing (dict): From framing.framed_layout() (may be empty).
        payoff_time (float or None): Payoff in source time.
        start (float): Moment start in the source.
        ranges (list[tuple]): Kept parts.
        final_seconds (float): Finished clip length.
        effects (EffectSettings): Zoom amount and black-and-white choice.

    Returns:
        dict or None: For clip_render.punch_in(), or None when off/unknown.
    """
    if effects.payoff_zoom <= 1.0 and not effects.payoff_black_white:
        return None
    if payoff_time is None:
        return None
    begin = to_render_time(payoff_time, start, ranges)
    if begin is None or final_seconds - begin < 0.6:
        return None
    top = 0
    bottom = 1920
    plan = (framing or {}).get("plan")
    if plan and plan.get("panels"):
        panel_top = 0
        for panel in plan["panels"]:
            if panel["role"] in ("facecam", "picture"):
                top = panel_top
                bottom = panel_top + panel["height"]
                break
            panel_top += panel["height"]
    elif plan and plan.get("foreground"):
        top = plan["foreground"]["top"]
        bottom = top + plan["foreground"]["height"]
    return {"start": begin, "zoom": effects.payoff_zoom, "top": int(top), "bottom": int(bottom),
            "black_white": effects.payoff_black_white}


def choose_caption_words(checked, source, start, end, clip, settings, sounds, other_voices_matter):
    """
    Decide which checked words become captions (the streamer, plus other voices when they matter).

    Args:
        checked (list[dict]): Accuracy-checked words in source time.
        source (str): The source video (for voice recognition).
        start (float): Moment start.
        end (float): Moment end.
        clip (dict): The clip row.
        settings (ClipChannelSettings): Caption settings.
        sounds (list[dict]): Per-second sound labels (source time).
        other_voices_matter (bool): The editor model's judgement.

    Returns:
        tuple: (caption words, note for the report).
    """
    from src.clips.caption_check import choose_other_voices
    from src.clips.speaker_filter import mark_speakers

    if not settings.captions.streamer_voice_only:
        return checked, ""
    try:
        marked, note = mark_speakers(checked, source, start, end, clip["platform"], clip["streamer"],
                                     settings.captions.voice_match_min)
    except (OSError, ValueError, subprocess.CalledProcessError):
        return checked, "Voice check failed; all words captioned."
    kept, kept_other, dropped = choose_other_voices(marked, settings.captions.other_voices, sounds, 0.0,
                                                    other_voices_matter)
    if kept_other:
        note = (note + " " if note else "") + f"Kept {kept_other} word(s) from another voice (part of an exchange)."
    if dropped:
        note = (note + " " if note else "") + f"Left out {dropped} word(s) from other voices (game/video/side talk)."
    return kept, note.strip()


def produce_clip(config, settings, clip, moment, words, video_path, notes_path):
    """
    Make one finished clip with the new editing path; save it and its records.

    Args:
        config (dict): Channel YAML.
        settings (ClipChannelSettings): Checked settings.
        clip (dict): The clip row.
        moment (dict): {"start", "end", "hook_title", "reason"}.
        words (list[dict]): The source's timed words.
        video_path (str): Final MP4 path.
        notes_path (str): Notes Markdown path.

    Returns:
        dict: The QA report.

    Raises:
        QARejected: When the finished clip fails quality control.
        ValueError: When the technical gate fails (as before).
    """
    from src.clips import framing as framing_module
    from src.clips.caption_check import check_captions
    from src.clips.decisions import save_part, write_decision_file
    from src.clips.editing import cut_and_join, remap_words
    from src.clips.maker import prepare_bleeps, save_review_files, word_list_path, write_notes
    from src.clips.profanity import load_word_list, mark_profanity
    from src.clips.qa import QARejected, evaluate
    from src.clips.quality import check_export
    from src.clips.ranking import DEFAULT_PROFILE, load_profile
    from src.clips.trimming import plan_trim
    from src.video.clip_render import probe_video, render_clip_video

    began = time.time()
    metadata_options = json.loads(clip.get("metadata_json") or "{}").get("packaging") or {}
    if metadata_options:
        # This clip's packaging experiment: its caption style and context-line time.
        from src.clips.packaging import render_overrides
        settings = render_overrides(settings, metadata_options)
    streamer = find_approved_streamer(settings, clip["streamer"], platform=clip["platform"])
    source = clip["file_path"]
    signals = json.loads(clip.get("signals_json") or "{}")
    metadata = json.loads(clip.get("metadata_json") or "{}")
    decision = json.loads(clip.get("decision_json") or "{}")
    review = signals.get("review") or {}
    category = signals.get("category", "")
    try:
        profile = load_profile(settings.ranking.profile)
    except ValueError:
        profile = dict(DEFAULT_PROFILE)
    sounds = sounds_for(source)
    motion = motion_for(source)

    # 1. Trim.
    start, end = moment["start"], moment["end"]
    payoff = payoff_time_for(signals, start, end)
    if settings.trimming.enabled:
        trim = plan_trim(start, end, words, sounds, motion, payoff, settings.trimming,
                         settings.clips.moment_min_seconds)
    else:
        trim = {"start": start, "end": end, "ranges": [(0.0, round(end - start, 3))], "removed_seconds": 0.0,
                "cuts": 0, "notes": []}
    start, end = trim["start"], trim["end"]

    # 2. Captions (source time).
    moment_words = words_in(words, start, end)
    caption_report = {"confidence": 1.0, "notes": ["accuracy check off"]}
    checked = moment_words
    if settings.captions.check_accuracy:
        checked, caption_report = check_captions(moment_words, sounds)
    caption_words, voice_note = choose_caption_words(checked, source, start, end, clip, settings, sounds,
                                                     review.get("other_voices_matter", False))

    # 3. Cut dead air (one temporary file) and move every word onto the new timeline.
    edited_path = os.path.splitext(video_path)[0] + ".edited.mp4"
    render_source, render_start, render_end = source, start, end
    all_words = words
    cleanup = None
    if len(trim["ranges"]) > 1:
        try:
            render_source, render_start, render_end, all_words, cleanup = cut_and_join(
                source, start, end, trim["ranges"], words, edited_path)
            caption_words = remap_words(caption_words, start, trim["ranges"])
        except (subprocess.CalledProcessError, OSError):
            trim["notes"].append("Dead-air cuts failed; the uncut moment was used.")
            trim["ranges"] = [(0.0, round(end - start, 3))]

    # 4. Bleeps on the final timeline; mask the same words in the captions.
    all_marked, spans = prepare_bleeps(all_words, render_start, render_end, settings.bleep,
                                       render_source, settings.transcription)
    if settings.bleep.enabled:
        caption_words = mark_profanity(caption_words, load_word_list(word_list_path(settings.bleep)),
                                       settings.bleep.mask_captions)

    # 5. Framing.
    details = probe_video(render_source)
    framing = {}
    if settings.framing.enabled:
        choice = framing_module.decide_framing(render_source, render_start, render_end, details["width"],
                                               details["height"], settings.framing, category, streamer.facecam)
        framing = framing_module.framed_layout(choice, details["width"], details["height"], settings)

    # 6. Render (to a temporary name), with at most qa.max_repairs repairs.
    hook_text = metadata.get("hook_text") or moment["hook_title"]
    temporary_path = os.path.splitext(video_path)[0] + ".rendering.mp4"
    os.makedirs(os.path.dirname(video_path) or ".", exist_ok=True)
    titles = decision.get("titles", {})
    repairs = []
    try:
        while True:
            labels = []
            if settings.captions.sound_labels:
                labels = sound_label_spans(sounds, start, end, trim["ranges"], words)
            punch = punch_settings(framing, payoff, start, trim["ranges"], render_end - render_start,
                                   settings.effects)
            render_info = render_clip_video(render_source, render_start, render_end, caption_words, hook_text,
                                            clip["credit_text"], streamer.facecam, settings, temporary_path,
                                            layout_mode=streamer.layout or None, bleep_spans=spans,
                                            framing=framing or None, sound_labels=labels, punch=punch)
            final_seconds = render_end - render_start
            export_report = check_export(temporary_path, final_seconds)
            pacing = pacing_after_edit(all_words, render_start, render_end)
            trim_for_qa = dict(trim)
            trim_for_qa.update(pacing)
            shown_words = words_in(caption_words, render_start, render_end)
            qa = evaluate(temporary_path, export_report, render_info, framing, signals, caption_report,
                          shown_words, trim_for_qa, titles, final_seconds, profile, settings.qa)
            if qa["repairable"] and len(repairs) < settings.qa.max_repairs and framing.get("mode") != "fit-blur":
                repairs.append("Re-rendered with the whole picture because: " + ", ".join(qa["repairable"]))
                from src.video.clip_layout import plan_layout, text_positions
                plan = plan_layout(details["width"], details["height"], "fit-blur", None,
                                   foreground_center=settings.layout.foreground_center)
                framing = dict(framing)
                framing.update({"plan": plan, "positions": text_positions(plan), "mode": "fit-blur",
                                "fallback": True, "reason": repairs[-1]})
                continue
            break
    finally:
        if cleanup and os.path.exists(cleanup):
            os.remove(cleanup)

    # 7. Record everything, whatever the result.
    qa["repairs"] = repairs
    report = dict(export_report)
    report["render"] = render_info
    report["render"]["path"] = video_path
    report["warnings"] = report.get("warnings", []) + render_info.get("warnings", [])
    report["bleeps"] = len(spans)
    report["qa"] = {"score": qa["score"], "passed": qa["passed"], "flagged": qa["flagged"],
                    "critical_failures": qa["critical_failures"], "repairs": repairs}
    if voice_note:
        report["voice_check"] = voice_note
    if qa["flagged"]:
        report["warnings"].append(f"QA score {qa['score']} is below {settings.qa.flag_below}; review carefully.")
    update_clip(clip["id"], quality_json=json.dumps(report))
    save_part(clip["id"], "edit", {"chosen_start": moment["start"], "chosen_end": moment["end"],
                                   "final_start": start, "final_end": end, "final_seconds": round(final_seconds, 2),
                                   "cuts": len(trim["ranges"]) - 1, "removed_seconds": trim["removed_seconds"],
                                   "notes": trim["notes"], "payoff_time": payoff,
                                   "sound_labels": len(labels), "payoff_zoom": bool(punch)})
    save_part(clip["id"], "captions", dict(caption_report, voice_note=voice_note,
                                           style=settings.captions.style,
                                           other_voices=settings.captions.other_voices,
                                           captioned_words=len(caption_words)))
    framing_record = {"mode": framing.get("mode", render_info.get("layout")), "reason": framing.get("reason", ""),
                      "fallback": framing.get("fallback", False), "problems": framing.get("problems", []),
                      "faces": framing.get("faces", {})}
    save_part(clip["id"], "framing", framing_record)
    qa_record = {}
    for key in ("score", "passed", "flagged", "group_scores", "checks", "critical_failures", "repairs"):
        qa_record[key] = qa[key]
    save_part(clip["id"], "qa", qa_record)
    record = save_part(clip["id"], "output", {"path": video_path, "render_seconds": round(time.time() - began, 1),
                                              "hook_text": hook_text, "title": moment["hook_title"]})
    if not export_report["passed"]:
        os.remove(temporary_path)
        raise ValueError("Clip failed quality checks: " + " ".join(export_report["errors"]))
    if not qa["passed"]:
        os.remove(temporary_path)
        reasons = qa["critical_failures"] or [f"QA score {qa['score']} below {settings.qa.minimum_score}"]
        raise QARejected("Rejected by quality control: " + ", ".join(reasons))
    os.replace(temporary_path, video_path)
    save_review_files(video_path, all_marked, render_start, render_end, spans, settings, render_info)
    write_decision_file(video_path, record)
    from src.clips.metadata import publishing_caption
    caption = publishing_caption(get_clip(clip["id"]), moment["hook_title"],
                                 config.get("publishing", {}).get("hashtags", []))
    notes_moment = dict(moment)
    notes_moment["reason"] = (signals.get("reason") or moment.get("reason") or "")
    write_notes(notes_path, clip, notes_moment, words, caption, settings)
    return qa

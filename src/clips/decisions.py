# =============================================================
# src/clips/decisions.py — remember every editorial decision
#
# For each clip the system saves (in the clips.decision_json column and
# in a <video>.decision.json file next to the MP4):
#   selection — source, original start/end, category, score and every
#               category score, why it was chosen, ranking profile version,
#               the editor model's reading, the moments it beat;
#   edit      — trimming notes, cuts, final start/end and length;
#   captions  — caption checks and confidence, which voices were kept;
#   framing   — faces found, layout chosen and why, any fallback;
#   titles    — every title idea with its score and problems, final texts;
#   qa        — the quality report and score;
#   output    — file path, render time, AI cost when known.
#
# Each recording's selection (including REJECTED moments and why) is also
# appended to output/clip_decisions/<channel>.jsonl, one JSON line each,
# so the reasoning can be inspected later with any text tool or with
# `python -m src.clips --channel twitch_clips decisions`.
# These records feed the improvement loop in src/clips/feedback.py.
# =============================================================

import json
import time
from pathlib import Path

LOG_FOLDER = Path("output") / "clip_decisions"


def log_event(channel, kind, data):
    """
    Append one decision record to the channel's JSON-lines log.

    Args:
        channel (str): The channel key.
        kind (str): What happened, e.g. "recording_ranked" or "clip_rendered".
        data (dict): The details (must be JSON-friendly).

    Returns:
        str: The log file path.
    """
    LOG_FOLDER.mkdir(parents=True, exist_ok=True)
    path = LOG_FOLDER / f"{channel}.jsonl"
    record = {"time": round(time.time(), 1), "kind": kind}
    record.update(data)
    with open(path, "a", encoding="utf-8") as file:
        file.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    return str(path)


def save_part(clip_id, part, data):
    """
    Add or replace one section of a clip's decision record in the database.

    Args:
        clip_id (int): The clip.
        part (str): Section name, e.g. "selection", "edit", "qa".
        data (dict): The section's details.

    Returns:
        dict: The clip's whole decision record after the change.
    """
    from src.clips.store import get_clip, update_clip

    clip = get_clip(clip_id)
    record = {}
    if clip is not None:
        try:
            record = json.loads(clip.get("decision_json") or "{}")
        except ValueError:
            record = {}
    record[part] = data
    update_clip(clip_id, decision_json=json.dumps(record, ensure_ascii=False, default=str))
    return record


def write_decision_file(video_path, record):
    """
    Save a clip's decision record next to its video as <video>.decision.json.

    Args:
        video_path (str): The MP4's path.
        record (dict): The whole decision record.

    Returns:
        str: The saved path.
    """
    path = Path(video_path).with_suffix(".decision.json")
    path.write_text(json.dumps(record, indent=2, ensure_ascii=False, default=str))
    return str(path)


def selection_record(candidate, recording, profile, rejected_others):
    """
    Build the "selection" section from a ranked candidate.

    Args:
        candidate (dict): A selected candidate (ranking.decide_candidates).
        recording (dict): The recording row (id, streamer, file, source kind).
        profile (dict): The ranking profile used.
        rejected_others (list[dict]): Candidates rejected in the same recording.

    Returns:
        dict: The section.
    """
    review = candidate.get("review") or {}
    others = []
    for other in rejected_others[:8]:
        others.append({"start": other["start"], "end": other["end"], "score": other.get("score"),
                       "reason": other.get("rejected", "")})
    return {"recording_id": recording["id"], "source_kind": recording.get("source_kind", ""),
            "streamer": recording["streamer"], "original_start": candidate["start"],
            "original_end": candidate["end"], "category": candidate.get("category", ""),
            "score": candidate.get("score"), "local_score": candidate.get("local_score"),
            "components": candidate.get("components", {}), "evidence": candidate.get("evidence", {}),
            "reason": candidate.get("reason", ""), "what_happened": review.get("what_happened", ""),
            "why_funny": review.get("why_funny", ""), "who": review.get("who", ""),
            "editor_reviewed": bool(review), "found_by": candidate.get("source", "local"),
            "scout_why": candidate.get("scout_why", ""), "profile_version": profile.get("version", ""),
            "profile_path": profile.get("path", ""), "rejected_in_same_recording": others}


def read_log(channel, limit=200):
    """
    Read the newest records from a channel's decision log.

    Args:
        channel (str): The channel key.
        limit (int): How many of the newest records to return.

    Returns:
        list[dict]: Records, oldest first.
    """
    path = LOG_FOLDER / f"{channel}.jsonl"
    if not path.is_file():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()[-limit:]
    records = []
    for line in lines:
        try:
            records.append(json.loads(line))
        except ValueError:
            continue
    return records

# =============================================================
# src/clips/taste.py — teach the editor model the owner's taste
#
# The owner's judgement is the real target. This collects:
#   1. config/owner_taste.yaml — rules and examples in the owner's words;
#   2. the newest clips the owner approved/posted (liked) or rejected
#      (disliked) in the review queue, with any review notes.
# and turns them into a short block of text added to the editor model's
# input, so each new decision is made with the owner's latest verdicts in
# mind. Free: it only reads local files and the database.
# =============================================================

import json
from pathlib import Path

import yaml

from src.storage.database import get_connection

TASTE_FILE = Path("config") / "owner_taste.yaml"


def taste_file_lines(path=None):
    """
    Read the owner's written rules and examples.

    Args:
        path (Path or None): The YAML file (None = config/owner_taste.yaml).

    Returns:
        list[str]: Lines such as "Rule: ..." and "DISLIKED: ... — why".
    """
    if path is None:
        path = TASTE_FILE
    if not Path(path).is_file():
        return []
    data = yaml.safe_load(Path(path).read_text()) or {}
    lines = []
    for note in data.get("notes", []) or []:
        lines.append("Rule: " + str(note))
    for example in data.get("examples", []) or []:
        verdict = "LIKED" if str(example.get("verdict", "")).lower() == "liked" else "DISLIKED"
        lines.append(f"{verdict}: {example.get('clip', '')} — {example.get('why', '')}")
    return lines


def review_verdicts(channel, limit=8):
    """
    The owner's latest review decisions on clips (approved/posted = liked, rejected = disliked).

    Args:
        channel (str): The clips channel.
        limit (int): Most verdicts of each kind.

    Returns:
        list[str]: Lines such as "LIKED: StableRonaldo got roasted ... (37 s)".
    """
    connection = get_connection()
    try:
        rows = connection.execute("""
            SELECT v.status, v.story_title, v.reviewer_notes, c.decision_json, c.start_seconds, c.end_seconds
            FROM videos v JOIN clips c ON c.video_id = v.id
            WHERE v.channel = ? AND v.status IN ('approved', 'posted', 'failed')
            ORDER BY v.id DESC LIMIT 200
        """, (channel,)).fetchall()
    finally:
        connection.close()
    liked = []
    disliked = []
    for row in rows:
        decision = json.loads(row["decision_json"] or "{}")
        what = decision.get("selection", {}).get("what_happened") or row["story_title"]
        seconds = decision.get("edit", {}).get("final_seconds")
        if seconds is None and row["end_seconds"] is not None and row["start_seconds"] is not None:
            seconds = row["end_seconds"] - row["start_seconds"]
        line = f"{what} ({seconds:.0f} s)" if seconds else what
        if row["reviewer_notes"]:
            line += " — owner's note: " + row["reviewer_notes"][:200]
        if row["status"] == "failed":
            if len(disliked) < limit:
                disliked.append("DISLIKED: " + line)
        elif len(liked) < limit:
            liked.append("LIKED: " + line)
    return liked + disliked


def taste_block(channel):
    """
    The text added to the editor model's input describing the owner's taste.

    Args:
        channel (str): The clips channel.

    Returns:
        str: The block, or "" when nothing is known yet.
    """
    lines = taste_file_lines() + review_verdicts(channel)
    if not lines:
        return ""
    return ("The channel owner's taste (their own rules and latest verdicts; judge new moments the way "
            "they would):\n" + "\n".join(lines))

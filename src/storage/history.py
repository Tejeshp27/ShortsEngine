# =============================================================
# src/storage/history.py — remember which stories we already used
#
# So we never write (and pay for) a script about the same story
# twice, each channel keeps a small JSON file of the story URLs
# it has already turned into scripts:
#     cache/<channel>/processed.json
#
# This is a simple stand-in. In Phase 3 it moves into the database.
# =============================================================

import json
import os
from datetime import datetime


def history_path(channel_key):
    """
    Return the path of a channel's "already processed" file.

    Args:
        channel_key (str): The channel's file name, e.g. "news".

    Returns:
        str: e.g. "cache/news/processed.json"
    """
    return os.path.join("cache", channel_key, "processed.json")


def load_processed(channel_key):
    """
    Load the stories this channel has already turned into scripts.

    Args:
        channel_key (str): The channel's file name, e.g. "news".

    Returns:
        dict: Story URL -> details (title, date, script path).
              Empty if nothing has been processed yet.
    """
    path = history_path(channel_key)
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def remove_processed(stories, processed):
    """
    Drop stories that were already turned into scripts.

    Args:
        stories (list[dict]): Candidate stories.
        processed (dict): The result of load_processed().

    Returns:
        list[dict]: Only the stories whose URL is not in processed.
    """
    fresh = []
    for story in stories:
        if story["url"] not in processed:
            fresh.append(story)
    return fresh


def mark_processed(channel_key, story, script_path):
    """
    Record that a story now has a script, so it's skipped next time.

    Args:
        channel_key (str): The channel's file name, e.g. "news".
        story (dict): The story that was written about.
        script_path (str): Where its script was saved.
    """
    processed = load_processed(channel_key)
    processed[story["url"]] = {
        "title": story["title"],
        "processed_at": datetime.now().isoformat(timespec="seconds"),
        "script_path": script_path,
    }

    path = history_path(channel_key)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(processed, f, indent=2)

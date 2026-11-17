# =============================================================
# src/dashboard/settings.py — settings shared by the dashboard
#
# The web pages and the background worker both need two kinds of
# settings:
#   1. Job limits from config/dashboard.yaml (daily spending cap,
#      how many jobs may run at once, time limits).
#   2. A channel's YAML file from the channels/ folder.
#
# Channel names arrive from the browser, so this file also refuses
# any name that could point outside channels/ (for example
# "../.env"). Channel content itself always stays in YAML.
# =============================================================

import re
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

# The project folder. This file is src/dashboard/settings.py, so the
# project is two folders above the folder this file is in.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
CHANNEL_FOLDER = PROJECT_ROOT / "channels"
SETTINGS_PATH = PROJECT_ROOT / "config" / "dashboard.yaml"

# A channel name may only contain letters, numbers, "_" and "-".
# Slashes and dots are not allowed, so a name can never become a
# path such as "../secrets".
VALID_CHANNEL_NAME = r"[a-zA-Z0-9_-]{1,80}"


class JobSettings(BaseModel):
    """
    The limits for work started from the browser.

    pydantic checks every value when the object is created. A mistake
    in config/dashboard.yaml (for example a negative spending limit)
    therefore stops with a clear error instead of being used.
    Field(default=..., gt=0) means "use this default, and the value must
    be greater than 0"; ge/le mean "at least" and "at most".

    Fields:
        daily_limit_usd: Most dollars browser jobs may spend per UTC day.
        max_in_progress: How many heavy jobs (writing or rendering a
                         video) may be queued or running at once.
        max_job_minutes: A job running longer than this is stopped.
        stale_after_seconds: A job whose worker has been silent this
                             long is marked "interrupted".
        max_source_chars: Largest article size a channel may send to the AI.
    """

    daily_limit_usd: float = Field(default=2.0, gt=0, le=1000)
    max_in_progress: int = Field(default=1, ge=1, le=4)
    max_job_minutes: int = Field(default=60, ge=1, le=240)
    stale_after_seconds: int = Field(default=90, ge=30, le=600)
    max_source_chars: int = Field(default=20000, ge=1000, le=50000)


def job_settings():
    """
    Read the job limits from config/dashboard.yaml.

    Returns:
        JobSettings: The checked limits. Any setting missing from the
                     file uses its default value.

    Example:
        job_settings().daily_limit_usd   # → 2.0
    """
    if not SETTINGS_PATH.exists():
        return JobSettings()

    config = yaml.safe_load(SETTINGS_PATH.read_text(encoding="utf-8")) or {}
    job_section = config.get("jobs", {})
    return JobSettings(**job_section)


def channel_config(channel):
    """
    Load one channel's YAML settings.

    Args:
        channel (str): The channel's file name without ".yaml", e.g. "news".

    Returns:
        dict: Everything in channels/<channel>.yaml.

    Raises:
        ValueError: If the name is not allowed, the file does not exist,
                    the YAML is broken, or the channel has no name.

    Example:
        channel_config("news")["name"]   # → "News Made Simple"
    """
    if not re.fullmatch(VALID_CHANNEL_NAME, channel):
        raise ValueError("Choose a valid channel.")

    path = CHANNEL_FOLDER / f"{channel}.yaml"
    # resolve() follows shortcuts (symlinks). The second check makes sure
    # a shortcut inside channels/ cannot point at a file somewhere else.
    if not path.is_file():
        raise ValueError("That channel does not exist.")
    if not path.resolve().is_relative_to(CHANNEL_FOLDER.resolve()):
        raise ValueError("That channel does not exist.")

    try:
        config = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as error:
        raise ValueError(f"Fix the YAML in channels/{channel}.yaml.") from error

    if not isinstance(config, dict) or not config.get("name"):
        raise ValueError(f"Add a name to channels/{channel}.yaml.")
    return config


def list_channels():
    """
    List every channel file, including broken ones.

    A broken file is still listed (with its error) so the dashboard can
    show what needs fixing instead of hiding the channel.

    Returns:
        list[dict]: One dict per channel with "key", "name", "niche",
                    "platforms" and "error" ("" when the file is fine).
    """
    channels = []
    for path in sorted(CHANNEL_FOLDER.glob("*.yaml")):
        key = path.stem
        try:
            config = channel_config(key)
            channels.append({
                "key": key,
                "name": config["name"],
                "niche": config.get("niche", ""),
                "platforms": config.get("platforms", []),
                "error": "",
            })
        except ValueError as error:
            channels.append({
                "key": key,
                "name": key,
                "niche": "",
                "platforms": [],
                "error": str(error),
            })
    return channels

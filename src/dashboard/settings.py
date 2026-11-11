# =============================================================
# Validated settings shared by the dashboard and background worker.
# Channel content stays in YAML; this module also rejects unsafe
# channel names before they can become file paths.
# =============================================================

import re
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CHANNEL_FOLDER = PROJECT_ROOT / "channels"
SETTINGS_PATH = PROJECT_ROOT / "config" / "dashboard.yaml"


class JobSettings(BaseModel):
    """Hold validated spending and job limits; inputs are YAML values.

    The resulting object supplies safe defaults for omitted settings.
    """

    daily_limit_usd: float = Field(default=2.0, gt=0, le=1000)
    max_in_progress: int = Field(default=1, ge=1, le=4)
    max_job_minutes: int = Field(default=60, ge=1, le=240)
    stale_after_seconds: int = Field(default=90, ge=30, le=600)
    max_source_chars: int = Field(default=20000, ge=1000, le=50000)


def job_settings():
    """Read shared YAML settings and return a validated JobSettings object."""
    if not SETTINGS_PATH.exists():
        return JobSettings()
    config = yaml.safe_load(SETTINGS_PATH.read_text(encoding="utf-8")) or {}
    return JobSettings(**config.get("jobs", {}))


def channel_config(channel):
    """Return the YAML dictionary for channel, or raise ValueError.

    channel is a file stem such as 'news'; paths and unknown names are refused.
    """
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", channel):
        raise ValueError("Choose a valid channel.")
    path = CHANNEL_FOLDER / f"{channel}.yaml"
    if not path.is_file() or not path.resolve().is_relative_to(CHANNEL_FOLDER.resolve()):
        raise ValueError("That channel does not exist.")
    try:
        config = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as error:
        raise ValueError(f"Fix the YAML in channels/{channel}.yaml.") from error
    if not isinstance(config, dict) or not config.get("name"):
        raise ValueError(f"Add a name to channels/{channel}.yaml.")
    return config


def list_channels():
    """Return display details for all channel YAML files, including broken ones."""
    channels = []
    for path in sorted(CHANNEL_FOLDER.glob("*.yaml")):
        try:
            config = channel_config(path.stem)
            channels.append({"key": path.stem, "name": config["name"],
                             "niche": config.get("niche", ""),
                             "platforms": config.get("platforms", []), "error": ""})
        except ValueError as error:
            channels.append({"key": path.stem, "name": path.stem, "niche": "",
                             "platforms": [], "error": str(error)})
    return channels

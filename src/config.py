# =============================================================
# src/config.py — load channel configuration files
#
# Channel configs live in channels/<name>.yaml. This module
# reads one and returns it as a Python dictionary so the rest
# of the code can use channel["name"], channel["sources"], etc.
# =============================================================

import os
import yaml


def load_channel_config(channel_name):
    """
    Load a channel config from channels/<channel_name>.yaml.

    Args:
        channel_name (str): The channel name, e.g. "news".
                            Must match a file in the channels/ folder.

    Returns:
        dict: The parsed YAML as a Python dictionary.

    Raises:
        SystemExit: If the file doesn't exist, with a helpful message.

    Example:
        config = load_channel_config("news")
        print(config["name"])  # "News Explained"
    """
    # Build the path relative to the project root.
    # os.path.join handles slashes correctly on all operating systems.
    config_path = os.path.join("channels", f"{channel_name}.yaml")

    if not os.path.exists(config_path):
        print(f"ERROR: No config file found at '{config_path}'.")
        print(f"       Create channels/{channel_name}.yaml to add this channel.")
        raise SystemExit(1)

    with open(config_path, "r") as f:
        # yaml.safe_load parses the YAML and returns a plain dict.
        # 'safe_load' is preferred over 'load' — it won't execute
        # arbitrary Python code if the file is ever malformed.
        config = yaml.safe_load(f)

    return config

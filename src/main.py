# =============================================================
# src/main.py — command-line entry point for ShortsEngine
#
# This is the main file you run. It reads a channel config,
# then runs the pipeline for that channel:
#   Phase 1, step 1: fetch → deduplicate → print story list
#   (ranking and script writing come in later steps)
#
# Usage:
#   python -m src.main --channel news
#   python -m src.main --channel news --dry-run
# =============================================================

import argparse
import os
import sys

from dotenv import load_dotenv

from src.config import load_channel_config
from src.sources.rss import fetch_all_stories, remove_duplicates, filter_recent


def check_environment():
    """
    Confirm that required environment variables are present.

    Prints a warning for each missing variable so the user knows
    what to add to their .env file.

    Returns:
        bool: True if all required variables are set, False otherwise.
    """
    required_vars = ["ANTHROPIC_API_KEY"]
    all_present = True

    for var in required_vars:
        if not os.getenv(var):
            print(f"  WARNING: {var} is not set. Add it to your .env file.")
            all_present = False

    return all_present


def print_story_list(stories):
    """
    Print a numbered list of stories to the terminal.

    Args:
        stories (list[dict]): Story dicts with 'title', 'source', 'url'.
    """
    if not stories:
        print("  No stories found in the last 24 hours.")
        return

    for i, story in enumerate(stories, start=1):
        print(f"  {i}. {story['title']}")
        print(f"     Source : {story['source']}")
        print(f"     Link   : {story['url']}")
        print()


def main():
    """
    Parse command-line arguments and run the pipeline for one channel.

    Example:
        python -m src.main --channel news
        python -m src.main --channel news --dry-run
    """
    # --- Parse arguments ---
    parser = argparse.ArgumentParser(
        description="ShortsEngine — automated short-form video pipeline"
    )
    parser.add_argument(
        "--channel",
        required=True,
        help="Name of the channel to run (must match a file in channels/)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run without making any paid API calls (useful for testing)",
    )
    args = parser.parse_args()

    # --- Load .env ---
    load_dotenv()

    # --- Header ---
    print("=" * 55)
    print("ShortsEngine — Phase 1")
    print("=" * 55)
    print(f"Channel  : {args.channel}")
    print(f"Dry run  : {args.dry_run}")
    print()

    # --- Check environment ---
    print("Checking environment...")
    env_ok = check_environment()
    if env_ok:
        print("  OK — all required environment variables are set.")
    print()

    # --- Load channel config ---
    print(f"Loading channel config: channels/{args.channel}.yaml")
    config = load_channel_config(args.channel)
    print(f"  Channel: {config['name']}")
    print(f"  Niche  : {config['niche']}")
    print(f"  Sources: {len(config['sources'])} feed(s)")
    print()

    # --- Fetch stories ---
    print("Fetching stories from RSS feeds...")
    all_stories = fetch_all_stories(config["sources"])
    print(f"  Fetched {len(all_stories)} total entries.")

    # Remove stories that appeared in more than one feed (same URL).
    unique_stories = remove_duplicates(all_stories)
    removed = len(all_stories) - len(unique_stories)
    if removed > 0:
        print(f"  Removed {removed} duplicate(s).")

    # Keep only stories from the last 24 hours, sorted newest first.
    recent_stories = filter_recent(unique_stories, hours=24)
    print(f"  {len(recent_stories)} stories from the last 24 hours.")
    print()

    # --- Print story list ---
    print("=" * 55)
    print(f"Today's stories — {config['name']}")
    print("=" * 55)
    print()
    print_story_list(recent_stories)


if __name__ == "__main__":
    main()

# =============================================================
# src/main.py — command-line entry point for ShortsEngine
#
# This is the main file you run. It reads a channel config,
# then runs the pipeline for that channel (fetch → rank → write).
#
# Phase 0: prints a greeting and confirms the environment works.
# Later phases will add real pipeline steps here.
#
# Usage:
#   python -m src.main --channel news
#   python -m src.main --channel news --dry-run
# =============================================================

import argparse
import os
import sys

# python-dotenv reads key=value pairs from .env and puts them
# into environment variables so the rest of the code can use
# os.getenv("ANTHROPIC_API_KEY") etc.
from dotenv import load_dotenv


def check_environment():
    """
    Confirm that required environment variables are present.

    Prints a warning (not an error) for each missing variable so the
    user knows what to add to their .env file.

    Returns:
        bool: True if everything is in order, False if something is missing.
    """
    required_vars = [
        "ANTHROPIC_API_KEY",
    ]

    all_present = True

    for var in required_vars:
        value = os.getenv(var)
        if not value:
            print(f"  WARNING: {var} is not set. Add it to your .env file.")
            all_present = False

    return all_present


def main():
    """
    Parse command-line arguments and run the pipeline for one channel.

    Example:
        python -m src.main --channel news
        python -m src.main --channel news --dry-run
    """
    # --- Parse arguments ---
    # argparse turns command-line flags like --channel news into
    # a Python object we can read (e.g. args.channel == "news").
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
    # load_dotenv() looks for a .env file in the current directory and
    # loads it. If the file doesn't exist it does nothing (no error).
    load_dotenv()

    # --- Greet and confirm environment ---
    print("=" * 50)
    print("ShortsEngine — Phase 0")
    print("=" * 50)
    print(f"Channel  : {args.channel}")
    print(f"Dry run  : {args.dry_run}")
    print()

    print("Checking environment...")
    env_ok = check_environment()

    if env_ok:
        print("  OK — all required environment variables are set.")
    else:
        print()
        print("  Add the missing keys to a file called .env in the project root.")
        print("  See .env.example for the list of required keys.")
        # We don't exit with an error here so the hello-world still runs.

    print()
    print("Hello from ShortsEngine! Phase 0 is complete.")
    print("Next: Phase 1 will fetch real news stories and write a script.")


if __name__ == "__main__":
    main()

# =============================================================
# src/main.py — command-line entry point for ShortsEngine
#
# This is the main file you run. For one channel it runs the
# Phase 1 pipeline:
#   1. load the channel config (channels/<name>.yaml)
#   2. fetch stories from RSS, remove duplicates and old stories
#   3. skip stories that already have a script
#   4. pick a story — by the cheap AI model, or by you (--pick)
#   5. get the article text (downloaded once, then cached)
#   6. write a neutral, sourced script with the writing model
#   7. save it to output/<channel>/<date>-<slug>.md
#   8. log the estimated cost of the run to output/cost_log.csv
#
# Usage:
#   python -m src.main --channel news --list       # just show stories (free)
#   python -m src.main --channel news --dry-run    # whole pipeline, no paid calls
#   python -m src.main --channel news              # AI picks a story, writes script
#   python -m src.main --channel news --pick 3     # you pick story #3 from --list
# =============================================================

import argparse
import os

from dotenv import load_dotenv

from src.config import load_channel_config
from src.generation.llm import CallLimitReached, LLMClient, LLMError
from src.generation.ranking import rank_stories
from src.generation.writer import write_script
from src.sources.articles import get_article_text
from src.sources.rss import fetch_all_stories, filter_recent, remove_duplicates
from src.storage.history import load_processed, mark_processed, remove_processed


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
        print("  No new stories found.")
        return

    for number, story in enumerate(stories, start=1):
        print(f"  {number}. {story['title']}")
        print(f"     Source : {story['source']}")
        print(f"     Link   : {story['url']}")
        print()


def parse_arguments():
    """
    Read the options typed after "python -m src.main".

    Returns:
        argparse.Namespace: The options, e.g. args.channel, args.dry_run.
    """
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
        help="Run the whole pipeline without any paid API calls (shows estimated cost)",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="Only list today's new stories, then stop (free, no AI)",
    )
    parser.add_argument(
        "--pick",
        type=int,
        metavar="N",
        help="Write about story number N from --list instead of letting the AI choose",
    )
    return parser.parse_args()


def get_candidate_stories(config, channel_key):
    """
    Fetch, clean and filter the stories a channel could cover today.

    Args:
        config (dict): The channel config.
        channel_key (str): The channel's file name, e.g. "news".

    Returns:
        list[dict]: New, recent, unique stories, newest first.
    """
    print("Fetching stories from RSS feeds...")
    all_stories = fetch_all_stories(config["sources"])
    print(f"  Fetched {len(all_stories)} total entries.")

    # Remove stories that appeared in more than one feed (same URL).
    unique_stories = remove_duplicates(all_stories)
    removed = len(all_stories) - len(unique_stories)
    if removed > 0:
        print(f"  Removed {removed} duplicate(s).")

    # Keep only recent stories, sorted newest first.
    hours_back = config["selection"]["hours_back"]
    recent_stories = filter_recent(unique_stories, hours=hours_back)
    print(f"  {len(recent_stories)} stories from the last {hours_back} hours.")

    # Skip stories we already wrote a script for (never pay twice).
    processed = load_processed(channel_key)
    new_stories = remove_processed(recent_stories, processed)
    already_done = len(recent_stories) - len(new_stories)
    if already_done > 0:
        print(f"  Skipped {already_done} story(ies) that already have a script.")
    print()

    return new_stories


def choose_story(stories, config, llm, pick_number):
    """
    Decide which story to write about.

    Args:
        stories (list[dict]): Candidate stories, newest first.
        config (dict): The channel config.
        llm (LLMClient): The run's AI client.
        pick_number (int or None): A story number chosen by the user, or None
                                   to let the ranking model choose.

    Returns:
        dict or None: The chosen story, or None if pick_number is invalid.
    """
    if pick_number is not None:
        if pick_number < 1 or pick_number > len(stories):
            print(f"ERROR: --pick must be between 1 and {len(stories)}. "
                  "Run with --list to see the numbers.")
            return None
        print(f"Using story #{pick_number} (your pick — no ranking call needed).")
        return stories[pick_number - 1]

    print(f"Ranking stories with {config['models']['ranking']}...")
    chosen_stories, reason = rank_stories(stories, config, llm)

    print()
    print("  Top picks:")
    for number, story in enumerate(chosen_stories, start=1):
        print(f"    {number}. {story['title']} ({story['source']})")
    if reason:
        print(f"  Why: {reason}")
    print()

    return chosen_stories[0]


def print_cost_summary(llm):
    """
    Show how many AI calls this run made and what they cost.

    Args:
        llm (LLMClient): The run's AI client, holding the totals.
    """
    print("=" * 55)
    print("Cost summary")
    print("=" * 55)
    print(f"  API calls    : {llm.calls_made} (cached answers reused: {llm.cache_hits})")
    print(f"  Tokens       : {llm.total_input_tokens} in / {llm.total_output_tokens} out")
    if llm.dry_run:
        print(f"  Est. cost    : at most ${llm.total_cost:.4f} (dry run — nothing was charged)")
    else:
        print(f"  Est. cost    : ${llm.total_cost:.4f}")
    print("  Logged to    : output/cost_log.csv")


def main():
    """
    Parse command-line arguments and run the pipeline for one channel.

    Example:
        python -m src.main --channel news --dry-run
    """
    args = parse_arguments()
    load_dotenv()

    # --- Header ---
    print("=" * 55)
    print("ShortsEngine — Phase 1: news script generator")
    print("=" * 55)
    print(f"Channel  : {args.channel}")
    print(f"Dry run  : {args.dry_run}")
    print()

    # --- Load channel config ---
    config = load_channel_config(args.channel)
    print(f"Loaded channels/{args.channel}.yaml — {config['name']}")
    print()

    # --- Get stories ---
    stories = get_candidate_stories(config, args.channel)

    if args.list:
        print_story_list(stories)
        return

    if not stories:
        print("No new stories to write about. Try again later.")
        return

    # --- Check the API key (not needed for a dry run) ---
    if not args.dry_run and not check_environment():
        print("ERROR: Missing ANTHROPIC_API_KEY in .env — "
              "add it, or use --dry-run to test without paid calls.")
        raise SystemExit(1)

    llm = LLMClient(dry_run=args.dry_run, max_calls=config["llm"]["max_calls_per_run"])
    note = ""

    try:
        story = choose_story(stories, config, llm, args.pick)
        if story is None:
            raise SystemExit(1)
        note = story["title"]

        print(f"Reading article: {story['url']}")
        article_text = get_article_text(
            story,
            max_chars=config["articles"]["max_chars"],
            fetch_full_text=config["articles"]["fetch_full_text"],
        )
        print()

        print(f"Writing script with {config['models']['writing']}...")
        script_path = write_script(story, article_text, config, args.channel, llm)
        print()
        print(f"Script saved: {script_path}")
        print("Next step: open it, fact-check against the sources, and edit as needed.")
        print()

        # Only remember the story for real runs, so a dry run
        # doesn't stop you writing the real script later.
        if not args.dry_run:
            mark_processed(args.channel, story, script_path)

    except CallLimitReached as error:
        print(f"STOPPED: {error}")
    except LLMError as error:
        print(f"ERROR: {error}")
    finally:
        # Always log what was spent, even if something went wrong.
        llm.write_cost_log(args.channel, note=note)
        print_cost_summary(llm)


if __name__ == "__main__":
    main()

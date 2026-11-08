# =============================================================
# src/main.py — command-line entry point for ShortsEngine
#
# This is the main file you run. It supports four modes:
#
#   --list    Show today's new stories (free, no AI).
#   --dry-run Full pipeline with no paid API calls.
#   (default) AI picks a story, writes a script, saves it to the
#             database, renders the video if --render is given.
#   --render  Run only video production on the most recent
#             approved script. Skips the script-writing step.
#   --review  Open the review queue — approve or reject drafted
#             scripts before they move to video production.
#
# Full pipeline (Phase 1 → 2 → 3):
#   1. Load the channel config (channels/<name>.yaml)
#   2. Fetch stories from RSS, remove duplicates and old stories
#   3. Skip stories that already have a script
#   4. Pick a story — by AI, or by you (--pick N)
#   5. Download the article text (cached after first download)
#   6. Write a neutral, sourced script with the writing model
#   7. Save the script + DB row (status = drafted)
#   8. --review: approve or reject scripts
#   9. --render: convert an approved script into a 9:16 MP4
#  10. Log the cost of every run to output/cost_log.csv
#
# Usage:
#   python -m src.main --channel news --list
#   python -m src.main --channel news --dry-run
#   python -m src.main --channel news
#   python -m src.main --channel news --pick 3
#   python -m src.main --channel news --review
#   python -m src.main --channel news --render
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
from src.storage.database import create_tables
from src.storage.history import load_processed, mark_processed, remove_processed
from src.storage.queries import (
    get_videos_by_status,
    save_cost_event,
    save_video,
    set_video_cost,
    set_video_path,
)


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
        argparse.Namespace: The parsed options.
    """
    parser = argparse.ArgumentParser(
        description="ShortsEngine — automated short-form video pipeline"
    )
    parser.add_argument(
        "--channel",
        required=False,
        default=None,
        help="Name of the channel to run (must match a file in channels/). "
             "Required for all modes except --report (which shows all channels if omitted).",
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
    parser.add_argument(
        "--render",
        action="store_true",
        help="Render the most recent approved script into a video (Phase 2)",
    )
    parser.add_argument(
        "--visuals",
        action="store_true",
        help="Generate a visual plan (stock photos + charts) for the video (Phase V.2–V.6)",
    )
    parser.add_argument(
        "--no-paid-visuals",
        action="store_true",
        dest="no_paid_visuals",
        help="Skip Pexels stock photos; use only free local visuals (charts and text cards)",
    )
    parser.add_argument(
        "--review",
        action="store_true",
        help="Open the review queue to approve or reject drafted scripts (Phase 3)",
    )
    parser.add_argument(
        "--report",
        action="store_true",
        help="Print a cost and activity report for this channel (Phase 3)",
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


def choose_story(stories, config, llm, pick_number, channel_key=None):
    """
    Decide which story to write about.

    Args:
        stories (list[dict]): Candidate stories, newest first.
        config (dict): The channel config.
        llm (LLMClient): The run's AI client.
        pick_number (int or None): A story number chosen by the user, or None
                                   to let the ranking model choose.
        channel_key (str or None): Channel name, used to load analytics feedback.

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
    chosen_stories, reason = rank_stories(stories, config, llm, channel_key=channel_key)

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


def run_script_pipeline(args, config, llm):
    """
    Run the script-writing pipeline (Phase 1) and save the result to the DB.

    Args:
        args: Parsed command-line arguments.
        config (dict): The channel config.
        llm (LLMClient): The run's AI client.

    Returns:
        str or None: The path of the saved script, or None if something failed.
    """
    stories = get_candidate_stories(config, args.channel)

    if args.list:
        print_story_list(stories)
        return None

    if not stories:
        print("No new stories to write about. Try again later.")
        return None

    story = choose_story(stories, config, llm, args.pick, channel_key=args.channel)
    if story is None:
        return None

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

    # Save to the database (status = drafted) so the review queue can find it.
    if not args.dry_run:
        video_id = save_video(
            channel=args.channel,
            story_title=story["title"],
            story_url=story["url"],
            script_path=script_path,
            cost_usd=llm.total_cost,
        )
        print(f"Saved to database (ID {video_id}, status: drafted).")
        print("Next step: run --review to approve it, then --render to make the video.")
        mark_processed(args.channel, story, script_path)
    else:
        print("Next step: open the script, fact-check, then run --review.")

    print()
    return script_path


def run_render_pipeline(args, config):
    """
    Render the most recently approved script into a video (Phase 2).

    Finds the latest approved script for the channel from the database,
    generates the voiceover and captions, and assembles the MP4 with FFmpeg.

    Args:
        args: Parsed command-line arguments.
        config (dict): The channel config.
    """
    # Import here so the render dependencies (edge-tts, ffmpeg) are only
    # needed when actually rendering, not on every run.
    from src.video.render import render_video

    approved = get_videos_by_status("approved", channel=args.channel)

    if not approved:
        print(f"No approved scripts found for '{args.channel}'.")
        print("Run --review first to approve a drafted script.")
        return

    # Pick the most recently approved one (the list is ordered newest first).
    video_row = approved[0]
    script_path = video_row["script_path"]

    if not script_path or not os.path.exists(script_path):
        print(f"ERROR: Script file not found: {script_path}")
        return

    voice = config["video"]["voice"]
    use_visuals = getattr(args, "visuals", False)
    no_paid_visuals = getattr(args, "no_paid_visuals", False)

    print(f"Rendering video for: {video_row['story_title']}")
    print(f"Script  : {script_path}")
    print(f"Voice   : {voice}")
    if use_visuals:
        print(f"Visuals : enabled (paid={'no' if no_paid_visuals else 'yes, Pexels'})")
    print()

    # Only create an LLM client when visuals are requested (to avoid
    # needing an API key for plain --render runs).
    llm = None
    if use_visuals and not args.dry_run:
        from src.generation.llm import LLMClient
        llm = LLMClient(dry_run=False,
                        max_calls=config["llm"]["max_calls_per_run"])

    video_path = render_video(
        script_path=script_path,
        story_title=video_row["story_title"],
        channel_key=args.channel,
        voice=voice,
        font_size=config["video"].get("font_size", 90),
        dry_run=args.dry_run,
        llm=llm,
        channel_config=config if use_visuals else None,
        no_paid_visuals=no_paid_visuals,
    )

    if not args.dry_run:
        set_video_path(video_row["id"], video_path)
        print(f"Database updated — video path recorded (ID {video_row['id']}).")


def main():
    """
    Parse command-line arguments and run the appropriate pipeline step.

    Example:
        python -m src.main --channel news --dry-run
    """
    args = parse_arguments()
    load_dotenv()

    # Ensure the database and its tables exist before anything else.
    create_tables()

    # --- Header ---
    print("=" * 55)
    print("ShortsEngine")
    print("=" * 55)
    print(f"Channel  : {args.channel or '(all)'}")
    if hasattr(args, "dry_run"):
        print(f"Dry run  : {args.dry_run}")
    print()

    # --- Cost report mode (Phase 3) — works with or without --channel ---
    if args.report:
        from src.storage.reports import print_cost_report
        print_cost_report(channel=args.channel)
        return

    # Everything below requires --channel.
    if not args.channel:
        print("ERROR: --channel is required. Example: --channel news")
        raise SystemExit(1)

    # --- Load channel config ---
    config = load_channel_config(args.channel)
    print(f"Loaded channels/{args.channel}.yaml — {config['name']}")
    print()

    # --- Review queue mode (Phase 3) ---
    if args.review:
        from src.review import run_review_queue
        run_review_queue(args.channel)
        return

    # --- Render mode (Phase 2): video production only ---
    if args.render:
        run_render_pipeline(args, config)
        return

    # --- Script-writing pipeline (Phase 1) ---
    if not args.dry_run and not check_environment():
        print("ERROR: Missing ANTHROPIC_API_KEY in .env — "
              "add it, or use --dry-run to test without paid calls.")
        raise SystemExit(1)

    llm = LLMClient(dry_run=args.dry_run, max_calls=config["llm"]["max_calls_per_run"])
    note = ""

    try:
        script_path = run_script_pipeline(args, config, llm)
        if script_path:
            note = os.path.basename(script_path)

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

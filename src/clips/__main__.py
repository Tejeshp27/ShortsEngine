# =============================================================
# src/clips/__main__.py — command line for clips channels
#
# Run with "python -m src.clips" (Python runs this file when a
# folder is started with -m). Commands:
#
#   connect STREAMER   one-time Twitch login so their clips can be downloaded
#   find               L.3: find new top clips and download them
#   make               L.4-L.6: pick the moment, render, send to review
#   run                find + make in one go (the normal daily command)
#   upload FILE        add a clip file an approved streamer sent you (Kick has no clip API)
#   list               show the channel's clips and where each one is
#
# Every command needs --channel, e.g.:
#   python -m src.clips --channel twitch_clips run --dry-run
# =============================================================

import argparse

from dotenv import load_dotenv

from src.clips.settings import StreamerNotApproved, find_approved_streamer, load_clip_settings
from src.config import load_channel_config
from src.generation.llm import LLMClient
from src.sources.twitch import TwitchError
from src.storage.database import create_tables, get_connection


def parse_arguments():
    """
    Read the command and options typed after "python -m src.clips".

    Returns:
        argparse.Namespace: The parsed options.
    """
    parser = argparse.ArgumentParser(description="ShortsEngine clips channels (Twitch highlights)")
    parser.add_argument("--channel", required=True, help="The clips channel, e.g. twitch_clips")
    commands = parser.add_subparsers(dest="command", required=True)

    connect = commands.add_parser("connect", help="Let an approved streamer allow clip downloads")
    connect.add_argument("streamer", help="Their Twitch login name")
    add_dry_run_option(connect)

    for name, help_text in (("find", "Find and download new top clips (L.3)"),
                            ("make", "Pick moments and render videos for review (L.4-L.6)"),
                            ("run", "find + make")):
        command = commands.add_parser(name, help=help_text)
        command.add_argument("--streamer", help="Only this approved streamer")
        if name != "find":
            command.add_argument("--limit", type=int, help="Make at most this many videos")
        add_dry_run_option(command)

    upload = commands.add_parser("upload", help="Add a clip file an approved streamer sent you")
    add_dry_run_option(upload)
    upload.add_argument("file", help="The video file (.mp4, .mov, .mkv, .webm)")
    upload.add_argument("--streamer", required=True, help="The approved streamer the clip is from")
    upload.add_argument("--platform", choices=["kick", "twitch"], default="kick",
                        help="Where that streamer streams (default: kick)")
    upload.add_argument("--i-own-this", action="store_true", dest="i_own_this",
                        help="Confirm the file came from the streamer (or you) and you may use it")
    upload.add_argument("--note", default="", help='Optional note, e.g. "sent by DM on 2026-11-18"')

    list_command = commands.add_parser("list", help="Show this channel's clips")
    add_dry_run_option(list_command)
    return parser.parse_args()


def add_dry_run_option(command):
    """
    Give one command the --dry-run option.

    It is added to each command (not to the main parser) so it can be
    typed after the command name: "... find --dry-run".

    Args:
        command: An argparse sub-command parser.
    """
    command.add_argument("--dry-run", action="store_true",
                         help="No paid AI calls, no downloads, nothing saved")


def run_connect(args, config):
    """
    Run the one-time Twitch login for an approved streamer.

    Args:
        args: The parsed options (args.streamer).
        config (dict): The channel's YAML settings.
    """
    from src.clips.twitch_login import connect_streamer

    settings = load_clip_settings(config)
    # Refuse before contacting Twitch if the streamer is not an approved
    # Twitch streamer. (Kick has no clip download API, so no login exists.)
    find_approved_streamer(settings, args.streamer, args.channel, platform="twitch")
    if args.dry_run:
        print("Dry run: would start the Twitch login. Nothing was sent.")
        return
    print(connect_streamer(args.streamer))


def run_find(args, config):
    """
    Find and download new top clips (L.3).

    Args:
        args: The parsed options.
        config (dict): The channel's YAML settings.
    """
    from src.clips.finder import find_and_download

    print("Finding top clips from the last day...")
    totals = find_and_download(args.channel, config, args.streamer, args.dry_run)
    print()
    print(f"Found {totals['new']} new clip(s), {totals['already_saved']} already saved, "
          f"{totals['skipped']} skipped, {totals['downloaded']} downloaded.")
    if args.dry_run:
        print("Dry run: nothing was saved or downloaded.")


def run_make(args, config):
    """
    Pick moments and render drafted videos (L.4-L.6), then log the cost.

    Args:
        args: The parsed options.
        config (dict): The channel's YAML settings.
    """
    from src.clips.maker import make_videos

    llm = LLMClient(dry_run=args.dry_run, max_calls=config.get("llm", {}).get("max_calls_per_run", 15))
    print("Making videos from downloaded clips...")
    try:
        video_ids = make_videos(args.channel, config, llm, args.streamer, args.dry_run,
                                getattr(args, "limit", None))
    finally:
        # Always log what was spent, even if something went wrong.
        llm.write_cost_log(args.channel, note="clips make")
    print()
    print(f"AI calls: {llm.calls_made} (cached: {llm.cache_hits}) · estimated cost ${llm.total_cost:.4f}")
    if video_ids:
        print(f"{len(video_ids)} video(s) wait for review: python -m src.main --channel {args.channel} "
              "--review, or the dashboard's review queue.")
    elif not args.dry_run:
        print("No new videos. Run 'find' first, or check errors with 'list'.")


def run_upload(args, config):
    """
    Add a video file you own (or have permission for) as a clip.

    Args:
        args: The parsed options.
        config (dict): The channel's YAML settings.
    """
    from src.clips.finder import add_uploaded_video

    if args.dry_run:
        print("Dry run: would add the file. Nothing was saved.")
        return
    clip_id = add_uploaded_video(args.channel, config, args.file, args.streamer, args.platform,
                                 args.i_own_this, args.note)
    if clip_id is None:
        print("This exact file was added before; nothing changed.")
        return
    print(f"Added as clip {clip_id}. Run 'make' to turn it into a video for review.")


def run_list(args):
    """
    Print the channel's clips with their pipeline step.

    Args:
        args: The parsed options.
    """
    connection = get_connection()
    try:
        rows = connection.execute(
            "SELECT * FROM clips WHERE channel = ? ORDER BY id DESC LIMIT 50", (args.channel,)
        ).fetchall()
    finally:
        connection.close()
    if not rows:
        print("No clips yet. Run 'find' (or 'upload').")
        return
    for row in rows:
        line = f"  {row['id']:>4}  {row['status']:<10} {row['streamer']:<20} {row['title'][:40]}"
        if row["video_id"]:
            line += f"  → video {row['video_id']}"
        print(line)
        if row["last_error"]:
            print(f"        problem: {row['last_error']}")


def main():
    """Run the chosen clips command with clear error messages."""
    args = parse_arguments()
    load_dotenv()
    create_tables()
    config = load_channel_config(args.channel)
    print(f"Channel: {config.get('name', args.channel)}" + (" (dry run)" if args.dry_run else ""))

    try:
        if args.command == "connect":
            run_connect(args, config)
        elif args.command == "find":
            run_find(args, config)
        elif args.command == "make":
            run_make(args, config)
        elif args.command == "run":
            run_find(args, config)
            print()
            run_make(args, config)
        elif args.command == "upload":
            run_upload(args, config)
        else:
            run_list(args)
    except (StreamerNotApproved, TwitchError, ValueError) as error:
        print(f"ERROR: {error}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()

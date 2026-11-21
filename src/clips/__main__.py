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
#   discover           save public clip/VOD metadata only, never download media
#   cut FILE           make a free exact-timestamp draft from an authorized recording
#   export IDS         package checked clips and text for official manual uploads
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
    upload.add_argument("--platform", choices=["kick", "twitch", "youtube"], default="kick",
                        help="Where that streamer streams (default: kick)")
    upload.add_argument("--i-own-this", action="store_true", dest="i_own_this",
                        help="Confirm the file came from the streamer (or you) and you may use it")
    upload.add_argument("--note", default="", help='Optional note, e.g. "sent by DM on 2026-11-18"')

    list_command = commands.add_parser("list", help="Show this channel's clips")
    add_dry_run_option(list_command)
    discover = commands.add_parser("discover", help="Discover Twitch clip/VOD metadata; never download video")
    discover.add_argument("--streamer", help="Only this configured creator")
    add_dry_run_option(discover)
    cycle = commands.add_parser("cycle", help="Monitor, ingest, score and render using the persistent worker")
    add_dry_run_option(cycle)
    record = commands.add_parser("record", help="Import a full authorized recording and prepare multiple highlights")
    record.add_argument("file", help="Path to a completed local recording")
    record.add_argument("--streamer", required=True, help="An approved streamer")
    record.add_argument("--platform", choices=["twitch", "kick", "youtube"], required=True)
    record.add_argument("--i-own-this", action="store_true", dest="i_own_this")
    add_dry_run_option(record)
    watch = commands.add_parser("watch", help="Keep checking this channel until Ctrl-C")
    add_dry_run_option(watch)
    cut = commands.add_parser("cut", help="Render a 15–60 second timestamp selection without paid AI")
    cut.add_argument("file", help="A completed authorized recording")
    cut.add_argument("--streamer", required=True)
    cut.add_argument("--platform", choices=["twitch", "kick", "youtube"], required=True)
    cut.add_argument("--start", type=float, required=True, help="Start seconds in the source")
    cut.add_argument("--end", type=float, required=True, help="End seconds in the source")
    cut.add_argument("--title", required=True, help="A truthful hook title")
    cut.add_argument("--i-own-this", action="store_true", dest="i_own_this")
    add_dry_run_option(cut)
    export = commands.add_parser("export", help="Package videos and metadata for official manual uploads")
    export.add_argument("video_ids", nargs="+", type=int, help="IDs from the review queue")
    export.add_argument("--output", required=True, help="A new folder inside output/")
    export.add_argument("--platform", choices=["both", "youtube", "tiktok"], default="both",
                        help="Manual destinations; choose one when the other already has the video")
    add_dry_run_option(export)
    decisions = commands.add_parser("decisions", help="Show recent clip decisions: chosen and rejected moments, with reasons")
    decisions.add_argument("--limit", type=int, default=10, help="How many recent recordings to show")
    add_dry_run_option(decisions)
    feedback = commands.add_parser("feedback", help="Compare published clips' performance and suggest ranking changes")
    feedback.add_argument("--write", action="store_true", help="Save a suggested profile file (never activated automatically)")
    add_dry_run_option(feedback)
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
        elif args.command == "discover":
            run_cycle(args)
        elif args.command in ("cycle", "record"):
            run_cycle(args)
        elif args.command == "watch":
            run_watch(args)
        elif args.command in ("cut", "export"):
            run_launch(args, config)
        elif args.command == "decisions":
            run_decisions(args)
        elif args.command == "feedback":
            run_feedback(args, config)
        else:
            run_list(args)
    except (StreamerNotApproved, TwitchError, ValueError) as error:
        print(f"ERROR: {error}")
        raise SystemExit(1)


def run_decisions(args):
    """
    Print the newest ranking decisions from output/clip_decisions/<channel>.jsonl.

    Args:
        args: Parsed options (channel, limit).
    """
    from src.clips.decisions import read_log

    records = []
    for record in read_log(args.channel, 500):
        if record.get("kind") == "recording_ranked":
            records.append(record)
    if not records:
        print("No ranking decisions logged yet.")
        return
    for record in records[-args.limit:]:
        reviewed = "editor model + local evidence" if record.get("editor_reviewed") else "local evidence only"
        print(f"\nRecording {record['recording_id']} · {record['streamer']} · {record['duration']:.0f}s · "
              f"profile {record.get('profile')} · {reviewed}")
        for chosen in record.get("chosen", []):
            print(f"  CHOSEN  {chosen['start']:.1f}-{chosen['end']:.1f}s score {chosen['score']} "
                  f"[{chosen.get('category')}] {chosen.get('title')}")
            print(f"          why: {chosen.get('reason')}")
        for rejected in record.get("rejected", []):
            print(f"  skipped {rejected['start']:.1f}-{rejected['end']:.1f}s score {rejected.get('score')}: "
                  f"{rejected.get('reason')}")


def run_feedback(args, config):
    """
    Print the performance comparison and any suggested ranking profile.

    Args:
        args: Parsed options (channel, write).
        config: The channel YAML.
    """
    from src.clips.feedback import feedback_report
    from src.clips.ranking import load_profile
    from src.clips.settings import load_clip_settings

    settings = load_clip_settings(config)
    report = feedback_report(args.channel, load_profile(settings.ranking.profile), write=args.write)
    print(f"Published clips with statistics: {report['published']}")
    for name, groups in report["comparisons"].items():
        print(f"\nBy {name}:")
        for group in groups:
            flag = "" if group["enough_data"] else "  (too few to judge)"
            print(f"  {group['group']:<22} {group['clips']:>3} clips · median {group['median_relative_views']}x "
                  f"normal views/day{flag}")
    if settings.packaging.enabled:
        from src.clips.packaging import packaging_report
        lab = packaging_report(args.channel, settings)
        print(f"\nPackaging experiments ({lab['published']} published clips with statistics):")
        for dimension, info in lab["dimensions"].items():
            parts = []
            for option, numbers in info["stats"].items():
                parts.append(f"{option} {numbers['successes']}/{numbers['tries']} ({info['chance_best'][option]:.0%} best)")
            verdict = f"WINNER: {info['winner']}" if info["winner"] else "still testing"
            print(f"  {dimension:<14} " + " · ".join(parts) + f"  → {verdict}")
    print("\nSuggestion:")
    for reason in report["reasons"]:
        print("  - " + reason)
    if report["proposal_path"]:
        print(f"Saved (not active): {report['proposal_path']}")
    print("\nLimits:")
    for limit in report["limits"]:
        print("  - " + limit)


def run_launch(args, config):
    """
    Run a free timestamp cut or manual-upload export and print the result.

    Args:
        args: Parsed command options, including channel and dry_run.
        config: The selected clips channel's current settings.

    Returns:
        Nothing; prints the draft ID or bundle location, with no approval or upload.
    """
    import json
    from src.clips.launch import cut_recording, export_bundle, validate_interval

    if args.command == "export":
        platforms = ["youtube", "tiktok"] if args.platform == "both" else [args.platform]
        result = export_bundle(args.channel, config, args.video_ids, args.output, args.dry_run, platforms)
        if args.dry_run:
            print(json.dumps(result, indent=2))
        else:
            print(f"Exported {len(result['clips'])} clips to {args.output}. No uploads performed.")
        return
    settings = load_clip_settings(config)
    find_approved_streamer(settings, args.streamer, args.channel, args.platform)
    validate_interval(args.start, args.end, settings.clips.moment_max_seconds)
    if not args.i_own_this:
        raise ValueError("cut requires --i-own-this and an approved streamer.")
    if args.dry_run:
        print("Dry run: would import, transcribe and render the selection. No media read, paid calls or uploads.")
        return
    video_id = cut_recording(args.channel, config, args.file, args.streamer, args.platform,
                             args.start, args.end, args.title, args.i_own_this)
    print(f"Video {video_id} is ready for review at /videos/{video_id}. Nothing uploaded.")


def run_cycle(args):
    """
    Queue one cycle or authorized recording from CLI arguments; wait for its worker and print the result.

    Args:
        args: The options parsed from the command line.

    Returns:
        Nothing; the worker result is printed after completion.
    """
    import json
    import time
    import uuid
    from src.dashboard.jobs import enqueue_job, job_record, launch_job

    payload = {}
    if args.command == "discover" and args.streamer:
        payload["streamer"] = args.streamer
    if args.command == "record":
        payload["recording"] = {"path": args.file, "streamer": args.streamer,
                                 "platform": args.platform, "confirmed": args.i_own_this}
        if not args.i_own_this:
            raise ValueError("record requires --i-own-this and an approved streamer.")
    kind = "clip_discovery" if args.command == "discover" else "clip_pipeline"
    job_id, created = enqueue_job(args.channel, kind, payload, args.dry_run, uuid.uuid4().hex)
    if created:
        launch_job(job_id)
    print(f"Job {job_id}; dashboard: /jobs/{job_id}")
    while True:
        job = job_record(job_id)
        if job["status"] not in ("queued", "running"):
            print(json.dumps(job["result"], indent=2))
            if job["error"]:
                raise ValueError(job["error"])
            return
        time.sleep(1)


def run_watch(args):
    """
    Run CLI monitoring and the publishing schedule until Ctrl-C; dry-run arguments perform only one harmless cycle.

    Args:
        args: The options parsed from the command line.

    Returns:
        Nothing; monitoring runs until interrupted by the user.
    """
    import threading
    from src.clips.automation import queue_due_channels, set_enabled
    from src.publishing.scheduler import run_scheduler

    if args.dry_run:
        args.command = "cycle"
        return run_cycle(args)
    set_enabled(args.channel, True)
    stopped = threading.Event()
    print("Monitoring started. Ctrl-C stops checks; active workers finish safely.")
    try:
        while not stopped.is_set():
            queue_due_channels(args.channel)
            run_scheduler(only_channel=args.channel)
            stopped.wait(15)
    except KeyboardInterrupt:
        set_enabled(args.channel, False)
        print("Monitoring paused.")


if __name__ == "__main__":
    main()

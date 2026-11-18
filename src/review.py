# =============================================================
# src/review.py — command-line review queue (Phase 3)
#
# Shows drafted scripts and videos waiting for approval, then
# lets you approve, reject, or skip each one. Nothing moves
# to "approved" without going through this step.
#
# Run with:
#   python -m src.main --channel news --review
#
# The review loop works through the database (src/storage/queries.py).
# It never reads script files itself — it just shows you the path
# and waits for your decision.
# =============================================================


from src.storage.queries import (
    approve_video,
    get_videos_by_status,
    reject_video,
)


def _print_divider():
    """Print a line of dashes to visually separate entries."""
    print("-" * 60)


def _print_video_summary(video):
    """
    Print the key details of one video row to the terminal.

    Args:
        video (sqlite3.Row): A row from the videos table.
    """
    _print_divider()
    print(f"ID       : {video['id']}")
    print(f"Channel  : {video['channel']}")
    print(f"Title    : {video['story_title']}")
    print(f"Source   : {video['story_url']}")
    print(f"Script   : {video['script_path']}")
    if video["video_path"]:
        print(f"Video    : {video['video_path']}")
    else:
        print("Video    : (not yet rendered)")
    print(f"Cost     : ${video['total_cost_usd']:.4f}")
    print(f"Created  : {video['created_at']}")


def _prompt_decision(video_id):
    """
    Ask the reviewer what to do with one video and return their choice.

    Args:
        video_id (int): The ID of the video being reviewed.

    Returns:
        tuple[str, str]: (action, notes)
            action is one of: "approve", "reject", "skip", "quit"
            notes is any text the reviewer typed (may be empty).
    """
    print()
    print("Options: [a] approve   [r] reject   [s] skip   [q] quit")
    choice = input("Your choice: ").strip().lower()

    if choice in ("a", "approve"):
        notes = input("Reviewer notes (optional, press Enter to skip): ").strip()
        return "approve", notes

    if choice in ("r", "reject"):
        notes = input("Reason for rejection (optional): ").strip()
        return "reject", notes

    if choice in ("q", "quit"):
        return "quit", ""

    # Anything else (including "s") means skip.
    return "skip", ""


def run_review_queue(channel_key):
    """
    Work through every drafted video for a channel, one at a time.

    Shows each video's details, opens the script file path for the
    reviewer to check manually, then records their approve/reject
    decision in the database.

    Args:
        channel_key (str): The channel key, e.g. "news".
    """
    drafted = get_videos_by_status("drafted", channel=channel_key)

    if not drafted:
        print(f"No drafted videos waiting for review in the '{channel_key}' channel.")
        return

    print(f"\nReview queue — {len(drafted)} video(s) waiting for the '{channel_key}' channel.")
    print("Open the script path in your editor before approving.\n")

    approved_count = 0
    rejected_count = 0
    skipped_count = 0

    for video in drafted:
        _print_video_summary(video)
        action, notes = _prompt_decision(video["id"])

        if action == "approve":
            if approve_video(video["id"], notes=notes):
                print(f"  ✓ Approved (ID {video['id']})")
                approved_count += 1
            else:
                print(f"  ! Not changed (ID {video['id']}): a job is still working on it, or it was posted.")

        elif action == "reject":
            if reject_video(video["id"], notes=notes):
                print(f"  ✗ Rejected (ID {video['id']})")
                rejected_count += 1
            else:
                print(f"  ! Not changed (ID {video['id']}): a job is still working on it, or it was posted.")

        elif action == "quit":
            print("Exiting review queue.")
            break

        else:
            print(f"  → Skipped (ID {video['id']})")
            skipped_count += 1

    _print_divider()
    print(f"Review session done — "
          f"{approved_count} approved, "
          f"{rejected_count} rejected, "
          f"{skipped_count} skipped.")

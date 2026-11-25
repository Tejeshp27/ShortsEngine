# =============================================================
# src/clips/moment_benchmark.py — check the moment ranker against what
#                                 real audiences picked from OUR recordings
#
# The ranker (src/clips/ranking.py) proposes a pool of moments from each
# recorded hour; the best few go to the editor model. If a moment never
# reaches that shortlist, the editor can never choose it. This file
# measures how often the moments that real people picked from the very
# same streams reach the pool and the shortlist.
#
# Two kinds of "real picks" are used:
#   1. TikTok posts. Clip accounts posted moments from streams we also
#      recorded. Each post's auto-transcript is matched to our Whisper
#      transcript (shared three-word phrases), which tells us exactly
#      where in our recording the moment is, and how many views it got.
#      This is the outcome we care about most.
#   2. Twitch viewer clips. Viewers clip moments while watching. Each
#      clip's place in the broadcast (vod_offset) is turned into a time
#      in our recording. Twitch clippers like in-game hype more than
#      TikTok viewers do, so this is only a "something happened here" check.
#
# Everything runs offline on cached transcripts, sound tags and chat
# counts (raw video is deleted after clipping). It makes no paid calls.
#
# Run:
#   python -m src.clips.moment_benchmark --tiktok cache/tiktok_moment_watch.jsonl
#   python -m src.clips.moment_benchmark --tiktok ... --profile config/clip_ranking/v2.yaml
# =============================================================

import argparse
import json
import re
from pathlib import Path

from src.clips.live_recorder import parse_rfc3339
from src.storage.database import get_connection

ROOT = Path(__file__).resolve().parents[2]
TRANSCRIPTS = ROOT / "cache" / "clips" / "transcripts"
RECORDING_CACHE = ROOT / "cache" / "clips" / "recordings"
EVIDENCE = ROOT / "cache" / "clips" / "evidence"

# A phrase found more often than this across all recordings is too common
# ("you know what", "oh my god") to say where a moment is.
COMMON_PHRASE_LIMIT = 20

# A TikTok post counts as found in a recording when at least this many
# different three-word phrases match inside about two minutes.
MIN_SHARED_PHRASES = 6

# A candidate "contains" a picked moment when they overlap by this many seconds.
MIN_OVERLAP_SECONDS = 8

# Two posts within this many seconds of each other show the same moment.
SAME_MOMENT_SECONDS = 40


# ---------------------------------------------------------------
# Reading cached data
# ---------------------------------------------------------------

def read_jsonl(path):
    """
    Read a file with one JSON object per line (bad lines are skipped).

    Args:
        path (str or Path): The file.

    Returns:
        list[dict]: One dict per good line.
    """
    rows = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    return rows


def live_recordings():
    """
    List the recorded live chunks whose Whisper transcript is still cached.

    Returns:
        list[dict]: Recording rows (id, streamer, platform, fingerprint, duration,
                    engagement_json), each with its "words" added.
    """
    connection = get_connection()
    try:
        rows = connection.execute(
            "SELECT id, streamer, platform, fingerprint, duration, engagement_json "
            "FROM recordings WHERE source_kind='live' ORDER BY id").fetchall()
    finally:
        connection.close()
    recordings = []
    for row in rows:
        path = TRANSCRIPTS / f"recording_{row['fingerprint']}_small.en_en.json"
        if not path.is_file():
            continue
        recording = dict(row)
        recording["words"] = json.loads(path.read_text())
        recordings.append(recording)
    return recordings


def cached_sounds(recording):
    """
    Get a recording's per-second sound tags if they are still cached.

    Args:
        recording (dict): A recording row with "fingerprint".

    Returns:
        list[dict]: Per-second sound chances, or [] when they were not kept.
    """
    path = RECORDING_CACHE / f"{recording['fingerprint']}.audio_events.json"
    if path.is_file():
        return json.loads(path.read_text())
    return []


def cached_media(recording):
    """
    Get a recording's per-second loudness/motion if still cached.

    Args:
        recording (dict): A recording row with "fingerprint".

    Returns:
        dict: {"volume_db": [...], "motion": [...]}, or {} when not kept.
    """
    path = RECORDING_CACHE / f"{recording['fingerprint']}.signals.json"
    if path.is_file():
        return json.loads(path.read_text())
    return {}


# ---------------------------------------------------------------
# Finding a TikTok post inside our recordings
# ---------------------------------------------------------------

def plain_tokens(text):
    """
    Split text into lowercase words without punctuation.

    Args:
        text (str): Any text, e.g. a transcript line.

    Returns:
        list[str]: The words.

    Example:
        plain_tokens("Wait, WHAT?!")   # ["wait", "what"]
    """
    cleaned = re.sub(r"[^a-z0-9' ]", " ", str(text).lower())
    return cleaned.split()


def phrases_of(tokens):
    """
    List the three-word phrases in a list of words.

    Args:
        tokens (list[str]): Words in order.

    Returns:
        list[tuple]: Each run of three neighbouring words.

    Example:
        phrases_of(["a", "b", "c", "d"])   # [("a","b","c"), ("b","c","d")]
    """
    phrases = []
    for position in range(len(tokens) - 2):
        phrases.append((tokens[position], tokens[position + 1], tokens[position + 2]))
    return phrases


def build_phrase_index(recordings):
    """
    Note where every three-word phrase is spoken in every recording.

    Args:
        recordings (list[dict]): From live_recordings() (with "words").

    Returns:
        dict: phrase → list of (recording id, seconds into the recording).
    """
    index = {}
    for recording in recordings:
        tokens = []
        times = []
        for word in recording["words"]:
            for token in plain_tokens(word["word"]):
                tokens.append(token)
                times.append(word["start"])
        for position, phrase in enumerate(phrases_of(tokens)):
            index.setdefault(phrase, []).append((recording["id"], times[position]))
    return index


def game_dialogue_phrases(index, streamer_of):
    """
    Find phrases spoken on two or more different streamers' streams.

    They all played the same game (GTA 6), so the game's own lines show
    up everywhere and would match a post to the wrong streamer.

    Args:
        index (dict): From build_phrase_index().
        streamer_of (dict): recording id → streamer name.

    Returns:
        set: The shared phrases.
    """
    shared = set()
    for phrase, places in index.items():
        names = set()
        for recording_id, _ in places:
            names.add(streamer_of[recording_id])
        if len(names) > 1:
            shared.add(phrase)
    return shared


def locate_post(post_text, index, skip):
    """
    Find where a TikTok post's spoken words happen in our recordings.

    Every rare phrase of the post votes for the minute of the recording
    where it was spoken; the recording minute (plus the next one) with
    the most different phrases wins.

    Args:
        post_text (str): The post's auto-transcript.
        index (dict): From build_phrase_index().
        skip (set): Phrases to ignore (game dialogue).

    Returns:
        dict or None: {"recording_id", "start", "end", "shared"} or None when
                      fewer than MIN_SHARED_PHRASES phrases match.
    """
    phrases = set(phrases_of(plain_tokens(post_text)))
    votes = {}
    for phrase in phrases:
        places = index.get(phrase, [])
        if not places or len(places) > COMMON_PHRASE_LIMIT or phrase in skip:
            continue
        for recording_id, seconds in places:
            key = (recording_id, int(seconds // 60))
            votes.setdefault(key, {})[phrase] = seconds
    best = None
    for (recording_id, minute), found in votes.items():
        together = dict(found)
        together.update(votes.get((recording_id, minute + 1), {}))
        if best is None or len(together) > len(best[1]):
            best = ((recording_id, minute), together)
    if best is None or len(best[1]) < MIN_SHARED_PHRASES:
        return None
    times = list(best[1].values())
    return {"recording_id": best[0][0], "start": min(times), "end": max(times), "shared": len(best[1])}


def tiktok_moments(posts, recordings):
    """
    Turn TikTok posts into moments inside our recordings (one per moment).

    Several accounts often post the same moment; the most-viewed post
    stands for it.

    Args:
        posts (list[dict]): Collected posts: {"id", "tx" (transcript), "v" (views),
                            "d" (seconds), "a" (account), "desc"}.
        recordings (list[dict]): From live_recordings().

    Returns:
        list[dict]: {"recording_id", "start", "end", "views", "posts", "desc"}.
    """
    index = build_phrase_index(recordings)
    streamer_of = {}
    for recording in recordings:
        streamer_of[recording["id"]] = recording["streamer"]
    skip = game_dialogue_phrases(index, streamer_of)
    moments = {}
    seen = set()
    for post in posts:
        if not post.get("tx") or post.get("id") in seen:
            continue
        seen.add(post.get("id"))
        place = locate_post(post["tx"], index, skip)
        if place is None:
            continue
        # The matched words only cover the start of the post; it runs for its full length.
        length = min(float(post.get("d") or 30), 60.0)
        end = max(place["end"], place["start"] + length)
        key = (place["recording_id"], int(place["start"] // SAME_MOMENT_SECONDS))
        views = int(post.get("v") or 0)
        moment = moments.get(key)
        if moment is None:
            moments[key] = {"recording_id": place["recording_id"], "start": place["start"], "end": end,
                            "views": views, "posts": 1, "desc": post.get("desc", "")}
        else:
            moment["posts"] += 1
            if views > moment["views"]:
                moment.update({"start": place["start"], "end": end, "views": views, "desc": post.get("desc", "")})
    return list(moments.values())


# ---------------------------------------------------------------
# Twitch viewer clips (from the evidence files the recorder saves)
# ---------------------------------------------------------------

def stream_seconds_at(wall, clock):
    """
    Work out the recorded stream time at a clock time (see live_recorder.stream_time_at).

    Args:
        wall (float): A clock time.
        clock (list[dict]): {"wall", "stream"} samples sorted by "wall".

    Returns:
        float or None: Seconds into the recorded stream, or None if outside the log.
    """
    from src.clips.live_recorder import stream_time_at
    return stream_time_at(wall, clock)


def twitch_moments(evidence):
    """
    Turn one chunk's saved viewer clips into moments inside its recording.

    A clip made live covers the seconds just before its creation time.

    Args:
        evidence (dict): A file written by live_recorder.save_chunk_evidence().

    Returns:
        list[dict]: {"recording_id", "start", "end", "views", "posts"}.
    """
    moments = []
    for clip in evidence.get("viewer_clips", []):
        created = parse_rfc3339(clip["created_at"])
        length = float(clip.get("duration") or 30)
        start = stream_seconds_at(created - length, evidence["clock"])
        if start is None:
            continue
        start = start - evidence["stream_start"]
        if start < 0 or start + length > evidence["duration"]:
            continue
        moments.append({"recording_id": evidence["recording_id"], "start": start, "end": start + length,
                        "views": int(clip.get("view_count") or 0), "posts": 1, "desc": clip.get("title", "")})
    return moments


def evidence_moments():
    """
    Read every saved chunk evidence file and return the Twitch moments.

    Returns:
        list[dict]: Moments from all chunks (see twitch_moments()).
    """
    moments = []
    if not EVIDENCE.is_dir():
        return moments
    for path in sorted(EVIDENCE.glob("recording_*.json")):
        moments.extend(twitch_moments(json.loads(path.read_text())))
    return moments


# ---------------------------------------------------------------
# Scoring the ranker
# ---------------------------------------------------------------

def overlap_seconds(first, second):
    """
    How many seconds two {"start", "end"} windows share.

    Args:
        first (dict): A window.
        second (dict): Another window.

    Returns:
        float: Shared seconds (0 when they do not touch).
    """
    return max(0.0, min(first["end"], second["end"]) - max(first["start"], second["start"]))


def rank_in_pool(moment, pool):
    """
    Find the position of the first pool candidate that contains a moment.

    Args:
        moment (dict): A picked moment.
        pool (list[dict]): Candidates, best first.

    Returns:
        int: 1 for the best candidate, 2 for the next..., 0 when none contains it.
    """
    for position, candidate in enumerate(pool):
        if overlap_seconds(moment, candidate) >= MIN_OVERLAP_SECONDS:
            return position + 1
    return 0


def evaluate(moments, recordings, profile_path, use_chat=True, pool_size=10):
    """
    Run the ranker on each recording and check where the picked moments landed.

    Args:
        moments (list[dict]): Picked moments (TikTok or Twitch).
        recordings (list[dict]): From live_recordings().
        profile_path (str): The ranking profile to test.
        use_chat (bool): False = pretend there was no chat (like Kick/YouTube streams).
        pool_size (int): How many candidates the ranker proposes.

    Returns:
        list[dict]: Each moment with "pool_rank" added (0 = not found).
    """
    from src.clips.ranking import load_profile, propose_candidates
    from src.clips.settings import load_clip_settings
    from src.config import load_channel_config

    settings = load_clip_settings(load_channel_config("twitch_clips"))
    profile = load_profile(profile_path)
    by_id = {}
    for recording in recordings:
        by_id[recording["id"]] = recording
    pools = {}
    results = []
    for moment in moments:
        recording = by_id.get(moment["recording_id"])
        if recording is None:
            continue
        if recording["id"] not in pools:
            engagement = json.loads(recording["engagement_json"] or "[]") if use_chat else []
            pools[recording["id"]] = propose_candidates(
                recording["duration"], recording["words"], cached_media(recording), cached_sounds(recording),
                engagement, settings, profile, pool_size=pool_size)
        scored = dict(moment)
        scored["streamer"] = recording["streamer"]
        scored["pool_rank"] = rank_in_pool(moment, pools[recording["id"]])
        results.append(scored)
    return results


def summarize(results, shortlist_size=6):
    """
    Count how many picked moments reached the pool and the shortlist.

    Args:
        results (list[dict]): From evaluate().
        shortlist_size (int): How many candidates the editor reads.

    Returns:
        dict: {"moments", "in_pool", "in_shortlist", "views_in_shortlist_share"}.
    """
    in_pool = 0
    in_shortlist = 0
    all_views = 0
    shortlist_views = 0
    for result in results:
        all_views += result["views"]
        if result["pool_rank"]:
            in_pool += 1
        if 0 < result["pool_rank"] <= shortlist_size:
            in_shortlist += 1
            shortlist_views += result["views"]
    share = shortlist_views / all_views if all_views else 0.0
    return {"moments": len(results), "in_pool": in_pool, "in_shortlist": in_shortlist,
            "views_in_shortlist_share": round(share, 3)}


def print_report(title, results):
    """
    Print one benchmark's totals and its moments, most viewed first.

    Args:
        title (str): A heading.
        results (list[dict]): From evaluate().
    """
    totals = summarize(results)
    print(f"\n{title}: {totals['in_shortlist']}/{totals['moments']} picked moments reached the shortlist "
          f"({totals['in_pool']} in the pool); {totals['views_in_shortlist_share']:.0%} of their views")
    ordered = sorted(results, key=lambda result: result["views"], reverse=True)
    for result in ordered[:25]:
        found = f"#{result['pool_rank']}" if result["pool_rank"] else "missed"
        print(f"  {result['views']:>9,} views  {result['streamer']:<14} rec {result['recording_id']:<4} "
              f"{result['start']:7.0f}s  {found:<7} {result['desc'][:60]}")


def main():
    """Command line: score a ranking profile against real audience picks."""
    parser = argparse.ArgumentParser(description="Check the moment ranker against real audience picks.")
    parser.add_argument("--tiktok", help="Collected TikTok posts (.jsonl with id, tx, v, d, desc).")
    parser.add_argument("--profile", default="config/clip_ranking/v3.yaml", help="Ranking profile to test.")
    parser.add_argument("--no-chat", action="store_true", help="Ignore chat (like streams with no chat data).")
    args = parser.parse_args()
    recordings = live_recordings()
    if args.tiktok:
        moments = tiktok_moments(read_jsonl(args.tiktok), recordings)
        print_report("TikTok posts", evaluate(moments, recordings, args.profile, use_chat=not args.no_chat))
    twitch = evidence_moments()
    if twitch:
        print_report("Twitch viewer clips", evaluate(twitch, recordings, args.profile, use_chat=not args.no_chat))


if __name__ == "__main__":
    main()

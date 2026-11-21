# =============================================================
# src/clips/tiktok_posts.py — find which TikTok post is which clip
#
# Clips reach TikTok as inbox drafts; the owner posts them by hand in the
# app, so the public video's ID is never sent back to us. Without that ID
# the daily statistics job cannot read the post's views, and the packaging
# experiments (src/clips/packaging.py) could only learn from YouTube.
#
# This lists the connected account's own posted videos (TikTok Display API
# /v2/video/list/, scope video.list — read-only, the owner's own account)
# and matches each one to a clip by its caption: the owner pastes the
# clip's "Final caption", which starts with the clip's title. A match is
# saved in platform_posts, after which the existing daily collector
# (src/analytics/accounts.py) records its views/likes/comments/shares.
# Uncertain matches are skipped rather than guessed.
# =============================================================

import json
import re
from datetime import datetime, timezone

from src.storage.database import get_connection

LIST_FIELDS = "id,title,video_description,create_time"
FILLER = {"the", "a", "an", "and", "to", "of", "in", "on", "at", "for", "with", "is", "was", "his", "her",
          "he", "she", "it", "this", "that", "so", "bro", "got", "just", "be", "are", "you", "i", "my"}


def meaningful_words(text):
    """
    The words of a text that carry meaning (lower case, no hashtags/links/filler).

    Args:
        text (str): Any caption or title.

    Returns:
        set[str]: Words of 3+ letters.
    """
    cleaned = re.sub(r"#\w+|https?://\S+|@\w+", " ", text.lower())
    words = set()
    for word in re.findall(r"[a-z0-9']+", cleaned):
        if len(word) >= 3 and word not in FILLER:
            words.add(word)
    return words


def list_posted_videos(token, limit=100):
    """
    List the connected TikTok account's own posted videos (newest first).

    Args:
        token (str): The account's access token (needs video.list).
        limit (int): Most videos to read.

    Returns:
        list[dict]: {"id", "text", "created"} where created is a UTC datetime.
    """
    from src.dashboard.accounts import TIKTOK_BASE, api_json

    videos = []
    cursor = None
    while len(videos) < limit:
        body = {"max_count": 20}
        if cursor:
            body["cursor"] = cursor
        data = api_json("POST", TIKTOK_BASE + "/video/list/", token,
                        params={"fields": LIST_FIELDS}, json=body).get("data", {})
        for video in data.get("videos", []):
            text = (video.get("video_description") or "") + " " + (video.get("title") or "")
            videos.append({"id": str(video["id"]), "text": text.strip(),
                           "created": datetime.fromtimestamp(int(video.get("create_time", 0)), timezone.utc)})
        if not data.get("has_more") or not data.get("cursor"):
            break
        cursor = data["cursor"]
    return videos[:limit]


def clip_candidates(channel):
    """
    The channel's clip videos that have no TikTok post linked yet.

    Args:
        channel (str): The clips channel.

    Returns:
        list[dict]: {"video_id", "words", "created"} — words from the title,
                    caption text and what the clip is about.
    """
    connection = get_connection()
    try:
        rows = connection.execute("""
            SELECT v.id, v.story_title, v.created_at, c.metadata_json, c.decision_json, c.hook_title
            FROM videos v JOIN clips c ON c.video_id = v.id
            WHERE v.channel = ? AND v.status IN ('approved', 'posted')
              AND NOT EXISTS (SELECT 1 FROM platform_posts p WHERE p.video_id = v.id AND p.platform = 'tiktok')
        """, (channel,)).fetchall()
    finally:
        connection.close()
    candidates = []
    for row in rows:
        metadata = json.loads(row["metadata_json"] or "{}")
        decision = json.loads(row["decision_json"] or "{}")
        text = " ".join([row["story_title"] or "", row["hook_title"] or "", metadata.get("description", ""),
                         metadata.get("hook_text", ""), decision.get("selection", {}).get("what_happened", "")])
        try:
            created = datetime.fromisoformat(row["created_at"])
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            created = None
        candidates.append({"video_id": row["id"], "title_words": meaningful_words(row["story_title"] or ""),
                           "words": meaningful_words(text), "created": created})
    return candidates


def match_score(post, candidate):
    """
    How well a TikTok post's caption matches one clip (0 to 1).

    Uses the post's first line/sentence (where the pasted title sits): the share
    of its meaningful words that appear in the clip's texts. The post must be
    made after the clip existed.

    Args:
        post (dict): From list_posted_videos().
        candidate (dict): From clip_candidates().

    Returns:
        float: 0 to 1.
    """
    if candidate["created"] is not None and post["created"] < candidate["created"]:
        return 0.0
    first_line = re.split(r"\n|(?<=[.!?])\s", post["text"], maxsplit=1)[0]
    post_words = meaningful_words(first_line) or meaningful_words(post["text"])
    if len(post_words) < 2:
        return 0.0
    shared = post_words & candidate["words"]
    if len(shared) < 2:
        return 0.0
    return len(shared) / len(post_words)


def link_tiktok_posts(channel, token, minimum_score=0.6):
    """
    Link the account's posted TikToks to their clips (saved in platform_posts).

    Each post goes to the best-matching clip only when the match is clear
    (score >= minimum_score and clearly better than the runner-up).

    Args:
        channel (str): The clips channel.
        token (str): The TikTok account's access token.
        minimum_score (float): Lowest acceptable match.

    Returns:
        list[tuple]: (video_id, tiktok_post_id, score) for each new link.
    """
    connection = get_connection()
    try:
        known = set()
        for row in connection.execute("SELECT post_id FROM platform_posts WHERE platform = 'tiktok'"):
            known.add(str(row["post_id"]))
    finally:
        connection.close()
    candidates = clip_candidates(channel)
    links = []
    # Oldest post first, so a clip posted twice is linked to its first post.
    for post in sorted(list_posted_videos(token), key=post_time):
        if post["id"] in known:
            continue
        scored = []
        for candidate in candidates:
            scored.append((match_score(post, candidate), candidate))
        scored.sort(key=first_of_pair, reverse=True)
        if not scored or scored[0][0] < minimum_score:
            continue
        if len(scored) > 1 and scored[0][0] - scored[1][0] < 0.15:
            continue   # two clips match about equally well: do not guess
        best = scored[0][1]
        connection = get_connection()
        try:
            cursor = connection.execute("INSERT OR IGNORE INTO platform_posts VALUES (?, 'tiktok', ?, ?)",
                                        (best["video_id"], post["id"], post["created"].isoformat()))
            connection.commit()
        finally:
            connection.close()
        if cursor.rowcount:
            links.append((best["video_id"], post["id"], round(scored[0][0], 2)))
            candidates.remove(best)
    return links


def post_time(post):
    """Sorting key: when a post was made."""
    return post["created"]


def first_of_pair(pair):
    """Sorting key: the first item of a (score, candidate) pair."""
    return pair[0]

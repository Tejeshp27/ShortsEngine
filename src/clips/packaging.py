# =============================================================
# src/clips/packaging.py — keep experimenting with titles, hashtags and
#                          caption looks until the winners are clear
#
# The owner asked for every clip's packaging to keep changing until a
# combination that works is found. This is a "multi-armed bandit": each
# packaging choice (a "dimension") has a few options ("arms"):
#
#   title_emoji    none | one          — emoji at the end of the title
#   title_case     natural | lowercase — "Jason Got Banned" vs "jason got banned"
#   hashtags       minimal | game | people — streamer+platform / +game /
#                  +the other named people in the clip. ("broad" = +#funny #fyp
#                  still works for old records but is no longer tried: generic
#                  tags made no difference on TikTok, 48 vs 53 accounts, and were
#                  3-6x more common among YouTube flops.)
#   caption_style  punch | clean       — big 1-2 word captions vs sentence case
#   context_line   whole | opening     — context line all clip vs first 8 seconds
#   description    summary | question  — post caption tells / asks viewers
#
# How it learns (Thompson sampling, a standard bandit method):
#   1. For each published clip, its views per day (from the official YouTube
#      statistics already collected) are compared with the channel's median.
#      Above the median = a "success" for every option that clip used.
#   2. Every option starts with a few forced tries (explore), so nothing is
#      judged on one clip.
#   3. After that, each option's success rate is drawn at random from what is
#      known about it, and the highest draw wins. Options that keep winning get
#      picked more and more often; weak ones almost never — but never zero, so
#      the system can notice if tastes change.
#   4. An option is reported as the "winner" once it is at least 95% likely to
#      be the best (also estimated by drawing many times).
#
# What was actually posted counts: the title's emoji/case are read from the
# final title (the owner may edit it before posting).
# Free: only reads the local database. Every choice and its reason is saved
# in the clip's decision record.
# =============================================================

import json
import random
import re

from src.storage.database import get_connection

DEFAULT_DIMENSIONS = {
    "title_emoji": ["none", "one"],
    "title_case": ["natural", "lowercase"],
    "hashtags": ["minimal", "game", "people"],
    "caption_style": ["punch", "clean"],
    "context_line": ["whole", "opening"],
    "description": ["summary", "question"],
}

BROAD_HASHTAGS = ["#funny", "#fyp"]
DEFAULT_EMOJI = "😭"


# ---------------------------------------------------------------------------
# Reading what was posted and how it did
# ---------------------------------------------------------------------------

def has_emoji(text):
    """
    Check whether a text contains an emoji.

    Args:
        text (str): A title.

    Returns:
        bool: True when it has at least one emoji or similar symbol.
    """
    for character in text:
        code = ord(character)
        if code >= 0x1F300 or 0x2600 <= code <= 0x27BF:
            return True
    return False


def observed_title_options(title):
    """
    Read the title options a posted title actually used.

    Args:
        title (str): The title as posted.

    Returns:
        dict: {"title_emoji": "one"|"none", "title_case": "lowercase"|"natural"}.
    """
    letters = re.sub(r"[^A-Za-z]", "", title)
    lowercase = bool(letters) and letters == letters.lower()
    return {"title_emoji": "one" if has_emoji(title) else "none",
            "title_case": "lowercase" if lowercase else "natural"}


def published_outcomes(channel, minimum_age_hours=48):
    """
    Every published clip with its packaging and how it performed against the channel median.

    Posts younger than `minimum_age_hours` are left out: a few-hours-old post
    has hardly any views yet and would look like a failure.

    Args:
        channel (str): The clips channel.
        minimum_age_hours (float): Only posts at least this old (at their latest statistics).

    Returns:
        list[dict]: {"video_id", "options": {dimension: option}, "relative_views", "success"}.
    """
    from src.clips.feedback import add_relative_views, published_clips

    records = []
    for record in published_clips(channel):
        if record.get("age_hours", 0) >= minimum_age_hours:
            records.append(record)
    records = add_relative_views(records)
    connection = get_connection()
    try:
        titles = {}
        for row in connection.execute("SELECT v.id, v.story_title, c.decision_json FROM videos v "
                                      "JOIN clips c ON c.video_id = v.id WHERE v.channel = ?", (channel,)):
            titles[row["id"]] = (row["story_title"], json.loads(row["decision_json"] or "{}"))
    finally:
        connection.close()
    outcomes = []
    for record in records:
        title, decision = titles.get(record["video_id"], ("", {}))
        options = dict(decision.get("packaging", {}).get("options", {}))
        options.update(observed_title_options(title))
        outcomes.append({"video_id": record["video_id"], "options": options,
                         "relative_views": record["relative_views"],
                         "success": record["relative_views"] >= 1.0})
    return outcomes


def option_stats(outcomes, dimension, options):
    """
    Count tries and successes for each option of one dimension.

    Args:
        outcomes (list[dict]): From published_outcomes().
        dimension (str): e.g. "hashtags".
        options (list[str]): Its options.

    Returns:
        dict: option → {"tries": n, "successes": n}.
    """
    stats = {}
    for option in options:
        stats[option] = {"tries": 0, "successes": 0}
    for outcome in outcomes:
        option = outcome["options"].get(dimension)
        if option in stats:
            stats[option]["tries"] += 1
            if outcome["success"]:
                stats[option]["successes"] += 1
    return stats


# ---------------------------------------------------------------------------
# Choosing the next clip's packaging
# ---------------------------------------------------------------------------

def draw_rate(stats, generator):
    """
    Draw a plausible success rate for one option from what is known about it.

    Uses a Beta(successes + 1, failures + 1) distribution: with few tries the
    draw is very spread out (room to explore); with many it is close to the
    measured rate.

    Args:
        stats (dict): {"tries", "successes"}.
        generator (random.Random): The random number source.

    Returns:
        float: A rate between 0 and 1.
    """
    failures = stats["tries"] - stats["successes"]
    return generator.betavariate(stats["successes"] + 1, failures + 1)


def pick_option(stats, minimum_tries, generator):
    """
    Choose one option: explore the least-tried until each has `minimum_tries`, then Thompson sampling.

    Args:
        stats (dict): option → {"tries", "successes"}.
        minimum_tries (int): Forced tries per option before learning takes over.
        generator (random.Random): The random number source.

    Returns:
        tuple: (option, plain-English reason).
    """
    least = None
    for option, numbers in stats.items():
        if numbers["tries"] < minimum_tries and (least is None or numbers["tries"] < stats[least]["tries"]):
            least = option
    if least is not None:
        return least, f"exploring: tried {stats[least]['tries']} of {minimum_tries} times so far"
    best = None
    best_draw = -1.0
    for option, numbers in stats.items():
        value = draw_rate(numbers, generator)
        if value > best_draw:
            best, best_draw = option, value
    numbers = stats[best]
    return best, f"learning: {numbers['successes']}/{numbers['tries']} above-median so far"


def chance_best(stats, generator, draws=2000):
    """
    Estimate how likely each option is to be the best one.

    Args:
        stats (dict): option → {"tries", "successes"}.
        generator (random.Random): The random number source.
        draws (int): How many random rounds to run.

    Returns:
        dict: option → probability (0 to 1).
    """
    wins = {}
    for option in stats:
        wins[option] = 0
    for _ in range(draws):
        best = None
        best_draw = -1.0
        for option, numbers in stats.items():
            value = draw_rate(numbers, generator)
            if value > best_draw:
                best, best_draw = option, value
        wins[best] += 1
    result = {}
    for option, count in wins.items():
        result[option] = round(count / draws, 3)
    return result


def dimensions_for(settings):
    """
    The dimensions and options to test (from the channel YAML, else the defaults).

    Args:
        settings (ClipChannelSettings): Channel settings (packaging section).

    Returns:
        dict: dimension → list of options.
    """
    chosen = {}
    for dimension, options in DEFAULT_DIMENSIONS.items():
        configured = settings.packaging.dimensions.get(dimension)
        chosen[dimension] = list(configured) if configured else list(options)
    return chosen


def choose_packaging(channel, settings, generator=None, outcomes=None):
    """
    Pick the packaging for the next clip.

    Args:
        channel (str): The clips channel.
        settings (ClipChannelSettings): Channel settings.
        generator (random.Random or None): Random source (tests pass a seeded one).
        outcomes (list or None): Published outcomes (None = read them now).

    Returns:
        dict: {"options": {dimension: option}, "reasons": {dimension: text}, "published_used": n}.
    """
    if generator is None:
        generator = random.Random()
    if outcomes is None:
        outcomes = published_outcomes(channel)
    options = {}
    reasons = {}
    for dimension, choices in dimensions_for(settings).items():
        stats = option_stats(outcomes, dimension, choices)
        options[dimension], reasons[dimension] = pick_option(stats, settings.packaging.explore_min_tries, generator)
    return {"options": options, "reasons": reasons, "published_used": len(outcomes)}


# ---------------------------------------------------------------------------
# Applying the choice
# ---------------------------------------------------------------------------

def strip_emoji(text):
    """
    Remove emoji from a text.

    Args:
        text (str): A title.

    Returns:
        str: The title without emoji, tidy spaces.
    """
    kept = []
    for character in text:
        code = ord(character)
        if code >= 0x1F300 or 0x2600 <= code <= 0x27BF or code in (0xFE0F, 0x200D):
            continue
        kept.append(character)
    return " ".join("".join(kept).split())


def style_title(title, options):
    """
    Apply the chosen emoji and casing to a title.

    Args:
        title (str): The chosen title.
        options (dict): Packaging options.

    Returns:
        str: The styled title.

    Example:
        style_title("Jason Got Banned", {"title_emoji": "one", "title_case": "lowercase"})
        → "jason got banned 😭"
    """
    styled = title
    if options.get("title_emoji") == "none":
        styled = strip_emoji(styled)
    elif options.get("title_emoji") == "one" and not has_emoji(styled):
        styled = styled.rstrip() + " " + DEFAULT_EMOJI
    if options.get("title_case") == "lowercase":
        styled = styled.lower()
    return styled


def people_hashtags(who, username, display_name=""):
    """
    Turn the editor's "who" text into hashtags for the other named people.

    Only words that look like names (start with a capital letter) are used, so
    roles like "his friend" or "chat" are skipped. The streamer is left out
    because they are tagged already.

    Args:
        who (str): The editor model's "who" field, e.g. "Kai Cenat, Druski, his friend".
        username (str): The streamer's login (left out).
        display_name (str): The streamer's display name (also left out).

    Returns:
        list[str]: e.g. ["#druski"].

    Example:
        people_hashtags("Kai Cenat and Druski", "kaicenat", "Kai Cenat") → ["#druski"]
    """
    skip = {username.lower(), re.sub(r"[^a-z0-9]", "", display_name.lower())}
    tags = []
    # The editor separates people with commas, "and" or "&".
    for part in re.split(r",|&|\band\b|/", who or ""):
        words = part.split()
        name_words = []
        for word in words:
            if word[:1].isupper():
                name_words.append(word)
        tag = re.sub(r"[^a-z0-9]", "", "".join(name_words).lower())
        if 3 <= len(tag) <= 20 and tag not in skip and tag not in ("chat", "streamer"):
            tags.append("#" + tag)
    return tags


def styled_hashtags(username, platform, game, option, maximum, who="", display_name=""):
    """
    Build the hashtag set for the chosen hashtag option.

    Args:
        username (str): Streamer login.
        platform (str): "twitch", "kick" or "youtube".
        game (str): Game name or "".
        option (str): "minimal", "game", "broad" or "people".
        maximum (int): Most hashtags.
        who (str): The editor's "who" field (used by "people").
        display_name (str): The streamer's display name (used by "people").

    Returns:
        list[str]: The hashtags.
    """
    tags = ["#" + username.lower(), "#" + platform.lower()]
    # "people" puts the other named people first: on TikTok, each account's
    # breakouts carried more specific name/topic hashtags than its flops
    # (88 vs 35 accounts; docs/TIKTOK_CONTENT_RESEARCH.md section 2.4e).
    if option == "people":
        tags = ["#" + username.lower()] + people_hashtags(who, username, display_name) + ["#" + platform.lower()]
    game_tag = re.sub(r"[^a-z0-9]", "", game.lower())
    if option in ("game", "broad") and 2 <= len(game_tag) <= 20:
        tags.append("#" + game_tag)
    if option == "broad":
        tags.extend(BROAD_HASHTAGS)
    unique = []
    for tag in tags:
        if tag not in unique:
            unique.append(tag)
    return unique[:maximum]


def apply_packaging(metadata, titles, review, streamer, settings, packaging):
    """
    Change a clip's texts to match its packaging choice.

    Args:
        metadata (dict): The clip's metadata (youtube_title, description, hashtags...).
        titles (dict): From titles.choose_titles() (for the original texts).
        review (dict): The editor model's review (game, question idea).
        streamer (ApprovedStreamer): The streamer.
        settings (ClipChannelSettings): Channel settings.
        packaging (dict): From choose_packaging().

    Returns:
        dict: The updated metadata (also recorded under "packaging").
    """
    options = packaging["options"]
    updated = dict(metadata)
    updated["youtube_title"] = style_title(metadata["youtube_title"], options)
    updated["hashtags"] = styled_hashtags(streamer.username, streamer.platform, (review or {}).get("game", ""),
                                          options.get("hashtags", "minimal"), settings.titles.max_hashtags,
                                          who=(review or {}).get("who", ""),
                                          display_name=getattr(streamer, "display_name", "") or "")
    question = ((review or {}).get("titles") or {}).get("question", "")
    if options.get("description") == "question" and question:
        updated["description"] = question
    updated["packaging"] = options
    return updated


def render_overrides(settings, options):
    """
    Settings for rendering one clip with its packaging (caption style, context line).

    Args:
        settings (ClipChannelSettings): Channel settings.
        options (dict): Packaging options (may be empty).

    Returns:
        ClipChannelSettings: A copy with this clip's caption style and context-line time.
    """
    captions = settings.captions
    hook = settings.hook_title
    if options.get("caption_style") in ("punch", "clean", "bold", "boxed"):
        words = 2 if options["caption_style"] == "punch" else 3
        captions = captions.model_copy(update={"style": options["caption_style"], "words_per_caption": words})
    if options.get("context_line") == "whole":
        hook = hook.model_copy(update={"seconds": 0})
    elif options.get("context_line") == "opening":
        hook = hook.model_copy(update={"seconds": 8.0})
    return settings.model_copy(update={"captions": captions, "hook_title": hook})


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def packaging_report(channel, settings, generator=None):
    """
    Show what has been learned so far for every packaging dimension.

    Args:
        channel (str): The clips channel.
        settings (ClipChannelSettings): Channel settings.
        generator (random.Random or None): Random source.

    Returns:
        dict: dimension → {"stats", "chance_best", "winner" (or None)}, plus "published".
    """
    if generator is None:
        generator = random.Random(1)
    outcomes = published_outcomes(channel)
    report = {"published": len(outcomes), "dimensions": {}}
    for dimension, choices in dimensions_for(settings).items():
        stats = option_stats(outcomes, dimension, choices)
        chances = chance_best(stats, generator)
        winner = None
        for option, chance in chances.items():
            enough = stats[option]["tries"] >= settings.packaging.explore_min_tries
            if chance >= settings.packaging.winner_confidence and enough:
                winner = option
        report["dimensions"][dimension] = {"stats": stats, "chance_best": chances, "winner": winner}
    return report

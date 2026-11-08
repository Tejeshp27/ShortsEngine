# =============================================================
# src/generation/ranking.py — pick the best story to cover
#
# Sends the list of headlines to the cheap ranking model (e.g.
# Claude Haiku) and reads back which story numbers it chose.
# The model's answer is checked carefully: if it's unreadable,
# we fall back to the newest story instead of crashing.
# =============================================================

import re

from src.generation.prompts import build_ranking_system_prompt, build_ranking_user_message
from src.analytics.feedback import load_feedback_for_channel


def parse_ranking_answer(answer_text, number_of_stories):
    """
    Read the story numbers out of the ranking model's answer.

    Args:
        answer_text (str): The model's reply, e.g. "4, 1, 7\\nBecause...".
        number_of_stories (int): How many stories were in the list,
                                 so we can ignore numbers out of range.

    Returns:
        list[int]: Valid story numbers (counting from 1), best first,
                   without repeats. Empty if nothing valid was found.

    Example:
        parse_ranking_answer("3, 1\\nBig news.", 5)   # -> [3, 1]
    """
    lines = answer_text.strip().splitlines()
    if not lines:
        return []

    # Only look at the first line, which should hold the numbers.
    # re.findall(r"\d+", ...) finds every run of digits, e.g. "4, 1" -> ["4", "1"].
    numbers_found = re.findall(r"\d+", lines[0])

    chosen = []
    for text_number in numbers_found:
        number = int(text_number)
        if 1 <= number <= number_of_stories and number not in chosen:
            chosen.append(number)
    return chosen


def parse_ranking_reason(answer_text):
    """
    Get the one-sentence reason from the ranking model's answer.

    Args:
        answer_text (str): The model's reply.

    Returns:
        str: The second line of the answer, or "" if there isn't one.
    """
    lines = answer_text.strip().splitlines()
    if len(lines) < 2:
        return ""
    return lines[1].strip()


def rank_stories(stories, config, llm, channel_key=None):
    """
    Ask the cheap model which stories are worth covering.

    Args:
        stories (list[dict]): Candidate stories (already deduplicated).
        config (dict): The channel config.
        llm (LLMClient): The run's AI client (handles cost and caching).
        channel_key (str or None): Channel name used to load feedback data.
                                   If None, the feedback loop is skipped.

    Returns:
        tuple: (list of chosen stories best first, reason text).
               If the answer can't be read, the newest story is returned.
    """
    # Only send the first N headlines, to keep the request small.
    max_headlines = config["selection"]["max_headlines_to_rank"]
    candidates = stories[:max_headlines]

    # Phase 6.3: inject past performance data into the ranking prompt
    # if a feedback file has been saved by src/analytics/feedback.py.
    feedback_text = load_feedback_for_channel(channel_key) if channel_key else ""

    answer = llm.ask(
        model=config["models"]["ranking"],
        system_prompt=build_ranking_system_prompt(config, feedback_text=feedback_text),
        user_message=build_ranking_user_message(candidates),
        max_tokens=config["llm"]["ranking_max_tokens"],
        effort=config["llm"].get("ranking_effort"),
        purpose="ranking",
    )

    chosen_numbers = parse_ranking_answer(answer, len(candidates))

    if not chosen_numbers:
        # In --dry-run the answer is a placeholder, so this is expected there.
        print("  Could not read the ranking answer. Using the newest story instead.")
        return [candidates[0]], "fallback: newest story"

    chosen_stories = []
    for number in chosen_numbers:
        # Story numbers start at 1, but Python lists start at 0.
        chosen_stories.append(candidates[number - 1])

    return chosen_stories, parse_ranking_reason(answer)

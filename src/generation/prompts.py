# =============================================================
# src/generation/prompts.py — the instructions we give the AI
#
# All prompt text lives here so it's easy to read and improve in
# one place. The prompts are filled in from the channel config,
# so nothing about a specific channel (tone, audience, length) is
# hard-coded — a new channel gets its own prompts automatically.
# =============================================================

# People speak at roughly 150 words per minute, i.e. 2.5 words per
# second. We use this to turn "75 seconds" into a word target.
WORDS_PER_SECOND = 2.5


def target_word_count(target_seconds):
    """
    Turn a video length in seconds into about how many words to write.

    Args:
        target_seconds (int): The channel's target video length.

    Returns:
        int: The approximate number of spoken words.

    Example:
        target_word_count(60)   # -> 150
    """
    return int(target_seconds * WORDS_PER_SECOND)


def build_ranking_system_prompt(config, feedback_text=""):
    """
    Write the standing instructions for the cheap "story picker" model.

    Args:
        config (dict): The channel config (uses niche and audience).
        feedback_text (str): Optional past-performance data from collector.py.
                             If provided, it's appended so the model knows
                             which story types have done well before.

    Returns:
        str: The system prompt text.
    """
    base = (
        f"You choose stories for a short-form video channel about {config['niche']}.\n"
        f"The audience is: {config['audience']}.\n\n"
        "Pick the stories that are most important and most interesting to this "
        "audience, and that can be explained clearly in about a minute. Prefer "
        "real news with clear consequences over minor updates, opinion pieces or "
        "live blogs.\n\n"
        "Reply in exactly this format and nothing else:\n"
        "Line 1: up to 3 story numbers, best first, separated by commas (e.g. 4, 1, 7)\n"
        "Line 2: one short sentence explaining why the first pick is best."
    )
    if feedback_text:
        base += f"\n\n{feedback_text}"
    return base


def build_ranking_user_message(stories):
    """
    List the headlines for the story picker, numbered from 1.

    Args:
        stories (list[dict]): Stories with 'title', 'source' and 'summary'.

    Returns:
        str: A numbered list, one story per line with a short summary.
    """
    lines = []
    for number, story in enumerate(stories, start=1):
        # A short slice of the summary helps the model without costing much.
        short_summary = story.get("summary", "")[:200]
        lines.append(f"{number}. {story['title']} ({story['source']}) — {short_summary}")
    return "\n".join(lines)


def build_writing_system_prompt(config):
    """
    Write the standing instructions for the script-writing model.

    This prompt is the same for every story on a channel, which is
    why the LLM client marks it for prompt caching.

    Args:
        config (dict): The channel config (niche, audience, tone, length).

    Returns:
        str: The system prompt text.
    """
    seconds = config["target_length_seconds"]
    words = target_word_count(seconds)

    return (
        f"You write scripts for \"{config['name']}\", a short-form video channel "
        f"about {config['niche']}.\n"
        f"Audience: {config['audience']}.\n"
        f"Tone: {config['tone']}.\n"
        f"Length: the spoken script should take about {seconds} seconds, "
        f"which is roughly {words} words in total.\n\n"
        "ACCURACY RULES (most important):\n"
        "- Use ONLY facts stated in the source article provided. Do not add facts "
        "from memory.\n"
        "- Never invent quotes, numbers, names, dates or events.\n"
        "- If something important is unclear or missing from the source, do not "
        "guess. Mention it under 'Notes for reviewer' instead.\n\n"
        "FAIRNESS RULES:\n"
        "- Explain what happened, why it matters and what each major side says, "
        "if the source gives their views.\n"
        "- No opinions, no loaded or emotional language, and never tell viewers "
        "what to think.\n"
        "- Explain any jargon in plain words.\n\n"
        "FORMAT: Reply with exactly these Markdown sections and nothing else:\n"
        "## Hook\n"
        "(One or two sentences for the first 3 seconds that make people keep watching.)\n"
        "## Story\n"
        "(What happened, in plain language.)\n"
        "## Why it matters\n"
        "(Why the audience should care.)\n"
        "## What happens next\n"
        "(Upcoming steps, only if the source mentions them; otherwise say what is "
        "still unknown.)\n"
        "## Closing line\n"
        "(One short sentence to end the video.)\n"
        "## Notes for reviewer\n"
        "(Bullet points: anything unclear, any claim you were unsure about, and "
        "anything the reviewer should double-check. Write 'None' if there is nothing.)\n\n"
        "The first five sections are spoken aloud, so write them as natural speech: "
        "no bullet points, no stage directions, no emojis."
    )


def build_writing_user_message(story, article_text):
    """
    Give the writing model the story and its source article.

    Args:
        story (dict): The chosen story ('title', 'source', 'url').
        article_text (str): Plain text of the article (already trimmed).

    Returns:
        str: The message containing the source material.
    """
    return (
        "Write a script about this story using only the source below.\n\n"
        f"Headline: {story['title']}\n"
        f"Source: {story['source']}\n"
        f"URL: {story['url']}\n\n"
        "<source_article>\n"
        f"{article_text}\n"
        "</source_article>"
    )

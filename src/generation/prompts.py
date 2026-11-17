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


# The script layout used when a channel's YAML has no "style.sections"
# list. These are general news-script parts, not specific to one channel.
DEFAULT_SECTIONS = [
    {"name": "Hook", "guide": "One or two sentences for the first 3 seconds that make people keep watching."},
    {"name": "Story", "guide": "What happened, in plain language."},
    {"name": "Why it matters", "guide": "Why the audience should care."},
    {"name": "What happens next", "guide": "Upcoming steps, only if the source mentions them; "
                                           "otherwise say what is still unknown."},
    {"name": "Closing line", "guide": "One short sentence to end the video."},
]

# What the reviewer-only notes must contain, for every channel.
NOTES_GUIDE = (
    "Bullet points for the human fact-checker. The first bullet starts with "
    "'Analogies:' and copies every analogy in the script word for word, with "
    "one short reason it is fair and accurate (or says 'Analogies: none'). Then "
    "list anything unclear, any claim you were unsure about, and anything to "
    "double-check."
)


def script_sections(config):
    """
    Get the list of spoken sections a channel's scripts must have.

    Args:
        config (dict): The channel config. Uses style.sections if present.

    Returns:
        list[dict]: One {"name", "guide", "max_words"} per section, in
                    speaking order (max_words is None when not set).
                    Any "{slogan}" in a guide is replaced with the
                    channel's slogan.

    Example:
        script_sections(config)[0]["name"]   # → "Hook"
    """
    style = config.get("style") or {}
    sections = style.get("sections") or DEFAULT_SECTIONS
    slogan = config.get("slogan", "")

    filled = []
    for section in sections:
        guide = section.get("guide", "").replace("{slogan}", slogan)
        if section.get("max_words"):
            guide += f" At most {section['max_words']} words."
        filled.append({"name": section["name"], "guide": guide,
                       "max_words": section.get("max_words")})
    return filled


def section_names(config):
    """
    List just the names of a channel's spoken sections.

    Args:
        config (dict): The channel config.

    Returns:
        list[str]: e.g. ["Hook", "What happened", ..., "Closing line"].
    """
    names = []
    for section in script_sections(config):
        names.append(section["name"])
    return names


def bullet_lines(items):
    """
    Turn a list of rules into "- rule" lines for a prompt.

    Args:
        items (list[str] or None): The rules.

    Returns:
        str: One rule per line, each starting with "- ".
    """
    lines = []
    for item in items or []:
        lines.append(f"- {item}")
    return "\n".join(lines)


def build_style_rules(config):
    """
    Write the channel's style rules (hook, language, analogies) as prompt text.

    Every rule comes from the channel YAML's "style" block, so another
    channel can have a completely different voice without code changes.

    Args:
        config (dict): The channel config.

    Returns:
        str: The rules as text, or "" if the channel has no style block.
    """
    style = config.get("style")
    if not style:
        return ""

    parts = []
    hook = style.get("hook", {})
    if hook:
        hook_text = "HOOK RULES:\n"
        hook_text += bullet_lines(hook.get("rules"))
        if hook.get("types"):
            hook_text += "\n- Use one of these hook types: " + "; ".join(hook["types"]) + "."
        if hook.get("banned_openers"):
            quoted = []
            for opener in hook["banned_openers"]:
                quoted.append(f'"{opener}"')
            hook_text += "\n- Never start the script with " + ", ".join(quoted) + "."
        parts.append(hook_text)

    if style.get("language"):
        parts.append("LANGUAGE RULES:\n" + bullet_lines(style["language"]))
    if style.get("analogies"):
        parts.append("ANALOGY RULES:\n" + bullet_lines(style["analogies"]))
    if style.get("audience_rules"):
        parts.append("AUDIENCE RULES:\n" + bullet_lines(style["audience_rules"]))
    return "\n\n".join(parts) + "\n\n"


def build_format_rules(config):
    """
    Describe the exact Markdown sections the script must have.

    Args:
        config (dict): The channel config.

    Returns:
        str: The FORMAT part of the writing prompt.
    """
    lines = ["FORMAT: Reply with exactly these Markdown sections, in this order, and nothing else:"]
    for section in script_sections(config):
        lines.append(f"## {section['name']}")
        lines.append(f"({section['guide']})")
    lines.append("## Notes for reviewer")
    lines.append(f"({NOTES_GUIDE})")
    lines.append("")
    lines.append(
        "Every section except 'Notes for reviewer' is spoken aloud, so write "
        "them as natural speech: no bullet points, no stage directions, no emojis, "
        "and no section names inside the text."
    )
    return "\n".join(lines)


def build_writing_system_prompt(config):
    """
    Write the standing instructions for the script-writing model.

    This prompt is the same for every story on a channel, which is
    why the LLM client marks it for prompt caching.

    Args:
        config (dict): The channel config (niche, audience, tone, length,
                       and optionally slogan + style rules).

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
        f"which is roughly {words} words in total. Do not go over {words + 10} words.\n\n"
        "ACCURACY RULES (most important, they override every other rule):\n"
        "- Use ONLY facts stated in the source article provided. Do not add facts "
        "from memory.\n"
        "- Never invent quotes, numbers, names, dates or events.\n"
        "- If something important is unclear or missing from the source, do not "
        "guess. Mention it under 'Notes for reviewer' instead.\n\n"
        "FAIRNESS RULES:\n"
        "- Explain what happened, why it matters and what each major side says, "
        "if the source gives their views. If the source gives only one side, say "
        "so plainly instead of inventing the other.\n"
        "- No opinions, no loaded or emotional language, and never tell viewers "
        "what to think.\n"
        "- Explain any jargon in plain words.\n\n"
        + build_style_rules(config)
        + build_format_rules(config)
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

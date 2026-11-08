# =============================================================
# src/generation/writer.py — write the script and save it to disk
#
# Takes the chosen story and its article text, asks the writing
# model (e.g. Claude Sonnet) for a script, then saves it as a
# Markdown file:  output/<channel>/<date>-<slug>.md
#
# The "Sources" list at the bottom is added by THIS code, not by
# the AI, so the links you fact-check are always the real ones.
# =============================================================

import os
import re
from datetime import date

from src.generation.prompts import (
    WORDS_PER_SECOND,
    build_writing_system_prompt,
    build_writing_user_message,
)

# These sections are read aloud, so only they count toward the video length.
SPOKEN_SECTIONS = ["Hook", "Story", "Why it matters", "What happens next", "Closing line"]


def slugify(text, max_length=60):
    """
    Turn a headline into a short, file-name-safe "slug".

    Args:
        text (str): Any text, usually the headline.
        max_length (int): The longest the slug may be.

    Returns:
        str: Lowercase words joined by dashes.

    Example:
        slugify("Senate Passes $1B Bill!")   # -> "senate-passes-1b-bill"
    """
    lowered = text.lower()
    # Replace every run of characters that isn't a letter or digit with one dash.
    dashed = re.sub(r"[^a-z0-9]+", "-", lowered)
    trimmed = dashed.strip("-")[:max_length]
    # Cutting at max_length can leave a dash at the end, so strip again.
    trimmed = trimmed.strip("-")
    if not trimmed:
        return "story"
    return trimmed


def extract_sections(script_text):
    """
    Split the model's Markdown script into its named sections.

    Args:
        script_text (str): Text containing headings like "## Hook".

    Returns:
        dict: Section name -> section text, e.g. {"Hook": "Ever wondered..."}.
    """
    sections = {}
    current_name = None
    current_lines = []

    for line in script_text.splitlines():
        if line.startswith("## "):
            # Save the section we were reading before starting a new one.
            if current_name is not None:
                sections[current_name] = "\n".join(current_lines).strip()
            current_name = line[3:].strip()
            current_lines = []
        elif current_name is not None:
            current_lines.append(line)

    if current_name is not None:
        sections[current_name] = "\n".join(current_lines).strip()

    return sections


def count_spoken_words(script_text):
    """
    Count the words that will actually be spoken in the video.

    Args:
        script_text (str): The full script from the model.

    Returns:
        int: Number of words in the spoken sections only
             (the reviewer notes are not read aloud).
    """
    sections = extract_sections(script_text)
    total = 0
    for name in SPOKEN_SECTIONS:
        total += len(sections.get(name, "").split())
    return total


def find_missing_sections(script_text):
    """
    List the required spoken sections that the model forgot.

    Args:
        script_text (str): The full script from the model.

    Returns:
        list[str]: Names of missing sections (empty if all are present).
    """
    sections = extract_sections(script_text)
    missing = []
    for name in SPOKEN_SECTIONS:
        if name not in sections:
            missing.append(name)
    return missing


def build_script_file(story, script_text, config, writing_model, run_date, dry_run=False):
    """
    Put together the full Markdown file: details, script, and sources.

    Args:
        story (dict): The chosen story ('title', 'url', 'source').
        script_text (str): The script written by the model.
        config (dict): The channel config.
        writing_model (str): The model that wrote the script.
        run_date (str): Today's date as "YYYY-MM-DD".
        dry_run (bool): If True, mark the file as a dry-run placeholder.

    Returns:
        str: The complete file contents.
    """
    spoken_words = count_spoken_words(script_text)
    estimated_seconds = round(spoken_words / WORDS_PER_SECOND)

    lines = []
    lines.append(f"# {story['title']}")
    lines.append("")
    if dry_run:
        lines.append("> **DRY RUN** — this is a placeholder. No API call was made.")
        lines.append("")
    lines.append(f"- **Channel:** {config['name']}")
    lines.append(f"- **Date:** {run_date}")
    lines.append(f"- **Status:** draft — needs human review before use")
    lines.append(f"- **Written by:** {writing_model}")
    lines.append(f"- **Spoken words:** {spoken_words} "
                 f"(~{estimated_seconds}s; target {config['target_length_seconds']}s)")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append(script_text.strip())
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## Sources")
    lines.append("")
    lines.append(f"- [{story['title']}]({story['url']}) — {story['source']}")
    lines.append("")
    return "\n".join(lines)


def choose_output_path(channel_key, run_date, title):
    """
    Decide where to save a script, without overwriting an existing file.

    Args:
        channel_key (str): The channel's file name, e.g. "news".
        run_date (str): "YYYY-MM-DD".
        title (str): The story headline (turned into a slug).

    Returns:
        str: A path like "output/news/2026-11-08-senate-passes-bill.md".
             If that exists, "-2", "-3"... is added before ".md".
    """
    folder = os.path.join("output", channel_key)
    base_name = f"{run_date}-{slugify(title)}"
    path = os.path.join(folder, f"{base_name}.md")

    counter = 2
    while os.path.exists(path):
        path = os.path.join(folder, f"{base_name}-{counter}.md")
        counter += 1
    return path


def write_script(story, article_text, config, channel_key, llm):
    """
    Ask the writing model for a script and save it to output/<channel>/.

    Args:
        story (dict): The chosen story.
        article_text (str): The article text to base the script on.
        config (dict): The channel config.
        channel_key (str): The channel's file name, e.g. "news".
        llm (LLMClient): The run's AI client.

    Returns:
        str: The path of the saved script file.
    """
    writing_model = config["models"]["writing"]

    script_text = llm.ask(
        model=writing_model,
        system_prompt=build_writing_system_prompt(config),
        user_message=build_writing_user_message(story, article_text),
        max_tokens=config["llm"]["writing_max_tokens"],
        effort=config["llm"].get("writing_effort"),
        purpose="writing",
    )

    if not llm.dry_run:
        missing = find_missing_sections(script_text)
        if missing:
            print(f"  WARNING: The script is missing these sections: {', '.join(missing)}")

    run_date = date.today().isoformat()
    file_text = build_script_file(story, script_text, config, writing_model,
                                  run_date, dry_run=llm.dry_run)

    output_path = choose_output_path(channel_key, run_date, story["title"])
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(file_text)

    return output_path

# =============================================================
# src/generation/sections.py — which parts of a script are spoken
#
# A script is a Markdown file split into "## " sections. Most of
# them (Hook, What happened, ...) are read aloud in the video. A few
# are only for the human reviewer and must never be spoken.
#
# Each channel chooses its own spoken sections in its YAML file, so
# instead of listing the spoken ones here, we list the few that are
# NEVER spoken. Everything else counts as narration. This way old
# scripts (Hook, Story, ...) and new ones (Hook, What happened, ...)
# both work without any change.
#
# Used by: writer.py (word counts), tts.py (voiceover text) and
# animation/storyboard.py (which words appear in the video).
# =============================================================

# Sections that exist only for the reviewer. Never read aloud.
NOT_SPOKEN_SECTIONS = ["Notes for reviewer", "Sources", "Quality check"]


def is_spoken_section(section_name):
    """
    Check whether a script section is read aloud in the video.

    Args:
        section_name (str): The heading text after "## ", e.g. "Hook".

    Returns:
        bool: True for narration, False for reviewer-only sections.

    Example:
        is_spoken_section("Why it matters")       # → True
        is_spoken_section("Notes for reviewer")   # → False
    """
    return section_name.strip() not in NOT_SPOKEN_SECTIONS

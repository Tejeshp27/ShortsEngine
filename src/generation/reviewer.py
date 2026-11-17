# =============================================================
# src/generation/reviewer.py — automatic quality check for scripts
#
# After the writing model drafts a script, a cheap model (e.g.
# Claude Haiku) reads it next to the source article and scores it
# from 1 to 10 on four things:
#
#   hook       — would the first 2 seconds make someone keep watching?
#   clarity    — could a busy 14-year-old follow it?
#   neutrality — does it avoid taking sides or loaded words?
#   accuracy   — is every fact in the script found in the source?
#
# A few rules are also checked by plain code, for free (for example
# "the hook must not start with 'Today'").
#
# If any score is below the channel's min_score, or a code check
# fails, the writing model rewrites the script ONCE with the list of
# problems. The new script is scored again, and everything is logged
# in a "## Quality check" section of the script file (never spoken).
#
# How it fits in:
#   writer.py → write_script() → review_and_improve() (here)
# =============================================================

import json
import re

from src.generation.prompts import (
    build_writing_system_prompt,
    build_writing_user_message,
    script_sections,
    section_names,
    target_word_count,
)

# The four things every script is scored on, in report order.
SCORE_NAMES = ["hook", "clarity", "neutrality", "accuracy"]

# Used when the channel YAML does not set quality_check.min_score.
DEFAULT_MIN_SCORE = 7

# A script may run this much over its word target before it must be cut
# (0.10 = 10%). Models often ignore word limits, so code checks it.
LENGTH_TOLERANCE = 0.10

REVIEW_SYSTEM_PROMPT = """You are a strict editor checking a short news video script before it is published.

You get the SOURCE article and the SCRIPT. Score the script from 1 (very bad) to 10 (excellent) on:
- hook: Would the first sentence make a scrolling viewer stop within 2 seconds? Weak openers like "Today..." or a vague summary score 4 or less.
- clarity: Could someone who never follows the news understand every sentence? Short sentences, plain words, every term explained.
- neutrality: No opinions, no loaded or emotional words, no taking sides, each side described fairly. Analogies must not favor a side.
- accuracy: Every fact, number, name and quote in the script must appear in the SOURCE. Any added fact scores 4 or less.

Be specific in "problems": quote the exact words that need fixing and say how to fix them.

Reply with ONLY this JSON object and nothing else:
{"hook": 0, "clarity": 0, "neutrality": 0, "accuracy": 0, "problems": ["..."]}"""


def build_review_message(script_text, article_text):
    """
    Put the source article and the script into one message for the checker.

    Args:
        script_text (str): The script's sections (spoken + notes).
        article_text (str): The source article the script must stick to.

    Returns:
        str: The message text.
    """
    return (
        "<source>\n"
        f"{article_text}\n"
        "</source>\n\n"
        "<script>\n"
        f"{script_text}\n"
        "</script>"
    )


def parse_review_reply(reply_text):
    """
    Read the checker's JSON answer into a dict of scores.

    Args:
        reply_text (str): The model's reply, ideally one JSON object.

    Returns:
        dict or None: {"hook": int, "clarity": int, "neutrality": int,
                       "accuracy": int, "problems": [str, ...]},
                      or None if the reply could not be read.

    Example:
        parse_review_reply('{"hook": 8, "clarity": 9, "neutrality": 10, '
                           '"accuracy": 9, "problems": []}')["hook"]   # → 8
    """
    # Models sometimes wrap JSON in extra text, so find the {...} part.
    match = re.search(r"\{.*\}", reply_text, re.DOTALL)
    if match is None:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None

    scores = {}
    for name in SCORE_NAMES:
        value = data.get(name)
        if not isinstance(value, (int, float)):
            return None
        # Keep every score inside 1-10 even if the model wrote 0 or 11.
        scores[name] = max(1, min(10, int(value)))

    problems = []
    for problem in data.get("problems", []) or []:
        if isinstance(problem, str) and problem.strip():
            problems.append(problem.strip())
    scores["problems"] = problems
    return scores


def first_spoken_text(script_text, first_section_name):
    """
    Get the text of the script's first spoken section (the hook).

    Args:
        script_text (str): The script with "## " section headings.
        first_section_name (str): The hook section's name, e.g. "Hook".

    Returns:
        str: The section's text, or "" if it is missing.
    """
    from src.generation.writer import extract_sections
    return extract_sections(script_text).get(first_section_name, "")


def find_banned_opener(hook_text, banned_openers):
    """
    Check whether the hook starts with a phrase the channel has banned.

    Args:
        hook_text (str): The hook section's text.
        banned_openers (list[str]): e.g. ["Today", "In a recent"].

    Returns:
        str or None: The banned phrase it starts with, or None if it's fine.

    Example:
        find_banned_opener("Today the Senate voted...", ["Today"])  # → "Today"
    """
    opening = hook_text.strip().lower()
    for phrase in banned_openers or []:
        if opening.startswith(phrase.lower()):
            return phrase
    return None


def find_code_problems(script_text, config):
    """
    Run the free, plain-code checks on a script.

    Args:
        script_text (str): The script with "## " section headings.
        config (dict): The channel config (style + slogan).

    Returns:
        list[str]: One sentence per problem found (empty if none).
    """
    problems = []
    names = section_names(config)
    from src.generation.writer import find_missing_sections
    missing = find_missing_sections(script_text, names)
    if missing:
        problems.append("Missing sections: " + ", ".join(missing) + ".")

    hook_settings = (config.get("style") or {}).get("hook", {})
    hook_text = first_spoken_text(script_text, names[0])
    banned = find_banned_opener(hook_text, hook_settings.get("banned_openers"))
    if banned:
        problems.append(f'The hook starts with "{banned}", which this channel never uses. '
                        "Open with a surprising fact, a direct question, or what it means for the viewer.")

    problems.extend(find_length_problems(script_text, config))

    slogan = config.get("slogan", "")
    if slogan:
        closing = first_spoken_text(script_text, names[-1])
        if slogan.lower().rstrip(".") not in closing.lower():
            problems.append(f'The closing line must end with the slogan: "{slogan}"')
    return problems


def find_length_problems(script_text, config):
    """
    Check the script's length in code (models are bad at counting words).

    Args:
        script_text (str): The script with "## " section headings.
        config (dict): The channel config (target length, section budgets).

    Returns:
        list[str]: One problem if the script is too long overall, naming
                   each section that is over its word budget. Empty if fine.

    Example:
        find_length_problems(long_script, config)
        # → ["The spoken script is 268 words; ... Over budget: What happened 71/40."]
    """
    from src.generation.writer import count_spoken_words, extract_sections
    target = target_word_count(config["target_length_seconds"])
    spoken = count_spoken_words(script_text)
    if spoken <= target * (1 + LENGTH_TOLERANCE):
        return []

    sections = extract_sections(script_text)
    over_budget = []
    for section in script_sections(config):
        if not section["max_words"]:
            continue
        words = len(sections.get(section["name"], "").split())
        if words > section["max_words"]:
            over_budget.append(f"{section['name']} {words}/{section['max_words']}")

    problem = (f"The spoken script is {spoken} words; it must be at most {target} words "
               f"(about {config['target_length_seconds']} seconds). Cut the least important "
               "details, not the hook, the explanation or the slogan.")
    if over_budget:
        problem += " Over budget (words/limit): " + ", ".join(over_budget) + "."
    return [problem]


def low_scores(scores, min_score):
    """
    List the scores that fall below the channel's minimum.

    Args:
        scores (dict or None): Output of parse_review_reply().
        min_score (int): The lowest acceptable score.

    Returns:
        list[str]: e.g. ["hook 5/10"]. Empty if all scores pass or there
                   are no scores.
    """
    if not scores:
        return []
    failing = []
    for name in SCORE_NAMES:
        if scores[name] < min_score:
            failing.append(f"{name} {scores[name]}/10")
    return failing


def score_script(script_text, article_text, config, llm):
    """
    Ask the cheap model to score a script against its source.

    Args:
        script_text (str): The script to check.
        article_text (str): The source article.
        config (dict): The channel config (models + llm limits).
        llm (LLMClient): The run's AI client.

    Returns:
        dict or None: Scores from parse_review_reply(), or None if the
                      model's answer could not be read.
    """
    models = config["models"]
    reply = llm.ask(
        model=models.get("review", models["ranking"]),
        system_prompt=REVIEW_SYSTEM_PROMPT,
        user_message=build_review_message(script_text, article_text),
        max_tokens=config["llm"].get("review_max_tokens", 800),
        purpose="review",
    )
    return parse_review_reply(reply)


def build_rewrite_message(story, article_text, draft_text, reasons):
    """
    Ask the writing model to fix a draft, listing exactly what was wrong.

    Args:
        story (dict): The story ('title', 'source', 'url').
        article_text (str): The source article.
        draft_text (str): The first draft of the script.
        reasons (list[str]): Problems to fix (low scores and checker notes).

    Returns:
        str: The message for the writing model.
    """
    problem_lines = []
    for reason in reasons:
        problem_lines.append(f"- {reason}")
    return (
        build_writing_user_message(story, article_text)
        + "\n\nHere is your first draft:\n<draft>\n"
        + draft_text
        + "\n</draft>\n\nAn editor found these problems:\n"
        + "\n".join(problem_lines)
        + "\n\nRewrite the whole script to fix them. Keep every rule and the exact same "
          "section format. Only use facts from the source."
    )


def review_and_improve(script_text, story, article_text, config, llm):
    """
    Score a script and, if it falls short, rewrite it once.

    Args:
        script_text (str): The first draft from the writing model.
        story (dict): The story being covered.
        article_text (str): The source article.
        config (dict): The channel config.
        llm (LLMClient): The run's AI client.

    Returns:
        tuple: (final script text, report dict). The report has
               "first" (scores), "reasons" (why a rewrite happened, or []),
               "rewritten" (bool) and "final" (scores of the final text).

    Example:
        final_text, report = review_and_improve(draft, story, article, config, llm)
        report["rewritten"]   # → False when the first draft was good enough
    """
    min_score = (config.get("quality_check") or {}).get("min_score", DEFAULT_MIN_SCORE)

    first_scores = score_script(script_text, article_text, config, llm)
    reasons = find_code_problems(script_text, config)
    failing = low_scores(first_scores, min_score)
    if failing:
        reasons.append("Scores below " + str(min_score) + ": " + ", ".join(failing) + ".")
        reasons.extend(first_scores["problems"])

    report = {"first": first_scores, "reasons": reasons, "rewritten": False,
              "final": first_scores, "min_score": min_score}
    if not reasons:
        return script_text, report

    print("  Script check found problems — rewriting once:")
    for reason in reasons:
        print(f"    - {reason}")

    models = config["models"]
    rewritten = llm.ask(
        model=models["writing"],
        system_prompt=build_writing_system_prompt(config),
        user_message=build_rewrite_message(story, article_text, script_text, reasons),
        max_tokens=config["llm"]["writing_max_tokens"],
        effort=config["llm"].get("writing_effort"),
        purpose="rewrite",
    )
    report["rewritten"] = True
    report["final"] = score_script(rewritten, article_text, config, llm)
    report["final_code_problems"] = find_code_problems(rewritten, config)
    return rewritten, report


def describe_scores(scores):
    """
    Write scores as one short line, e.g. "hook 8 · clarity 9 · ...".

    Args:
        scores (dict or None): Output of parse_review_reply().

    Returns:
        str: The line, or "not available" if there were no scores.
    """
    if not scores:
        return "not available (the checker's answer could not be read)"
    parts = []
    for name in SCORE_NAMES:
        parts.append(f"{name} {scores[name]}")
    return " · ".join(parts)


def format_quality_section(report):
    """
    Turn a review report into the "## Quality check" part of the script file.

    This section is for the human reviewer and is never spoken.

    Args:
        report (dict): Output of review_and_improve().

    Returns:
        str: Markdown text starting with "## Quality check".
    """
    lines = ["## Quality check", ""]
    lines.append(f"- **First draft:** {describe_scores(report['first'])} "
                 f"(minimum {report['min_score']})")
    if report["rewritten"]:
        lines.append("- **Rewritten once because:**")
        for reason in report["reasons"]:
            lines.append(f"  - {reason}")
        lines.append(f"- **Final script:** {describe_scores(report['final'])}")
        for problem in report.get("final_code_problems", []):
            lines.append(f"- **Still needs a look:** {problem}")
        final_low = low_scores(report["final"], report["min_score"])
        if final_low:
            lines.append(f"- **Still below minimum:** {', '.join(final_low)} — check carefully.")
    else:
        lines.append("- Passed on the first draft; no rewrite needed.")
    return "\n".join(lines)

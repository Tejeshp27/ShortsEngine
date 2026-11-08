# =============================================================
# src/video/visual_planner.py — plan which visual to show for each
# sentence of a script (Phase V.2)
#
# After a script is written, this module sends the spoken text to
# Claude Haiku (the cheap model) and gets back a "visual plan":
# a list of segments, where each segment says:
#   - what text is being spoken
#   - what type of visual to show (stock_photo, chart, text_card)
#   - a search query (for Pexels) or chart data (for matplotlib)
#
# The plan is saved as JSON next to the script so it can be
# reviewed and edited before the video is rendered.
#
# How it fits in:
#   main.py → write_script() → plan_visuals()  (new step, optional)
#          → render_video()   reads the saved plan
# =============================================================

import json
import os


# How many spoken words form one "visual segment" — roughly one
# sentence's worth at a time, so visuals change every few seconds.
WORDS_PER_SEGMENT = 15


def _split_into_segments(spoken_text, words_per_segment=WORDS_PER_SEGMENT):
    """
    Split the spoken script text into chunks of roughly equal length.

    Each chunk becomes one visual segment in the video — the visual
    changes when the next chunk starts.

    Args:
        spoken_text (str): The full spoken script (from tts._extract_spoken_text).
        words_per_segment (int): Target number of words per segment.

    Returns:
        list[str]: Each element is one segment's worth of text.

    Example:
        segs = _split_into_segments("Word one two three...", words_per_segment=3)
        # -> ["Word one two", "three...", ...]
    """
    words = spoken_text.split()
    segments = []

    index = 0
    while index < len(words):
        chunk = words[index : index + words_per_segment]
        segments.append(" ".join(chunk))
        index += words_per_segment

    return segments


def _build_planning_prompt(segments, channel_config):
    """
    Build the prompt we send to Claude Haiku asking it to plan visuals.

    Args:
        segments (list[str]): The spoken text chunks.
        channel_config (dict): The channel config (for niche / tone context).

    Returns:
        str: A prompt string ready to send.
    """
    niche = channel_config.get("niche", "general news")
    numbered = "\n".join(f"{i+1}. {seg}" for i, seg in enumerate(segments))

    return f"""You are helping plan visuals for a short-form news video about {niche}.

For each spoken segment below, choose the best visual type and write a short query or note.

Visual types:
- stock_photo: a still image from a photo library (use for most segments)
- chart: a simple bar or line chart (use ONLY if the segment contains specific numbers/stats)
- text_card: show the words as styled text (use for the hook, closing line, or if no good photo exists)

For stock_photo and text_card, write a 3–6 word search query describing what to show.
For chart, write the data as "label1:value1, label2:value2" (use numbers from the text only).

Rules:
- Never suggest images of real, named politicians or public figures (copyright + likeness risk).
- Prefer symbolic or scene-based queries ("Capitol building exterior", "crowd at rally", "news broadcast studio").
- Keep each search query to 3–6 words.

Segments:
{numbered}

Reply with ONLY a JSON array, one object per segment, like this:
[
  {{"segment": "...", "visual_type": "stock_photo", "query": "Capitol building exterior"}},
  {{"segment": "...", "visual_type": "chart", "query": "Support:52, Oppose:41, Unsure:7"}},
  {{"segment": "...", "visual_type": "text_card", "query": "Breaking news today"}}
]"""


def plan_visuals(script_path, channel_config, llm, dry_run=False):
    """
    Use Claude Haiku to generate a visual plan for a script.

    The plan is saved as JSON next to the script file and also returned
    as a Python list so the renderer can use it right away.

    Args:
        script_path (str): Path to the .md script file.
        channel_config (dict): The channel config dict.
        llm (LLMClient): The run's AI client.
        dry_run (bool): If True, return a simple text_card plan without
                        making an AI call.

    Returns:
        list[dict]: The visual plan — one dict per segment:
            {"segment": str, "visual_type": str, "query": str}

    Example:
        plan = plan_visuals("output/news/2026-11-07-story.md", config, llm)
        print(plan[0])
        # {"segment": "Breaking news today...", "visual_type": "stock_photo",
        #  "query": "breaking news studio"}
    """
    # Where to save the JSON plan — same folder and stem as the script.
    base = os.path.splitext(script_path)[0]
    plan_path = base + "_visuals.json"

    # Already planned? Load from cache.
    if os.path.exists(plan_path):
        print("  Visual plan loaded from cache.")
        with open(plan_path, "r", encoding="utf-8") as f:
            return json.load(f)

    # Import here to avoid a circular dependency with render.py.
    from src.video.tts import _extract_spoken_text

    spoken_text = _extract_spoken_text(script_path)
    if not spoken_text:
        print("  WARNING: No spoken text found — skipping visual plan.")
        return []

    segments = _split_into_segments(spoken_text)

    if dry_run:
        # In dry-run mode, make a simple plan with text cards for every segment.
        print(f"  [DRY RUN] Skipping visual planning AI call ({len(segments)} segments).")
        plan = [
            {"segment": seg, "visual_type": "text_card", "query": seg[:40]}
            for seg in segments
        ]
        return plan

    print(f"  Planning visuals with {channel_config['models']['ranking']} "
          f"({len(segments)} segments)...")

    prompt = _build_planning_prompt(segments, channel_config)

    response_text = llm.ask(
        model=channel_config["models"]["ranking"],
        system_prompt=(
            "You are a video producer. Reply ONLY with valid JSON — "
            "no explanation, no markdown, just the array."
        ),
        user_message=prompt,
        max_tokens=1500,
    )

    # Parse the JSON response. If it fails, fall back to text cards.
    try:
        # Strip any accidental markdown fences the model might add.
        cleaned = response_text.strip()
        if cleaned.startswith("```"):
            cleaned = "\n".join(cleaned.split("\n")[1:])
        if cleaned.endswith("```"):
            cleaned = "\n".join(cleaned.split("\n")[:-1])

        plan = json.loads(cleaned)

        # Make sure every entry has the required fields.
        validated = []
        for item in plan:
            validated.append({
                "segment": item.get("segment", ""),
                "visual_type": item.get("visual_type", "text_card"),
                "query": item.get("query", ""),
            })

        print(f"  Visual plan created: {len(validated)} segments.")

    except (json.JSONDecodeError, KeyError, TypeError) as err:
        print(f"  WARNING: Could not parse visual plan ({err}). "
              "Falling back to text cards.")
        validated = [
            {"segment": seg, "visual_type": "text_card", "query": seg[:40]}
            for seg in segments
        ]

    # Save the plan next to the script.
    os.makedirs(os.path.dirname(plan_path), exist_ok=True)
    with open(plan_path, "w", encoding="utf-8") as f:
        json.dump(validated, f, indent=2)

    print(f"  Visual plan saved: {plan_path}")
    return validated

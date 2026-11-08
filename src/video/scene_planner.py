# =============================================================
# src/video/scene_planner.py — plan which visual to show for each
# segment of a script (M.2 of the M-series visual upgrade)
#
# This module supersedes visual_planner.py. It uses Claude Haiku
# to plan a richer "scene plan" with five visual types:
#
#   footage   — short B-roll video clip from Pexels Video
#   image     — still editorial photo (article image, Pexels, Wikimedia)
#   animated  — Pillow-drawn animated graphic (number callouts, bars)
#   chart     — matplotlib bar/line chart (numbers from the text only)
#   text_card — styled text overlay (hook, closing line, strong facts)
#
# Each entry maps to roughly 15 spoken words (~6 seconds of audio).
# The plan is saved as <slug>_sceneplan.json next to the script so it
# can be reviewed and edited before the video is rendered.
#
# Key design decision: "footage" and "animated" segments return a LIST
# of frame paths (one per caption group) rather than a single image,
# so each caption group within the segment gets its own unique visual.
#
# How it fits in:
#   main.py → run_render_pipeline() → plan_scenes() → scene plan JSON
#   render.py reads the plan and calls the right visual module per type
# =============================================================

import json
import os


# How many spoken words form one "visual segment" — roughly one
# sentence's worth, so the visual changes every few seconds.
WORDS_PER_SEGMENT = 15

# All valid scene types. Anything else falls back to "text_card".
VALID_SCENE_TYPES = {"footage", "image", "animated", "chart", "text_card"}


def _split_into_segments(spoken_text, words_per_segment=WORDS_PER_SEGMENT):
    """
    Split the spoken script text into chunks of roughly equal word count.

    Each chunk becomes one visual segment in the video — the visual
    changes when the next chunk starts.

    Args:
        spoken_text (str): The full spoken text from the script.
        words_per_segment (int): Target number of words per segment.

    Returns:
        list[str]: Each element is one segment's worth of text.

    Example:
        segs = _split_into_segments("One two three four five", words_per_segment=3)
        # → ["One two three", "four five"]
    """
    words = spoken_text.split()
    segments = []
    index = 0
    while index < len(words):
        chunk = words[index : index + words_per_segment]
        segments.append(" ".join(chunk))
        index += words_per_segment
    return segments


def _build_scene_prompt(segments, channel_config, style="auto"):
    """
    Build the prompt sent to Claude Haiku asking it to plan scenes.

    Args:
        segments (list[str]): The spoken text chunks.
        channel_config (dict): The channel config (for niche / tone context).
        style (str): "auto" (best choice per segment), "footage" (prefer video),
                     or "animated" (prefer graphics for data-heavy segments).

    Returns:
        str: A prompt string ready to send to the LLM.
    """
    niche = channel_config.get("niche", "general news")
    numbered = "\n".join(f"{i+1}. {seg}" for i, seg in enumerate(segments))

    if style == "footage":
        preference = "Strongly prefer 'footage' for every segment possible."
    elif style == "animated":
        preference = (
            "Prefer 'animated' for segments mentioning numbers or stats. "
            "Use 'footage' for action/location segments."
        )
    else:
        preference = "Choose the best type for each segment based on the content."

    return f"""You are a video producer planning visuals for a short-form news video about {niche}.

For each spoken segment below, pick the best visual type and write a short query or note.

Visual types:
- footage: a B-roll video clip from a stock library
  - Use for: action, movement, locations, events ("plane landing", "crowd gathering")
  - Query: 3–6 word search term for the video library
- image: a still editorial photo
  - Use for: buildings, documents, locations, scenes
  - Query: 3–6 word search term
- animated: a Pillow-drawn animated graphic — use for a single stat or comparison
  - For a single number: write "value:unit" (e.g. "27000:feet" or "9:passengers")
  - For two values compared: write "label1:value1, label2:value2" (e.g. "Normal:500, Crash:20000")
  - Only use numbers that actually appear in the segment text
- chart: a matplotlib bar chart
  - Use ONLY when the segment contains 3 or more specific numbers to compare
  - Query: "label1:value1, label2:value2, label3:value3" with real numbers only
- text_card: bold text on a dark background
  - Use for: the opening hook, closing line, or if no strong visual exists

Rules:
- Never suggest real, named politicians or celebrities (likeness risk).
- For footage and image, write a 3–6 word search term describing the scene.
- For animated and chart, only use numbers that appear in the segment text.
- {preference}

Segments:
{numbered}

Reply with ONLY a JSON array — no explanation, no markdown code fences, just the array:
[
  {{"segment": "...", "scene_type": "footage", "query": "plane flying blue sky", "description": "aircraft B-roll"}},
  {{"segment": "...", "scene_type": "animated", "query": "27000:feet", "description": "altitude stat callout"}},
  {{"segment": "...", "scene_type": "text_card", "query": "Nine people confirmed dead", "description": "closing statement"}}
]"""


def plan_scenes(script_path, channel_config, llm, dry_run=False, style="auto"):
    """
    Use Claude Haiku to generate a scene plan for a script.

    The plan is saved as JSON next to the script file and also returned
    as a Python list so the renderer can use it right away.

    Args:
        script_path (str): Path to the .md script file.
        channel_config (dict): The channel config dict.
        llm (LLMClient): The run's AI client.
        dry_run (bool): If True, return a simple text_card plan without
                        making an AI call.
        style (str): "auto" (let Haiku decide), "footage" (prefer video),
                     or "animated" (prefer graphics).

    Returns:
        list[dict]: The scene plan — one dict per segment:
            {
                "segment":     str,   # spoken text for this segment
                "scene_type":  str,   # one of the VALID_SCENE_TYPES
                "query":       str,   # search term or animation data
                "description": str,   # short human-readable label
            }

    Example:
        plan = plan_scenes("output/news/2026-11-07-story.md", config, llm)
        print(plan[0])
        # {"segment": "Breaking news today...", "scene_type": "footage",
        #  "query": "breaking news broadcast studio", "description": "news anchor B-roll"}
    """
    base = os.path.splitext(script_path)[0]
    plan_path = base + "_sceneplan.json"

    # Already planned? Load from cache.
    if os.path.exists(plan_path):
        print("  Scene plan loaded from cache.")
        with open(plan_path, "r", encoding="utf-8") as f:
            return json.load(f)

    from src.video.tts import _extract_spoken_text

    spoken_text = _extract_spoken_text(script_path)
    if not spoken_text:
        print("  WARNING: No spoken text found — skipping scene plan.")
        return []

    segments = _split_into_segments(spoken_text)

    if dry_run:
        # In dry-run mode, make a simple plan without an AI call.
        print(f"  [DRY RUN] Skipping scene planning AI call ({len(segments)} segments).")
        plan = [
            {
                "segment": seg,
                "scene_type": "text_card",
                "query": seg[:40],
                "description": "dry-run placeholder",
            }
            for seg in segments
        ]
        os.makedirs(os.path.dirname(plan_path) or ".", exist_ok=True)
        with open(plan_path, "w", encoding="utf-8") as f:
            json.dump(plan, f, indent=2)
        return plan

    ranking_model = channel_config["models"]["ranking"]
    print(f"  Planning scenes with {ranking_model} ({len(segments)} segments, style={style})...")

    prompt = _build_scene_prompt(segments, channel_config, style=style)

    response_text = llm.ask(
        model=ranking_model,
        system_prompt=(
            "You are a video producer. Reply ONLY with valid JSON — "
            "no explanation, no markdown, just the array."
        ),
        user_message=prompt,
        max_tokens=1800,
        purpose="scene_planning",
    )

    # Parse the JSON response. If it fails, fall back to text cards.
    try:
        # Strip any accidental markdown fences the model might add.
        cleaned = response_text.strip()
        if cleaned.startswith("```"):
            cleaned = "\n".join(cleaned.split("\n")[1:])
        if cleaned.endswith("```"):
            cleaned = "\n".join(cleaned.split("\n")[:-1])

        raw_plan = json.loads(cleaned)

        validated = []
        for item in raw_plan:
            scene_type = item.get("scene_type", "text_card")
            # Accept "stock_photo" from older prompts — treat as "image".
            if scene_type == "stock_photo":
                scene_type = "image"
            if scene_type not in VALID_SCENE_TYPES:
                scene_type = "text_card"

            validated.append({
                "segment":     item.get("segment", ""),
                "scene_type":  scene_type,
                "query":       item.get("query", ""),
                "description": item.get("description", ""),
            })

        print(f"  Scene plan created: {len(validated)} segments.")

    except (json.JSONDecodeError, KeyError, TypeError) as err:
        print(f"  WARNING: Could not parse scene plan ({err}). Falling back to text cards.")
        validated = [
            {
                "segment":     seg,
                "scene_type":  "text_card",
                "query":       seg[:40],
                "description": "fallback",
            }
            for seg in segments
        ]

    # Save the plan next to the script.
    os.makedirs(os.path.dirname(plan_path) or ".", exist_ok=True)
    with open(plan_path, "w", encoding="utf-8") as f:
        json.dump(validated, f, indent=2)

    print(f"  Scene plan saved: {plan_path}")
    return validated

# =============================================================
# src/video/charts.py — generate chart PNGs for the video (Phase V.5)
#
# When a script segment contains numbers or statistics, we can show
# a simple chart instead of a stock photo. The chart is generated
# locally using matplotlib — no API key or internet connection needed.
#
# The visual planner (visual_planner.py) produces a "query" string
# in the format "Label1:Value1, Label2:Value2" for chart segments.
# This module parses that string and draws the chart.
#
# Charts are saved as 1080x1920 PNGs (9:16, same as the video) so
# they drop straight into the frame sequence.
#
# How it fits in:
#   render.py → _get_visual_for_segment() → make_chart() (this file)
# =============================================================

import os
import re

# matplotlib is a standard Python chart library. We use the "Agg"
# backend so it works without a display (important when running on a
# server or inside Docker).
import matplotlib
matplotlib.use("Agg")

import matplotlib.pyplot as plt
import matplotlib.patches as mpatches


# Output folder for generated chart images.
CHARTS_FOLDER = os.path.join("cache", "charts")

# 9:16 portrait dimensions in pixels.
VIDEO_WIDTH = 1080
VIDEO_HEIGHT = 1920


def _parse_chart_data(query):
    """
    Parse the visual planner's chart query string into labels and values.

    The expected format is "Label1:Value1, Label2:Value2".

    Args:
        query (str): e.g. "Support:52, Oppose:41, Unsure:7"

    Returns:
        tuple[list[str], list[float]]: (labels, values), or ([], []) on failure.

    Example:
        _parse_chart_data("Yes:62, No:31, Unsure:7")
        # -> (["Yes", "No", "Unsure"], [62.0, 31.0, 7.0])
    """
    labels = []
    values = []

    # Split on commas, then split each piece on the first colon.
    for part in query.split(","):
        part = part.strip()
        if ":" not in part:
            continue

        # rsplit with maxsplit=1 so "Long label:value" works correctly.
        label, value_str = part.rsplit(":", 1)
        label = label.strip()

        # Extract just the number from the value (ignore % signs etc.)
        numbers = re.findall(r"[\d.]+", value_str)
        if not numbers:
            continue

        labels.append(label)
        values.append(float(numbers[0]))

    return labels, values


def make_chart(query, output_path, channel_key="news"):
    """
    Draw a simple horizontal bar chart and save it as a 1080×1920 PNG.

    The chart uses dark colours that match the video background so it
    looks like part of the video rather than a pasted-in image.

    Args:
        query (str): The data string from the visual plan,
                     e.g. "Support:52, Oppose:41, Unsure:7".
        output_path (str): Where to save the PNG file.
        channel_key (str): Used to pick the accent colour.

    Returns:
        str or None: The path to the saved PNG, or None if the data
                     could not be parsed.

    Example:
        path = make_chart("Yes:62, No:31, Unsure:7", "cache/charts/abc.png")
    """
    labels, values = _parse_chart_data(query)

    if not labels or not values:
        return None

    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    # --- Style settings ---
    # We build everything in figure-inches; at 100 dpi this gives us
    # exactly the 1080×1920 pixel size we need.
    fig_width = VIDEO_WIDTH / 100
    fig_height = VIDEO_HEIGHT / 100

    bg_color = "#1a1a2e"        # dark navy — same as the video background
    accent_color = "#8888ff"    # purple — the dashboard accent colour
    text_color = "#e8e8f0"      # near-white

    fig, ax = plt.subplots(figsize=(fig_width, fig_height))
    fig.patch.set_facecolor(bg_color)
    ax.set_facecolor(bg_color)

    # Horizontal bars make labels easier to read on a tall screen.
    y_positions = range(len(labels))
    bars = ax.barh(list(y_positions), values, color=accent_color,
                   height=0.5, align="center")

    # Label each bar with its value at the right end.
    for bar, value in zip(bars, values):
        ax.text(
            bar.get_width() + max(values) * 0.02,
            bar.get_y() + bar.get_height() / 2,
            f"{value:g}",
            va="center",
            ha="left",
            color=text_color,
            fontsize=28,
            fontweight="bold",
        )

    # Set the y-axis ticks to our label text.
    ax.set_yticks(list(y_positions))
    ax.set_yticklabels(labels, color=text_color, fontsize=30)

    # Hide all the axes chrome we don't want.
    ax.set_xticks([])
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["bottom"].set_visible(False)
    ax.spines["left"].set_color("#2a2a4e")

    # Extend the x-axis limit so the value labels have room.
    ax.set_xlim(0, max(values) * 1.25)

    # Add generous padding so nothing is cut off on a portrait screen.
    plt.tight_layout(pad=4.0)

    plt.savefig(output_path, dpi=100, bbox_inches="tight",
                facecolor=bg_color, edgecolor="none")
    plt.close(fig)

    return output_path

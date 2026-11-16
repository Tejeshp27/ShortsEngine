# =============================================================
# src/video/animation/ — the animated video engine
#
# This folder turns a script into a fully animated video where
# something is always moving. Every frame is built in layers:
#
#   1. Real stock video footage playing full-screen     (footage.py)
#   2. An animated graphic for the current sentence     (scenes.py)
#   3. Word-by-word captions, progress bar, transitions (overlays.py)
#
# The other files support those three layers:
#
#   storyboard.py — asks the cheap AI model to pick the footage
#                   and graphic for each sentence of the script
#   renderer.py   — the main loop: draws every frame and sends
#                   it to FFmpeg, which encodes the final MP4
#   drawing.py    — small helpers for drawing text and shapes
#   easing.py     — maths that makes motion look smooth
#   icons.py      — downloads free emoji icons to use as artwork
#
# How it fits in:
#   main.py --render --animated → render.py → renderer.py (here)
# =============================================================

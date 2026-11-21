# =============================================================
# src/clips/framing.py — find the streamer's face and choose a
#                        layout that never cuts off their head
#
# Problem this solves: the old renderer used one fixed webcam box per
# streamer (measured once by hand). When a streamer moved their camera,
# or had no box saved, the face was cropped or shrunk to a dot.
#
# What it does for every clip:
#   1. Looks at ~12 frames spread over the moment and finds faces with
#      the YuNet face detector (OpenCV, MIT-licensed model, 230 KB, CPU).
#   2. The streamer's face is the face that stays in the SAME place in
#      most frames (a webcam does not move). Game characters and people
#      in videos come and go or move around, so they are ignored.
#   3. Looks for the webcam's border around that face (a straight edge
#      that stays still), so the crop shows the camera and not gameplay.
#   4. Chooses a layout for this moment (research: docs/VIRAL_CONTENT_RESEARCH.md):
#        - "split" (face on top, gameplay below) for a webcam overlay,
#        - "face-crop" (a tall crop around the person) for IRL / full camera,
#        - "fit-blur" (the whole picture) when no steady face is found or
#          the head cannot be kept whole.
#   5. Checks the plan: the whole head (forehead to chin) must be inside
#      the crop, not enlarged absurdly, and no text may cover the face.
#      If any check fails, it falls back to "fit-blur", which shows the
#      entire picture and can never cut anything off.
#
# Pure maths functions (no video needed) are separate from the ones that
# read frames, so they are easy to test.
# =============================================================

import hashlib
import os
import subprocess
from pathlib import Path

import numpy as np

from src.video.clip_layout import OUTPUT_HEIGHT, OUTPUT_WIDTH, round_to_even

MODEL_PATH = Path("models") / "face" / "face_detection_yunet_2023mar.onnx"
MODEL_URL = ("https://github.com/opencv/opencv_zoo/raw/main/models/"
             "face_detection_yunet/face_detection_yunet_2023mar.onnx")
MODEL_SHA256 = "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4"

# Frames are analysed at this size (big enough for a small webcam face).
SAMPLE_WIDTH = 960
SAMPLE_HEIGHT = 540

# How far the whole head reaches beyond the detected face box, as shares of
# the face's own size. The detector's box covers roughly brows to chin, so
# hair/forehead needs much more room above than the chin needs below.
HEAD_ABOVE = 0.6
HEAD_BELOW = 0.3
HEAD_SIDES = 0.35

_detector = None


# ---------------------------------------------------------------------------
# Loading the face detector
# ---------------------------------------------------------------------------

def download_model():
    """
    Download the face detector model once and check it is the expected file.

    Returns:
        bool: True when the model is ready.
    """
    import httpx

    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    partial = MODEL_PATH.with_suffix(".part")
    try:
        response = httpx.get(MODEL_URL, follow_redirects=True, timeout=60)
        response.raise_for_status()
        partial.write_bytes(response.content)
    except httpx.HTTPError:
        partial.unlink(missing_ok=True)
        return False
    if hashlib.sha256(partial.read_bytes()).hexdigest() != MODEL_SHA256:
        partial.unlink(missing_ok=True)
        return False
    os.replace(partial, MODEL_PATH)
    return True


def face_detector():
    """
    Load the YuNet face detector (once per process).

    Returns:
        cv2.FaceDetectorYN or None: The detector, or None when OpenCV or the
        model is unavailable (the caller then keeps the whole picture).
    """
    global _detector
    if _detector is not None:
        return _detector
    try:
        import cv2
    except ImportError:
        return None
    if not MODEL_PATH.is_file() and not download_model():
        return None
    # 0.6 = minimum confidence, 0.3 = overlap threshold, 50 = most faces per frame.
    _detector = cv2.FaceDetectorYN.create(str(MODEL_PATH), "", (SAMPLE_WIDTH, SAMPLE_HEIGHT), 0.6, 0.3, 50)
    return _detector


# ---------------------------------------------------------------------------
# Reading frames and finding faces
# ---------------------------------------------------------------------------

def sample_times(start, end, count):
    """
    Spread `count` sample times evenly over a moment (avoiding the very edges).

    Args:
        start (float): Moment start, in seconds.
        end (float): Moment end, in seconds.
        count (int): How many samples.

    Returns:
        list[float]: The times.

    Example:
        sample_times(0, 10, 4) → [1.25, 3.75, 6.25, 8.75]
    """
    times = []
    step = (end - start) / count
    for number in range(count):
        times.append(round(start + step * (number + 0.5), 3))
    return times


def read_frame(source, time_seconds):
    """
    Read one picture from a video, shrunk to the analysis size.

    Args:
        source (str): The video file.
        time_seconds (float): When in the video.

    Returns:
        numpy array (540, 960, 3) in BGR colour order, or None if unreadable.
    """
    command = ["ffmpeg", "-nostdin", "-v", "error", "-ss", f"{time_seconds:.3f}", "-i", str(source),
               "-frames:v", "1", "-vf", f"scale={SAMPLE_WIDTH}:{SAMPLE_HEIGHT}",
               "-f", "rawvideo", "-pix_fmt", "bgr24", "-"]
    result = subprocess.run(command, capture_output=True)
    expected = SAMPLE_WIDTH * SAMPLE_HEIGHT * 3
    if result.returncode != 0 or len(result.stdout) < expected:
        return None
    return np.frombuffer(result.stdout[:expected], dtype=np.uint8).reshape(SAMPLE_HEIGHT, SAMPLE_WIDTH, 3)


def faces_in_frame(detector, frame):
    """
    Find faces in one analysis frame.

    Args:
        detector: From face_detector().
        frame: A BGR picture from read_frame().

    Returns:
        list[dict]: {"x", "y", "w", "h", "score"} with positions and sizes as
                    fractions of the frame (0 to 1), so they fit any resolution.
    """
    detector.setInputSize((frame.shape[1], frame.shape[0]))
    _, found = detector.detect(frame)
    faces = []
    if found is None:
        return faces
    for row in found:
        faces.append({"x": float(row[0]) / frame.shape[1], "y": float(row[1]) / frame.shape[0],
                      "w": float(row[2]) / frame.shape[1], "h": float(row[3]) / frame.shape[0],
                      "score": round(float(row[14]), 3)})
    return faces


# ---------------------------------------------------------------------------
# Pure maths: which face is the streamer, where is the head
# ---------------------------------------------------------------------------

def face_center(face):
    """
    Return the middle point of a face box.

    Args:
        face (dict): {"x", "y", "w", "h"} as fractions.

    Returns:
        tuple: (x, y) of the centre.
    """
    return face["x"] + face["w"] / 2, face["y"] + face["h"] / 2


def group_steady_faces(faces_per_frame):
    """
    Group face detections from many frames into "the same face in the same place".

    A face joins a group when its centre is within 60% of the group's face
    width and its size is similar. A webcam face lands in one group that is
    present in most frames; moving game characters spread over many groups.

    Args:
        faces_per_frame (list[list[dict]]): The faces found in each frame.

    Returns:
        list[dict]: One entry per group: {"face": median box, "frames": how many
                    frames it was in, "share": frames / all frames, "spread": how
                    much its centre wandered (in face widths)}.
    """
    groups = []
    for frame_number, faces in enumerate(faces_per_frame):
        for face in faces:
            center_x, center_y = face_center(face)
            placed = False
            for group in groups:
                last = group["members"][-1]
                last_x, last_y = face_center(last)
                distance = ((center_x - last_x) ** 2 + (center_y - last_y) ** 2) ** 0.5
                size_ratio = face["h"] / max(last["h"], 1e-6)
                if distance <= 0.6 * last["w"] and 0.6 <= size_ratio <= 1.6:
                    group["members"].append(face)
                    group["frame_numbers"].add(frame_number)
                    placed = True
                    break
            if not placed:
                groups.append({"members": [face], "frame_numbers": {frame_number}})
    total_frames = max(1, len(faces_per_frame))
    result = []
    for group in groups:
        members = group["members"]
        median = {}
        for key in ("x", "y", "w", "h", "score"):
            values = []
            for member in members:
                values.append(member[key])
            median[key] = float(np.median(values))
        centers_x = []
        centers_y = []
        for member in members:
            center_x, center_y = face_center(member)
            centers_x.append(center_x)
            centers_y.append(center_y)
        spread = float(np.hypot(np.std(centers_x), np.std(centers_y))) / max(median["w"], 1e-6)
        result.append({"face": median, "frames": len(group["frame_numbers"]),
                       "share": round(len(group["frame_numbers"]) / total_frames, 3),
                       "spread": round(spread, 3)})
    return result


def pick_streamer_faces(groups, minimum_share=0.4, maximum_spread=0.5):
    """
    Choose which steady faces belong to the streamer (and anyone sitting with them).

    Args:
        groups (list[dict]): From group_steady_faces().
        minimum_share (float): A face must be in at least this share of frames.
        maximum_spread (float): And may not wander more than this many face widths.

    Returns:
        list[dict]: Steady face groups, most present (then largest) first. Empty
                    when nobody stays on screen — then the picture is kept whole.
    """
    steady = []
    for group in groups:
        if group["share"] >= minimum_share and group["spread"] <= maximum_spread:
            steady.append(group)
    steady.sort(key=presence_then_size, reverse=True)
    return steady


def presence_then_size(group):
    """
    Sorting key: how often a face was seen, then how big it is.

    Args:
        group (dict): A face group.

    Returns:
        tuple: (share, face height).
    """
    return group["share"], group["face"]["h"]


def head_box(face):
    """
    Grow a face box to cover the whole head (hair, forehead, chin, ears).

    Args:
        face (dict): {"x", "y", "w", "h"} as fractions of the frame.

    Returns:
        dict: {"left", "top", "right", "bottom"} as fractions (may go past 0..1
              when the head is at the edge of the picture).

    Example:
        head_box({"x": 0.4, "y": 0.4, "w": 0.1, "h": 0.1})
        → {"left": 0.365, "top": 0.34, "right": 0.535, "bottom": 0.53}
    """
    return {"left": face["x"] - HEAD_SIDES * face["w"],
            "top": face["y"] - HEAD_ABOVE * face["h"],
            "right": face["x"] + face["w"] * (1 + HEAD_SIDES),
            "bottom": face["y"] + face["h"] * (1 + HEAD_BELOW)}


def box_contains(outer, inner, tolerance=0.0):
    """
    Check whether one box is completely inside another.

    Args:
        outer (dict): {"left", "top", "right", "bottom"}.
        inner (dict): Same keys.
        tolerance (float): How far the inner box may stick out.

    Returns:
        bool: True when inner fits inside outer.
    """
    return (inner["left"] >= outer["left"] - tolerance and inner["top"] >= outer["top"] - tolerance
            and inner["right"] <= outer["right"] + tolerance and inner["bottom"] <= outer["bottom"] + tolerance)


def crop_to_box(crop, source_width, source_height):
    """
    Turn a pixel crop into a fractional box.

    Args:
        crop (dict): {"x", "y", "width", "height"} in pixels.
        source_width (int): Picture width.
        source_height (int): Picture height.

    Returns:
        dict: {"left", "top", "right", "bottom"} as fractions.
    """
    return {"left": crop["x"] / source_width, "top": crop["y"] / source_height,
            "right": (crop["x"] + crop["width"]) / source_width,
            "bottom": (crop["y"] + crop["height"]) / source_height}


# ---------------------------------------------------------------------------
# Finding the webcam's border
# ---------------------------------------------------------------------------

def edge_strengths(gray):
    """
    Measure how sharp the brightness changes are across and down a picture.

    Args:
        gray (numpy array): A grayscale picture (rows x columns).

    Returns:
        tuple: (vertical_edges, horizontal_edges) — arrays the same size as the
               picture; big numbers mark a left/right edge or a top/bottom edge.
    """
    picture = gray.astype(float)
    vertical = np.zeros_like(picture)
    horizontal = np.zeros_like(picture)
    vertical[:, 1:] = np.abs(picture[:, 1:] - picture[:, :-1])
    horizontal[1:, :] = np.abs(picture[1:, :] - picture[:-1, :])
    return vertical, horizontal


def is_edge_line(values, threshold, coverage):
    """
    Check whether a line of edge strengths is a real straight edge.

    Args:
        values (numpy array): Edge strengths along one row or column piece.
        threshold (float): Strength that counts as an edge.
        coverage (float): Share of the line that must be an edge.

    Returns:
        bool: True when enough of the line is a sharp edge.
    """
    if values.size == 0:
        return False
    return float(np.mean(values > threshold)) >= coverage


def find_camera_border(gray, head, max_reach=3.5, threshold=24.0, coverage=0.65):
    """
    Look for the webcam's rectangular border around a head.

    Searches outward from the head on each side for a straight line where the
    brightness changes sharply along most of its length (an overlay border or
    the edge of the camera picture). Uses a still frame made from the middle
    value of many frames, so moving gameplay edges fade out.

    Args:
        gray (numpy array): Median grayscale frame (rows x columns).
        head (dict): Head box from head_box(), as fractions.
        max_reach (float): Search at most this many head-widths/heights away.
        threshold (float): Brightness change that counts as an edge (0–255).
        coverage (float): Share of the line that must be an edge.

    Returns:
        dict or None: {"left", "top", "right", "bottom"} as fractions when all four
                      sides were found, else None (a cut-out camera has no border).
    """
    rows, columns = gray.shape
    vertical, horizontal = edge_strengths(gray)
    left = int(max(0, head["left"]) * columns)
    right = int(min(1, head["right"]) * columns)
    top = int(max(0, head["top"]) * rows)
    bottom = int(min(1, head["bottom"]) * rows)
    head_width = max(4, right - left)
    head_height = max(4, bottom - top)

    # Columns are checked only between the head's top and bottom, rows only
    # between its left and right: a camera border runs past the whole head.
    found = {}
    for column in range(left - 1, max(0, int(left - max_reach * head_width)) - 1, -1):
        if is_edge_line(vertical[top:bottom, column], threshold, coverage):
            found["left"] = column / columns
            break
    if "left" not in found and left - max_reach * head_width <= 0:
        found["left"] = 0.0
    for column in range(right + 1, min(columns, int(right + max_reach * head_width))):
        if is_edge_line(vertical[top:bottom, column], threshold, coverage):
            found["right"] = column / columns
            break
    if "right" not in found and right + max_reach * head_width >= columns:
        found["right"] = 1.0
    for row in range(top - 1, max(0, int(top - max_reach * head_height)) - 1, -1):
        if is_edge_line(horizontal[row, left:right], threshold, coverage):
            found["top"] = row / rows
            break
    if "top" not in found and top - max_reach * head_height <= 0:
        found["top"] = 0.0
    for row in range(bottom + 1, min(rows, int(bottom + max_reach * head_height))):
        if is_edge_line(horizontal[row, left:right], threshold, coverage):
            found["bottom"] = row / rows
            break
    if "bottom" not in found and bottom + max_reach * head_height >= rows:
        found["bottom"] = 1.0
    if len(found) < 4 or (found["left"], found["top"], found["right"], found["bottom"]) == (0.0, 0.0, 1.0, 1.0):
        return None
    return found


# ---------------------------------------------------------------------------
# Building crops around the head
# ---------------------------------------------------------------------------

def camera_crop(face, panel_shape, source_width, source_height, camera_border=None,
                face_share=0.3):
    """
    Work out the part of the picture to show in the facecam panel.

    The crop is centred on the whole head (hair to chin), and sized so the
    face fills about `face_share` of the panel height (head and shoulders). When the webcam's border is known, the crop is kept
    inside it (no gameplay at the edges) as long as the whole head still fits.

    Args:
        face (dict): The streamer's face box (fractions).
        panel_shape (float): Panel width / height (e.g. 1080/864 = 1.25).
        source_width (int): Source picture width in pixels.
        source_height (int): Source picture height in pixels.
        camera_border (dict or None): The webcam border (fractions), if found.
        face_share (float): How much of the panel height the face should fill.

    Returns:
        dict: Pixel crop {"x", "y", "width", "height"} (even numbers).
    """
    face_height = face["h"] * source_height
    center_x = (face["x"] + face["w"] / 2) * source_width
    height = face_height / face_share
    width = height * panel_shape
    head = head_box(face)
    head_width = (head["right"] - head["left"]) * source_width * 1.15
    if width < head_width:
        width = head_width
        height = width / panel_shape
    if camera_border is not None:
        border_width = (camera_border["right"] - camera_border["left"]) * source_width
        border_height = (camera_border["bottom"] - camera_border["top"]) * source_height
        head_height = (head["bottom"] - head["top"]) * source_height
        # Fit inside the border, but never smaller than the head itself.
        fit_height = min(border_height, border_width / panel_shape)
        if fit_height >= head_height * 1.05 and fit_height * panel_shape >= head_width:
            height = min(height, fit_height)
            width = height * panel_shape
    width = min(width, source_width)
    height = min(height, source_height, width / panel_shape)
    width = height * panel_shape
    # Centre the whole head (not just the face) vertically, nudged up a
    # little so there is a bit more room below the chin (shoulders) than above.
    head_center_y = (head["top"] + head["bottom"]) / 2 * source_height
    top = head_center_y - height * 0.47
    left = center_x - width / 2
    if camera_border is not None:
        # Slide inside the border when the crop fits in it.
        border_left = camera_border["left"] * source_width
        border_right = camera_border["right"] * source_width
        border_top = camera_border["top"] * source_height
        border_bottom = camera_border["bottom"] * source_height
        if width <= border_right - border_left:
            left = min(max(left, border_left), border_right - width)
        if height <= border_bottom - border_top:
            top = min(max(top, border_top), border_bottom - height)
    left = min(max(left, 0), source_width - width)
    top = min(max(top, 0), source_height - height)
    return {"x": round_to_even(left), "y": round_to_even(top),
            "width": round_to_even(width), "height": round_to_even(height)}


def face_crop_focus(faces, source_width, source_height):
    """
    For a full-camera (IRL) shot: where to centre a tall 9:16 crop so every
    steady person's head fits, if possible.

    Args:
        faces (list[dict]): Steady face boxes (fractions).
        source_width (int): Picture width.
        source_height (int): Picture height.

    Returns:
        float or None: Horizontal centre (0 to 1), or None when the heads are
                       too far apart for one tall crop.
    """
    crop_width = source_height * OUTPUT_WIDTH / OUTPUT_HEIGHT / source_width
    lefts = []
    rights = []
    for face in faces:
        head = head_box(face)
        lefts.append(head["left"])
        rights.append(head["right"])
    if max(rights) - min(lefts) > crop_width * 0.95:
        return None
    return (max(rights) + min(lefts)) / 2


def square_focus(faces, source_width, source_height):
    """
    Where to centre a square crop (full picture height) so every head fits.

    Args:
        faces (list[dict]): Steady face boxes (fractions).
        source_width (int): Picture width.
        source_height (int): Picture height.

    Returns:
        float or None: Horizontal centre (0 to 1), or None when the heads do not fit.
    """
    square_width = min(1.0, source_height / source_width)
    lefts = []
    rights = []
    for face in faces:
        head = head_box(face)
        lefts.append(max(0.0, head["left"]))
        rights.append(min(1.0, head["right"]))
    if max(rights) - min(lefts) > square_width * 0.97:
        return None
    return (max(rights) + min(lefts)) / 2


def output_rect(head, crop, source_width, source_height, panel_top, panel_height):
    """
    Map a head box from the source picture to where it appears on the phone screen.

    Args:
        head (dict): Head box (fractions of the source).
        crop (dict): The panel's pixel crop.
        source_width (int): Source width.
        source_height (int): Source height.
        panel_top (int): The panel's top edge on the 1920-tall screen.
        panel_height (int): The panel's height on screen.

    Returns:
        dict: {"left", "top", "right", "bottom"} in screen pixels.
    """
    scale_x = OUTPUT_WIDTH / crop["width"]
    scale_y = panel_height / crop["height"]
    return {"left": (head["left"] * source_width - crop["x"]) * scale_x,
            "right": (head["right"] * source_width - crop["x"]) * scale_x,
            "top": panel_top + (head["top"] * source_height - crop["y"]) * scale_y,
            "bottom": panel_top + (head["bottom"] * source_height - crop["y"]) * scale_y}


# ---------------------------------------------------------------------------
# Choosing the layout and checking it
# ---------------------------------------------------------------------------

def choose_framing(steady, source_width, source_height, framing_settings, category="",
                   configured_box=None, camera_border=None):
    """
    Decide the layout for one clip from the steady faces found in it.

    Args:
        steady (list[dict]): From pick_streamer_faces().
        source_width (int): Source width in pixels.
        source_height (int): Source height in pixels.
        framing_settings (FramingSettings): Thresholds from the channel YAML.
        category (str): The moment's type from the ranking ("reaction",
                        "gameplay", "conversation"...); gameplay moments give the
                        game more room, reactions give the face more room.
        configured_box (FacecamBox or None): A hand-measured webcam box.
        camera_border (dict or None): A detected webcam border (fractions).

    Returns:
        dict: {"mode": "split" | "face-crop" | "fit-blur", "reason": plain text,
               plus "camera_crop", "gameplay_share" (split) or "focus_x" (face-crop)}.
    """
    if not steady:
        return {"mode": "fit-blur", "reason": "No steady face in this moment; the whole picture is kept."}
    main = steady[0]["face"]
    if main["h"] >= framing_settings.full_camera_face_height:
        faces = []
        for group in steady:
            if group["face"]["h"] >= framing_settings.full_camera_face_height * 0.5:
                faces.append(group["face"])
        focus = face_crop_focus(faces, source_width, source_height)
        if focus is None:
            square = square_focus(faces, source_width, source_height)
            if square is not None:
                return {"mode": "face-square", "focus_x": round(square, 4),
                        "reason": "Close-up too wide for a tall crop; a square around the head(s) is shown "
                                  "large over a blurred copy, nothing cut off."}
            return {"mode": "fit-blur", "reason": "The head(s) on camera are too wide or too far apart for a tall "
                                                  "crop without cutting someone off; the whole picture is kept."}
        return {"mode": "face-crop", "focus_x": round(focus, 4),
                "reason": f"Full-camera shot ({len(faces)} person/people); tall crop centred on the head(s)."}
    gameplay_share = framing_settings.gameplay_share
    if category in ("reaction", "conversation", "awkward"):
        gameplay_share = framing_settings.reaction_gameplay_share
    facecam_height = OUTPUT_HEIGHT - round_to_even(OUTPUT_HEIGHT * gameplay_share)
    shape = OUTPUT_WIDTH / facecam_height
    border = camera_border
    border_source = "detected border"
    if configured_box is not None:
        configured = {"left": configured_box.x, "top": configured_box.y,
                      "right": configured_box.x + configured_box.width,
                      "bottom": configured_box.y + configured_box.height}
        if box_contains(configured, head_box(main), tolerance=0.01):
            border = configured
            border_source = "configured box"
    crop = camera_crop(main, shape, source_width, source_height, border, framing_settings.face_share)
    if OUTPUT_WIDTH / max(1, crop["width"]) > framing_settings.max_zoom and gameplay_share < 0.65:
        # A tiny webcam: give it a shorter, wider panel so it is enlarged less.
        gameplay_share = 0.65
        facecam_height = OUTPUT_HEIGHT - round_to_even(OUTPUT_HEIGHT * gameplay_share)
        shape = OUTPUT_WIDTH / facecam_height
        crop = camera_crop(main, shape, source_width, source_height, border, framing_settings.face_share)
    reason = "Webcam overlay found by face detection"
    if border is not None:
        reason += f" ({border_source})"
    return {"mode": "split", "camera_crop": crop, "gameplay_share": gameplay_share,
            "face": main, "reason": reason + "; stacked face on top, gameplay below."}


def check_framing(decision, plan, steady, source_width, source_height, text_boxes, framing_settings):
    """
    Check a planned layout before rendering; return any problems found.

    Checks: the whole head is inside the crop (no cut forehead, chin or ears),
    the face is not enlarged absurdly, no text box covers the face, and the
    output is exactly 1080x1920 with no empty bars.

    Args:
        decision (dict): From choose_framing().
        plan (dict): The layout plan from clip_layout.plan_layout() (or plan_from_framing()).
        steady (list[dict]): Steady faces.
        source_width (int): Source width.
        source_height (int): Source height.
        text_boxes (list[dict]): Screen rectangles {"left","top","right","bottom"}
                                 where the hook and captions will be drawn.
        framing_settings (FramingSettings): max_zoom and other limits.

    Returns:
        list[str]: Plain-English problems (empty = the framing is safe).
    """
    problems = []
    if plan["mode"] == "fit-blur" or not steady:
        return problems
    face = steady[0]["face"]
    head = head_box(face)
    # The head may legitimately reach past the picture edge (streamer leaning
    # out of frame); only judge the part that exists in the source.
    visible_head = {"left": max(0.0, head["left"]), "top": max(0.0, head["top"]),
                    "right": min(1.0, head["right"]), "bottom": min(1.0, head["bottom"])}
    panel_top = 0
    face_panel = None
    for panel in plan["panels"]:
        if panel["role"] in ("facecam", "picture"):
            face_panel = panel
            break
        panel_top += panel["height"]
    if face_panel is None:
        return ["The layout has no panel showing the streamer."]
    crop_box_fractions = crop_to_box(face_panel["crop"], source_width, source_height)
    if not box_contains(crop_box_fractions, visible_head, tolerance=0.004):
        problems.append("The crop would cut off part of the streamer's head.")
    if face_panel["zoom"] > framing_settings.max_zoom:
        problems.append(f"The face would be enlarged {face_panel['zoom']}x (limit {framing_settings.max_zoom}x).")
    on_screen = output_rect(visible_head, face_panel["crop"], source_width, source_height,
                            panel_top, face_panel["height"])
    for box in text_boxes:
        overlap_width = min(box["right"], on_screen["right"]) - max(box["left"], on_screen["left"])
        overlap_height = min(box["bottom"], on_screen["bottom"]) - max(box["top"], on_screen["top"])
        if overlap_width > 0 and overlap_height > 0:
            face_area = max(1, (on_screen["right"] - on_screen["left"]) * (on_screen["bottom"] - on_screen["top"]))
            if overlap_width * overlap_height / face_area > 0.08:
                problems.append("Text would cover the streamer's face.")
                break
    total_height = 0
    for panel in plan["panels"]:
        total_height += panel["height"]
    if total_height != OUTPUT_HEIGHT:
        problems.append("The panels do not fill the 1080x1920 screen (empty bars).")
    return problems


def analyse_faces(source, start, end, sample_count=12):
    """
    Find the steady faces in a moment and a median still frame for border search.

    Args:
        source (str): The video file.
        start (float): Moment start, in seconds.
        end (float): Moment end, in seconds.
        sample_count (int): How many frames to look at.

    Returns:
        dict: {"available": bool, "steady": [...], "groups": [...], "frames": n,
               "median_gray": numpy array or None}.
    """
    detector = face_detector()
    if detector is None:
        return {"available": False, "steady": [], "groups": [], "frames": 0, "median_gray": None}
    import cv2

    faces_per_frame = []
    grays = []
    for time_seconds in sample_times(start, end, sample_count):
        frame = read_frame(source, time_seconds)
        if frame is None:
            continue
        faces_per_frame.append(faces_in_frame(detector, frame))
        grays.append(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY))
    groups = group_steady_faces(faces_per_frame)
    median_gray = None
    if grays:
        median_gray = np.median(np.stack(grays), axis=0).astype(np.uint8)
    return {"available": True, "steady": pick_streamer_faces(groups), "groups": groups,
            "frames": len(faces_per_frame), "median_gray": median_gray}


def rounded(values):
    """
    Round every number in a small dictionary to 4 decimals (for readable reports).

    Args:
        values (dict): Name → number.

    Returns:
        dict: The same names with rounded numbers.
    """
    result = {}
    for key, value in values.items():
        result[key] = round(value, 4)
    return result


def decide_framing(source, start, end, source_width, source_height, framing_settings,
                   category="", configured_box=None):
    """
    Full framing step for one clip: find faces, choose a layout.

    Args:
        source (str): The video file.
        start (float): Moment start.
        end (float): Moment end.
        source_width (int): Source width in pixels.
        source_height (int): Source height in pixels.
        framing_settings (FramingSettings): From the channel YAML.
        category (str): Moment type from the ranking.
        configured_box (FacecamBox or None): A hand-measured webcam box, if any.

    Returns:
        dict: The choice from choose_framing() plus "faces" (summary for the
              quality report) and "steady" (the face groups, for check_framing).
    """
    analysis = analyse_faces(source, start, end, framing_settings.sample_frames)
    if not analysis["available"]:
        return {"mode": "fit-blur", "reason": "Face detector unavailable; whole picture kept.",
                "steady": [], "faces": {"frames": 0}}
    steady = analysis["steady"]
    border = None
    if steady and analysis["median_gray"] is not None:
        border = find_camera_border(analysis["median_gray"], head_box(steady[0]["face"]))
    decision = choose_framing(steady, source_width, source_height, framing_settings, category,
                              configured_box, border)
    decision["steady"] = steady
    summary = {"frames": analysis["frames"], "steady_faces": len(steady),
               "other_faces": max(0, len(analysis["groups"]) - len(steady))}
    if steady:
        summary["main_face"] = rounded(steady[0]["face"])
        summary["main_face_share"] = steady[0]["share"]
    if border is not None:
        summary["camera_border"] = rounded(border)
    decision["faces"] = summary
    return decision


# ---------------------------------------------------------------------------
# Turning a decision into a checked plan (used by the renderer)
# ---------------------------------------------------------------------------

def text_box(center_y, height):
    """
    Describe the screen area a line of text will cover.

    Args:
        center_y (float): The text's middle height on screen.
        height (float): The text block's height.

    Returns:
        dict: {"left", "top", "right", "bottom"} in screen pixels.
    """
    from src.video.animation.drawing import SAFE_LEFT, SAFE_RIGHT

    return {"left": SAFE_LEFT, "right": SAFE_RIGHT, "top": center_y - height / 2, "bottom": center_y + height / 2}


def plan_for_decision(decision, source_width, source_height, layout_settings):
    """
    Build the layout plan that matches a framing decision.

    Args:
        decision (dict): From choose_framing() / decide_framing().
        source_width (int): Source width.
        source_height (int): Source height.
        layout_settings (LayoutSettings): Fit-blur look and gameplay focus.

    Returns:
        dict: A plan for clip_layout.build_video_filter().
    """
    from src.video.clip_layout import plan_face_crop, plan_face_square, plan_framed_split, plan_layout

    if decision["mode"] == "face-square":
        return plan_face_square(source_width, source_height, decision["focus_x"], layout_settings.foreground_center)
    if decision["mode"] == "split":
        return plan_framed_split(source_width, source_height, decision["camera_crop"],
                                 decision["gameplay_share"], layout_settings.gameplay_focus_x, True)
    if decision["mode"] == "face-crop":
        return plan_face_crop(source_width, source_height, decision["focus_x"])
    return plan_layout(source_width, source_height, "fit-blur", None,
                       foreground_center=layout_settings.foreground_center)


def head_on_screen(plan, steady, source_width, source_height):
    """
    Where the streamer's head appears on the phone screen in a plan.

    Args:
        plan (dict): A split/face-crop plan.
        steady (list[dict]): Steady faces.
        source_width (int): Source width.
        source_height (int): Source height.

    Returns:
        dict or None: Screen rectangle, or None for fit-blur / no face.
    """
    if plan["mode"] == "fit-blur" or not steady:
        return None
    panel_top = 0
    for panel in plan["panels"]:
        if panel["role"] in ("facecam", "picture"):
            return output_rect(head_box(steady[0]["face"]), panel["crop"], source_width, source_height,
                               panel_top, panel["height"])
        panel_top += panel["height"]
    return None


def framed_layout(decision, source_width, source_height, settings):
    """
    Turn a framing decision into a final plan and text positions, checked for safety.

    Text is moved below the head if it would cover the face. If any check
    still fails (cut-off head, extreme zoom, text on the face), the clip
    falls back to fit-blur, which shows the whole picture.

    Args:
        decision (dict): From decide_framing().
        source_width (int): Source width.
        source_height (int): Source height.
        settings (ClipChannelSettings): Layout, caption, hook and framing settings.

    Returns:
        dict: {"plan", "positions", "mode", "reason", "problems" (found before any
               fallback), "fallback" (bool), "faces"}.
    """
    from src.video.clip_layout import TEXT_BOTTOM_LIMIT, plan_layout, text_positions

    steady = decision.get("steady", [])
    plan = plan_for_decision(decision, source_width, source_height, settings.layout)
    positions = text_positions(plan)
    caption_height = settings.captions.font_size * 1.3
    hook_height = settings.hook_title.font_size * 1.15 * 2 + 50
    head = head_on_screen(plan, steady, source_width, source_height)
    if head is not None:
        caption_top = positions["caption_y"] - caption_height / 2
        if caption_top < head["bottom"] + 10 and positions["caption_y"] + caption_height / 2 > head["top"]:
            # Move captions (and the hook under them) below the chin.
            positions["caption_y"] = int(min(TEXT_BOTTOM_LIMIT, head["bottom"] + 20 + caption_height / 2))
            positions["hook_y"] = int(min(TEXT_BOTTOM_LIMIT, positions["caption_y"] + caption_height / 2
                                          + 20 + hook_height / 2))
    boxes = [text_box(positions["caption_y"], caption_height)]
    if settings.hook_title.seconds > 0:
        boxes.append(text_box(positions["hook_y"], hook_height))
    problems = check_framing(decision, plan, steady, source_width, source_height, boxes, settings.framing)
    result = {"plan": plan, "positions": positions, "mode": plan.get("variant", plan["mode"]),
              "reason": decision.get("reason", ""),
              "problems": problems, "fallback": False, "faces": decision.get("faces", {})}
    if problems:
        plan = plan_layout(source_width, source_height, "fit-blur", None,
                           foreground_center=settings.layout.foreground_center)
        result.update({"plan": plan, "positions": text_positions(plan), "mode": "fit-blur", "fallback": True,
                       "reason": "Safe fallback to the whole picture: " + "; ".join(problems)})
    return result

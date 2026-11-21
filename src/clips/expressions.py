# =============================================================
# src/clips/expressions.py — read faces: is someone dying laughing,
#                            shocked, crying or angry?
#
# Why: the TikTok study (docs/TIKTOK_CONTENT_RESEARCH.md) found that the
# clips which break out are carried by REACTIONS: someone losing it
# laughing, a hand over the mouth in shock, screaming at a near-miss.
# The sound model (audio_events.py) rarely hears those reactions under
# game sound and music, but they are plain to see on the face.
#
# How: find faces with the YuNet detector the project already uses
# (framing.py), then read each face's expression with FER+ (Microsoft's
# emotion model from the ONNX model zoo, MIT licence, 35 MB, CPU, free).
# FER+ gives a chance for eight expressions: neutral, happiness,
# surprise, sadness, anger, disgust, fear and contempt.
#
# Nothing here costs money or sends data anywhere.
# =============================================================

import hashlib
import os
from pathlib import Path

import numpy as np

MODEL_PATH = Path("models") / "expression" / "emotion-ferplus-8.onnx"
MODEL_URL = ("https://github.com/onnx/models/raw/main/validated/vision/body_analysis/"
             "emotion_ferplus/model/emotion-ferplus-8.onnx")
MODEL_SHA256 = "a2a2ba6a335a3b29c21acb6272f962bd3d47f84952aaffa03b60986e04efa61c"

# The model's eight outputs, in its own order.
EXPRESSIONS = ["neutral", "happiness", "surprise", "sadness", "anger", "disgust", "fear", "contempt"]

# The model looks at a 64x64 grey picture of one face.
FACE_SIZE = 64

# Extra room around the detector's face box (as a share of the box), so
# the whole face including the open mouth is inside the crop.
FACE_MARGIN = 0.15

# Faces smaller than this share of the frame width are too small to read.
MIN_FACE_WIDTH = 0.04

_model = None


def download_model():
    """
    Download the expression model once and check it is the expected file.

    Returns:
        bool: True when the model is ready.
    """
    import httpx

    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    partial = MODEL_PATH.with_suffix(".part")
    try:
        response = httpx.get(MODEL_URL, follow_redirects=True, timeout=120)
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


def expression_model():
    """
    Load the expression model (once per process).

    Returns:
        cv2.dnn.Net or None: The model, or None when OpenCV or the model file
        is unavailable (callers then simply have no expression evidence).
    """
    global _model
    if _model is not None:
        return _model
    try:
        import cv2
    except ImportError:
        return None
    if not MODEL_PATH.is_file() and not download_model():
        return None
    _model = cv2.dnn.readNetFromONNX(str(MODEL_PATH))
    return _model


def softmax(values):
    """
    Turn the model's raw scores into chances that add up to 1.

    Args:
        values (list[float]): Raw scores.

    Returns:
        list[float]: Chances between 0 and 1.

    Example:
        softmax([0, 0])   # [0.5, 0.5]
    """
    array = np.array(values, dtype=np.float64)
    array = np.exp(array - array.max())
    return list(array / array.sum())


def crop_face(frame, face):
    """
    Cut one face out of a picture as the grey 64x64 square the model needs.

    Args:
        frame: A BGR picture (height x width x 3).
        face (dict): {"x", "y", "w", "h"} as fractions of the frame (from
            framing.faces_in_frame).

    Returns:
        numpy array (64, 64) of float32 grey values 0-255, or None when the
        face is too small or outside the picture.
    """
    import cv2

    height, width = frame.shape[:2]
    if face["w"] < MIN_FACE_WIDTH:
        return None
    # Make a square around the face centre, a little bigger than the box.
    size = max(face["w"] * width, face["h"] * height) * (1 + 2 * FACE_MARGIN)
    centre_x = (face["x"] + face["w"] / 2) * width
    centre_y = (face["y"] + face["h"] / 2) * height
    left = int(max(0, centre_x - size / 2))
    top = int(max(0, centre_y - size / 2))
    right = int(min(width, centre_x + size / 2))
    bottom = int(min(height, centre_y + size / 2))
    if right - left < 16 or bottom - top < 16:
        return None
    grey = cv2.cvtColor(frame[top:bottom, left:right], cv2.COLOR_BGR2GRAY)
    return cv2.resize(grey, (FACE_SIZE, FACE_SIZE), interpolation=cv2.INTER_AREA).astype(np.float32)


def read_expression(face_picture):
    """
    Read the expression of one face.

    Args:
        face_picture: The 64x64 grey face from crop_face().

    Returns:
        dict or None: Expression name → chance (0-1), e.g.
        {"neutral": 0.1, "happiness": 0.85, "surprise": 0.03, ...};
        None when the model is unavailable.
    """
    model = expression_model()
    if model is None or face_picture is None:
        return None
    model.setInput(face_picture.reshape(1, 1, FACE_SIZE, FACE_SIZE))
    chances = softmax(model.forward()[0])
    result = {}
    for index, name in enumerate(EXPRESSIONS):
        result[name] = round(float(chances[index]), 3)
    return result


def frame_expressions(frame, max_faces=3):
    """
    Find the faces in one picture and read each one's expression.

    Args:
        frame: A BGR picture.
        max_faces (int): Read at most this many faces (the biggest ones).

    Returns:
        list[dict]: One dict per face: {"x", "y", "w", "h", "score"} from the
        face detector plus "expression" (see read_expression). Empty when no
        face is found or the models are unavailable.
    """
    from src.clips import framing

    detector = framing.face_detector()
    if detector is None:
        return []
    faces = framing.faces_in_frame(detector, frame)
    faces.sort(key=face_area, reverse=True)
    results = []
    for face in faces[:max_faces]:
        expression = read_expression(crop_face(frame, face))
        if expression is None:
            continue
        entry = dict(face)
        entry["expression"] = expression
        results.append(entry)
    return results


def face_area(face):
    """
    Return the size of a face box (used to sort the biggest faces first).

    Args:
        face (dict): {"w", "h"} as fractions.

    Returns:
        float: Width times height.
    """
    return face["w"] * face["h"]


def strongest_reaction(faces):
    """
    Pick the strongest reaction among the faces in one picture.

    Args:
        faces (list[dict]): From frame_expressions.

    Returns:
        dict: {"happiness": highest happiness chance, "surprise": highest
        surprise chance, "sadness", "anger", "fear": the same,
        "faces": how many faces were read}. All 0 when there is no face.

    Example:
        strongest_reaction([])   # {"happiness": 0.0, ..., "faces": 0}
    """
    summary = {"happiness": 0.0, "surprise": 0.0, "sadness": 0.0, "anger": 0.0, "fear": 0.0,
               "faces": len(faces)}
    for face in faces:
        for name in ("happiness", "surprise", "sadness", "anger", "fear"):
            summary[name] = max(summary[name], face["expression"].get(name, 0.0))
    return summary


def reaction_moments(per_frame, threshold=0.6, minimum_frames=2):
    """
    Find the moments where a face clearly shows a strong reaction.

    Args:
        per_frame (list[dict]): strongest_reaction() for each sampled picture,
            each with an added "time" (seconds).
        threshold (float): A reaction counts when its chance is at least this.
        minimum_frames (int): It must last this many pictures in a row, so a
            single misread picture does not count.

    Returns:
        list[dict]: {"kind": "happiness"/"surprise"/..., "start", "end",
        "peak"} for each reaction moment, in time order.

    Example:
        reaction_moments([{"time": 1, "happiness": 0.9, ...},
                          {"time": 1.5, "happiness": 0.8, ...}])
        # [{"kind": "happiness", "start": 1, "end": 1.5, "peak": 0.9}]
    """
    moments = []
    for kind in ("happiness", "surprise", "sadness", "anger", "fear"):
        run = []
        for sample in per_frame + [None]:
            if sample is not None and sample.get(kind, 0.0) >= threshold:
                run.append(sample)
                continue
            if len(run) >= minimum_frames:
                peak = 0.0
                for item in run:
                    peak = max(peak, item[kind])
                moments.append({"kind": kind, "start": run[0]["time"], "end": run[-1]["time"],
                                "peak": round(peak, 3)})
            run = []
    moments.sort(key=moment_start)
    return moments


def moment_start(moment):
    """
    Return when a reaction moment starts (used for sorting).

    Args:
        moment (dict): From reaction_moments.

    Returns:
        float: Start time in seconds.
    """
    return moment["start"]

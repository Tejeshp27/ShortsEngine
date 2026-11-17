# =============================================================
# src/video/clip_layout.py — where to cut a wide stream video so
#                            it fits a tall 1080x1920 phone screen
#
# Stream video is wide (16:9, e.g. 1920x1080). Phone video is tall
# (9:16). Two layouts:
#
#   split  (when the streamer's webcam position is in the YAML)
#          ┌──────────────┐
#          │   webcam     │  top panel (facecam_panel_height px)
#          ├──────────────┤
#          │   gameplay   │  bottom panel (the rest of the 1920 px)
#          └──────────────┘
#   full   (no webcam position): one tall slice from the middle of
#          the stream, the full height of the picture.
#
# Rules that keep faces from being cut off:
#   - The webcam crop always CONTAINS the whole webcam box from the
#     YAML; it only grows (never shrinks) to match the panel's shape,
#     so nothing is squashed or stretched.
#   - Every crop stays inside the picture.
#
# This file only does the maths (plain numbers in, numbers out), so
# it is easy to test. clip_render.py turns the result into FFmpeg
# instructions.
# =============================================================

OUTPUT_WIDTH = 1080
OUTPUT_HEIGHT = 1920


def round_to_even(number):
    """
    Round a size or position down to an even whole number.

    The H.264 video format stores colour for 2x2 blocks of pixels,
    so FFmpeg needs crop sizes and positions that are even numbers.

    Args:
        number (float): Any number of pixels.

    Returns:
        int: The nearest even number at or below it, e.g. 607.4 → 606.
    """
    return int(number) // 2 * 2


def keep_between(value, lowest, highest):
    """
    Limit a number to a range.

    Args:
        value (float): The number.
        lowest (float): The smallest allowed value.
        highest (float): The largest allowed value.

    Returns:
        float: value, moved inside [lowest, highest] if needed.
    """
    return max(lowest, min(value, highest))


def crop_box(center_x, center_y, min_width, min_height, shape, source_width, source_height):
    """
    Find the smallest crop of a given shape that contains a box.

    Args:
        center_x (float): Where the crop should be centred, in pixels.
        center_y (float): Same, vertically.
        min_width (float): The crop must be at least this wide (e.g. the webcam box).
        min_height (float): The crop must be at least this tall.
        shape (float): Width divided by height of the panel it will fill.
        source_width (int): The stream picture's width.
        source_height (int): The stream picture's height.

    Returns:
        dict: {"x", "y", "width", "height"} in pixels, all even, inside the picture.

    Example:
        crop_box(1680, 162, 480, 324, 1.5, 1920, 1080)
        # → a 486x324 crop around the top-right webcam
    """
    width = max(min_width, 2)
    height = width / shape
    if height < min_height:
        height = min_height
        width = height * shape

    # Too big for the picture? Shrink it, keeping its shape.
    if width > source_width:
        width = source_width
        height = width / shape
    if height > source_height:
        height = source_height
        width = height * shape

    # Centre it, then slide it back inside the picture if it sticks out.
    left = keep_between(center_x - width / 2, 0, source_width - width)
    top = keep_between(center_y - height / 2, 0, source_height - height)
    return {"x": round_to_even(left), "y": round_to_even(top),
            "width": round_to_even(width), "height": round_to_even(height)}


def full_height_slice(source_width, source_height, focus_x, panel_height):
    """
    Cut a slice the full height of the picture, shaped for one panel.

    Args:
        source_width (int): The picture's width.
        source_height (int): The picture's height.
        focus_x (float): 0 = left side, 0.5 = middle, 1 = right side.
        panel_height (int): The panel's height on the 1080-wide phone screen.

    Returns:
        dict: The crop {"x", "y", "width", "height"}.
    """
    shape = OUTPUT_WIDTH / panel_height
    return crop_box(source_width * focus_x, source_height / 2, 0, source_height,
                    shape, source_width, source_height)


def plan_layout(source_width, source_height, facecam, facecam_panel_height, focus_x):
    """
    Decide how to cut a stream video into the phone layout.

    Args:
        source_width (int): Stream picture width, e.g. 1920.
        source_height (int): Stream picture height, e.g. 1080.
        facecam (FacecamBox or None): Webcam position as fractions, or None.
        facecam_panel_height (int): Height of the webcam panel on the phone screen.
        focus_x (float): Which part of the gameplay to keep (0 to 1).

    Returns:
        dict: {"mode": "split" or "full",
               "panels": [{"crop": {...}, "height": pixels}, ...] top to bottom}
              The panel heights always add up to 1920.
    """
    if facecam is None:
        crop = full_height_slice(source_width, source_height, focus_x, OUTPUT_HEIGHT)
        return {"mode": "full", "panels": [{"crop": crop, "height": OUTPUT_HEIGHT}]}

    top_height = round_to_even(facecam_panel_height)
    bottom_height = OUTPUT_HEIGHT - top_height

    box_width = facecam.width * source_width
    box_height = facecam.height * source_height
    box_center_x = (facecam.x + facecam.width / 2) * source_width
    box_center_y = (facecam.y + facecam.height / 2) * source_height
    camera_crop = crop_box(box_center_x, box_center_y, box_width, box_height,
                           OUTPUT_WIDTH / top_height, source_width, source_height)
    gameplay_crop = full_height_slice(source_width, source_height, focus_x, bottom_height)

    return {"mode": "split", "panels": [
        {"crop": camera_crop, "height": top_height},
        {"crop": gameplay_crop, "height": bottom_height},
    ]}


def crop_and_scale(crop, panel_height):
    """
    Write one panel's FFmpeg instructions: cut out the crop, resize it.

    Args:
        crop (dict): {"x", "y", "width", "height"}.
        panel_height (int): The panel's height on the phone screen.

    Returns:
        str: For example "crop=486:324:1434:0,scale=1080:720,setsar=1".
             crop=W:H:X:Y cuts out a W-by-H rectangle whose top-left corner is X,Y.
             scale=W:H resizes it to fill the panel exactly.
             setsar=1 marks the pixels as square so players don't stretch it.
    """
    return (f"crop={crop['width']}:{crop['height']}:{crop['x']}:{crop['y']},"
            f"scale={OUTPUT_WIDTH}:{panel_height},setsar=1")


def build_video_filter(plan, fps):
    """
    Turn a layout plan into an FFmpeg "filter graph" (a recipe of video steps).

    Args:
        plan (dict): From plan_layout().
        fps (int): Frames per second of the output.

    Returns:
        str: The filter text for FFmpeg's -filter_complex option. Its
             result is called [out]: raw RGB frames, 1080x1920.

    Example (split):
        [0:v]split=2[cam][game];
        [cam]crop=...,scale=1080:720,setsar=1[top];
        [game]crop=...,scale=1080:1200,setsar=1[bottom];
        [top][bottom]vstack=inputs=2,fps=30,format=rgb24[out]
    """
    # fps=N makes every clip the same frame rate (streams can be 60 fps).
    # format=rgb24 gives Python plain red/green/blue pixels to draw on.
    finish = f"fps={fps},format=rgb24[out]"

    if plan["mode"] == "full":
        panel = plan["panels"][0]
        return f"[0:v]{crop_and_scale(panel['crop'], panel['height'])},{finish}"

    top = plan["panels"][0]
    bottom = plan["panels"][1]
    # split=2 makes two copies of the input video, one per panel.
    # vstack=inputs=2 stacks the two panels on top of each other.
    return (
        "[0:v]split=2[cam][game];"
        f"[cam]{crop_and_scale(top['crop'], top['height'])}[top];"
        f"[game]{crop_and_scale(bottom['crop'], bottom['height'])}[bottom];"
        f"[top][bottom]vstack=inputs=2,{finish}"
    )

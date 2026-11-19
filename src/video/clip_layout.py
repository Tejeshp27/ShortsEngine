# =============================================================
# src/video/clip_layout.py — how a wide stream picture is fitted
#                            to a tall 1080x1920 phone screen
#
# Stream video is wide (16:9, e.g. 1920x1080). Phone video is tall
# (9:16). Three layouts, chosen with `layout.mode` in the channel YAML:
#
#   fit-blur (default)        split                center-crop
#   ┌──────────────┐     ┌──────────────┐     ┌──────────────┐
#   │ blurred copy │     │              │     │  a tall      │
#   │┌────────────┐│     │  gameplay    │     │  slice from  │
#   ││ the WHOLE  ││     │  (~60%)      │     │  the middle  │
#   ││ picture    ││     ├──────────────┤     │  (the sides  │
#   │└────────────┘│     │  facecam     │     │  are lost)   │
#   │ blurred copy │     │  (~40%)      │     │              │
#   └──────────────┘     └──────────────┘     └──────────────┘
#
# fit-blur never cuts anything off. split needs the streamer's
# facecam position in the YAML. center-crop is kept as an option.
#
# split can also put the facecam on top and the gameplay below
# (layout.split_facecam_position: top), which keeps the face clear of
# the app's caption and buttons at the bottom of the screen.
#
# Quality rules:
#   - Every resize uses the "lanczos" method (the sharpest standard one),
#     with exact rounding and full-resolution colour (SCALE_FLAGS).
#   - A light sharpen (unsharp=5:5:0.6) is added ONLY to a panel that is
#     being enlarged, because enlarging softens the picture. A picture
#     that is being shrunk is already sharp and is left alone.
#   - A panel enlarged 2x or more (a small webcam) first gets a very light
#     clean-up of compression noise (hqdn3d), so the enlargement does not
#     magnify it, and then a slightly stronger sharpen.
#
# This file only does maths and builds FFmpeg filter text (plain
# values in, values out), so it is easy to test. clip_render.py runs it.
# =============================================================

OUTPUT_WIDTH = 1080
OUTPUT_HEIGHT = 1920

# How far the facecam crop stays inside the camera box on each side.
FACECAM_INSET = 0.02

# Sharpen settings: 5x5 pixel area, strength 0.6 (light).
SHARPEN_FILTER = "unsharp=5:5:0.6"

# Resize method: lanczos, with exact rounding (accurate_rnd) and colour
# scaled at full resolution (full_chroma_int) for cleaner colour edges.
SCALE_FLAGS = "lanczos+accurate_rnd+full_chroma_int"

# Panels enlarged at least this much (e.g. a 386-pixel webcam filling the
# 1080-pixel width, 2.8x) get the clean-up and the stronger sharpen.
HEAVY_ENLARGE = 2.0
CLEANUP_FILTER = "hqdn3d=1.5:1.5:3:3"
STRONG_SHARPEN_FILTER = "unsharp=5:5:0.9"

# The fit-blur background is blurred at a quarter of the size and then
# enlarged. Blurring a small picture is about 16x less work than blurring
# the full 1080x1920 one, and a blurred picture loses nothing when enlarged.
BLUR_WIDTH = 270
BLUR_HEIGHT = 480

# Text positions on the 1920-pixel-tall screen. The bottom ~20% (below
# about y=1536) is covered by TikTok/YouTube's caption and buttons, and
# the top ~11% by their tabs, so text stays between these lines.
TEXT_TOP_LIMIT = 300
TEXT_BOTTOM_LIMIT = 1440
# center-crop keeps the older fixed positions.
CENTER_CROP_HOOK_Y = 520
CENTER_CROP_CAPTION_Y = 1330


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


def inner_crop(box_x, box_y, box_width, box_height, shape, inset_fraction):
    """
    Find the largest crop of a given shape that fits INSIDE a box.

    Used for the facecam: a stream's camera usually has a border or other
    overlay right around it, so the crop stays a little inside the camera
    box instead of reaching outside it. Trimming a few pixels of wall at
    the edges is better than showing a stray border line.

    Args:
        box_x (float): The box's left edge, in pixels.
        box_y (float): The box's top edge, in pixels.
        box_width (float): The box's width, in pixels.
        box_height (float): The box's height, in pixels.
        shape (float): Width divided by height of the panel it will fill.
        inset_fraction (float): How much to stay away from each edge, e.g. 0.02 = 2%.

    Returns:
        dict: {"x", "y", "width", "height"} in pixels, all even, centred in the box.

    Example:
        inner_crop(1492, 604, 428, 286, 1.40625, 0.02)
        # → about 394 x 280 pixels, centred in the camera box
    """
    usable_width = box_width * (1 - 2 * inset_fraction)
    usable_height = box_height * (1 - 2 * inset_fraction)
    width = usable_width
    height = width / shape
    if height > usable_height:
        height = usable_height
        width = height * shape
    left = box_x + (box_width - width) / 2
    top = box_y + (box_height - height) / 2
    return {"x": round_to_even(left), "y": round_to_even(top),
            "width": round_to_even(width), "height": round_to_even(height)}


def is_enlarged(crop_width, output_width):
    """
    Check whether a picture is made bigger than it really is.

    Args:
        crop_width (int): How many source pixels wide the picture is.
        output_width (int): How many pixels wide it becomes on the phone screen.

    Returns:
        bool: True when the picture is enlarged (and so needs sharpening).
    """
    return output_width > crop_width


def make_panel(crop, height, role="picture"):
    """
    Describe one picture panel of a layout.

    Args:
        crop (dict): The part of the source picture to use.
        height (int): The panel's height on the phone screen (its width is 1080).
        role (str): What the panel shows: "gameplay", "facecam" or "picture".

    Returns:
        dict: {"crop", "height", "enlarged", "zoom", "role"}; zoom is how many
              times bigger the picture becomes (2.8 means 2.8x).
    """
    return {"crop": crop, "height": height, "enlarged": is_enlarged(crop["width"], OUTPUT_WIDTH),
            "zoom": round(OUTPUT_WIDTH / crop["width"], 2), "role": role}


def plan_fit_blur(source_width, source_height, foreground_center):
    """
    Plan the fit-blur layout: the whole picture, uncropped, on a blurred copy.

    Args:
        source_width (int): Stream picture width, e.g. 1920.
        source_height (int): Stream picture height, e.g. 1080.
        foreground_center (float): Where the picture's middle sits, as a
                                   fraction of the screen height (0.5 = centre).

    Returns:
        dict: {"mode": "fit-blur", "foreground": {"width", "height", "top", "enlarged"}}.

    Example:
        plan_fit_blur(1920, 1080, 0.45)["foreground"]
        # → {"width": 1080, "height": 606, "top": 560, "enlarged": False}
    """
    height = round_to_even(OUTPUT_WIDTH * source_height / source_width)
    height = min(height, OUTPUT_HEIGHT)
    top = round_to_even(OUTPUT_HEIGHT * foreground_center - height / 2)
    top = int(keep_between(top, 0, OUTPUT_HEIGHT - height))
    return {"mode": "fit-blur", "foreground": {
        "width": OUTPUT_WIDTH, "height": height, "top": top,
        "enlarged": is_enlarged(source_width, OUTPUT_WIDTH),
    }}


def plan_split(source_width, source_height, facecam, gameplay_share, focus_x, facecam_on_top=False):
    """
    Plan the split layout: gameplay and the facecam crop, one above the other.

    Args:
        source_width (int): Stream picture width.
        source_height (int): Stream picture height.
        facecam (FacecamBox): Webcam position as fractions of the picture.
        gameplay_share (float): Share of the screen height for gameplay, e.g. 0.6.
        focus_x (float): Which part of the gameplay to keep (0 to 1).
        facecam_on_top (bool): True puts the facecam above the gameplay.

    Returns:
        dict: {"mode": "split", "panels": [top panel, bottom panel]}; each
              panel's "role" says whether it is the gameplay or the facecam.
              The two panel heights always add up to 1920.
    """
    gameplay_height = round_to_even(OUTPUT_HEIGHT * gameplay_share)
    facecam_height = OUTPUT_HEIGHT - gameplay_height

    gameplay_crop = full_height_slice(source_width, source_height, focus_x, gameplay_height)
    # The webcam crop stays just inside the webcam box (see inner_crop), so
    # the stream's overlay border never shows; the face itself is centred.
    camera_crop = inner_crop(facecam.x * source_width, facecam.y * source_height,
                             facecam.width * source_width, facecam.height * source_height,
                             OUTPUT_WIDTH / facecam_height, FACECAM_INSET)
    gameplay = make_panel(gameplay_crop, gameplay_height, "gameplay")
    camera = make_panel(camera_crop, facecam_height, "facecam")
    if facecam_on_top:
        return {"mode": "split", "panels": [camera, gameplay]}
    return {"mode": "split", "panels": [gameplay, camera]}


def plan_center_crop(source_width, source_height, focus_x):
    """
    Plan the center-crop layout: one full-height 9:16 slice of the picture.

    Args:
        source_width (int): Stream picture width.
        source_height (int): Stream picture height.
        focus_x (float): Which part of the picture to keep (0 to 1).

    Returns:
        dict: {"mode": "center-crop", "panels": [one panel]}.
    """
    crop = full_height_slice(source_width, source_height, focus_x, OUTPUT_HEIGHT)
    return {"mode": "center-crop", "panels": [make_panel(crop, OUTPUT_HEIGHT)]}


def plan_layout(source_width, source_height, mode, facecam=None, gameplay_share=0.6,
                focus_x=0.5, foreground_center=0.45, facecam_on_top=False):
    """
    Decide how to fit a stream picture onto the phone screen.

    Args:
        source_width (int): Stream picture width, e.g. 1920.
        source_height (int): Stream picture height, e.g. 1080.
        mode (str): "fit-blur", "split" or "center-crop".
        facecam (FacecamBox or None): Webcam position; "split" needs one.
        gameplay_share (float): Gameplay's share of the height in "split".
        focus_x (float): Which part of a cropped picture to keep (0 to 1).
        foreground_center (float): Vertical position of the picture in "fit-blur".
        facecam_on_top (bool): In "split", put the facecam above the gameplay.

    Returns:
        dict: A plan with a "mode" key ("split" without a facecam becomes
              "fit-blur", and the plan then has "note" explaining why).
    """
    if mode == "split" and facecam is None:
        plan = plan_fit_blur(source_width, source_height, foreground_center)
        plan["note"] = "Split layout needs a facecam box for this streamer; used fit-blur instead."
        return plan
    if mode == "split":
        return plan_split(source_width, source_height, facecam, gameplay_share, focus_x, facecam_on_top)
    if mode == "center-crop":
        return plan_center_crop(source_width, source_height, focus_x)
    return plan_fit_blur(source_width, source_height, foreground_center)


def crop_and_scale(panel):
    """
    Write one panel's FFmpeg instructions: cut out the crop, resize, sharpen if enlarged.

    Args:
        panel (dict): From make_panel(): {"crop", "height", "enlarged"}.

    Returns:
        str: For example "crop=1012:1080:454:0,scale=1080:1152:flags=lanczos,unsharp=5:5:0.6,setsar=1".
             crop=W:H:X:Y cuts out a W-by-H rectangle whose top-left corner is X,Y.
             scale=W:H resizes it to fill the panel exactly.
             setsar=1 marks the pixels as square so players don't stretch it.
    """
    crop = panel["crop"]
    heavy = panel["enlarged"] and OUTPUT_WIDTH / crop["width"] >= HEAVY_ENLARGE
    steps = f"crop={crop['width']}:{crop['height']}:{crop['x']}:{crop['y']},"
    if heavy:
        steps += CLEANUP_FILTER + ","
    steps += f"scale={OUTPUT_WIDTH}:{panel['height']}:flags={SCALE_FLAGS}"
    if heavy:
        steps += "," + STRONG_SHARPEN_FILTER
    elif panel["enlarged"]:
        steps += "," + SHARPEN_FILTER
    return steps + ",setsar=1"


def fit_blur_filter(plan, blur, darken):
    """
    Build the fit-blur picture steps (before the frame-rate and colour steps).

    Args:
        plan (dict): From plan_fit_blur().
        blur (int): Blur strength (boxblur radius on the small background).
        darken (float): How much to darken the background (0 to 0.5).

    Returns:
        str: FFmpeg filter text ending in the combined picture.
    """
    foreground = plan["foreground"]
    front = f"scale={foreground['width']}:{foreground['height']}:flags={SCALE_FLAGS}"
    if foreground["enlarged"]:
        front += "," + SHARPEN_FILTER
    # Background: fill the screen (cutting the sides), shrink, blur,
    # darken a little (eq=brightness), then enlarge back to full size.
    back = (f"scale={BLUR_WIDTH}:{BLUR_HEIGHT}:force_original_aspect_ratio=increase:flags=lanczos,"
            f"crop={BLUR_WIDTH}:{BLUR_HEIGHT},boxblur={blur}:2,eq=brightness=-{darken:g},"
            f"scale={OUTPUT_WIDTH}:{OUTPUT_HEIGHT}:flags=lanczos")
    return (f"split=2[bg][fg];[bg]{back}[back];[fg]{front}[front];"
            f"[back][front]overlay=0:{foreground['top']},setsar=1")


def build_video_filter(plan, fps, blur=20, darken=0.12):
    """
    Turn a layout plan into an FFmpeg "filter graph" (a recipe of video steps).

    All picture work happens in this one FFmpeg run; the result goes to
    Python as uncompressed frames and is compressed only once, at the end.

    Args:
        plan (dict): From plan_layout().
        fps (int): Frames per second of the output.
        blur (int): fit-blur background blur strength.
        darken (float): fit-blur background darkening.

    Returns:
        str: The filter text for FFmpeg's -filter_complex option. Its
             result is called [out]: raw RGB frames, 1080x1920.

    Example (split):
        [0:v]fps=60,split=2[game][cam];
        [game]crop=...,scale=1080:1152:flags=lanczos,unsharp=5:5:0.6,setsar=1[top];
        [cam]crop=...,scale=1080:768:flags=lanczos,unsharp=5:5:0.6,setsar=1[bottom];
        [top][bottom]vstack=inputs=2,format=rgb24[out]
    """
    # fps=N first, so no work is spent on frames that would be dropped.
    # format=rgb24 gives Python plain red/green/blue pixels to draw on.
    start = f"[0:v]fps={fps},"
    finish = "format=rgb24[out]"

    if plan["mode"] == "fit-blur":
        return start + fit_blur_filter(plan, blur, darken) + "," + finish

    if plan["mode"] == "center-crop":
        return start + crop_and_scale(plan["panels"][0]) + "," + finish

    upper = plan["panels"][0]
    lower = plan["panels"][1]
    names = {"gameplay": "game", "facecam": "cam"}
    first = names[upper["role"]]
    second = names[lower["role"]]
    # split=2 makes two copies of the input video, one per panel.
    # vstack=inputs=2 stacks the two panels on top of each other.
    return (
        start + f"split=2[{first}][{second}];"
        f"[{first}]{crop_and_scale(upper)}[top];"
        f"[{second}]{crop_and_scale(lower)}[bottom];"
        f"[top][bottom]vstack=inputs=2,{finish}"
    )


def text_positions(plan):
    """
    Decide where the hook title and captions go for a layout.

    fit-blur: the hook sits just above the picture and the captions just
    below it, on the blurred background, so no gameplay is covered.
    split with the facecam below: the hook sits in the upper gameplay and
    the captions just below the line between gameplay and facecam: clear of
    the game's own subtitles and above the face.
    split with the facecam on top: the hook sits across the line between
    face and gameplay (under the chin, never over the face), and the
    captions in the lower gameplay, above the app's buttons.

    Args:
        plan (dict): From plan_layout().

    Returns:
        dict: {"hook_y": pixels, "caption_y": pixels} (centre heights),
              always inside the area the app's buttons and tabs don't cover.
    """
    if plan["mode"] == "fit-blur":
        foreground = plan["foreground"]
        hook_y = foreground["top"] - 150
        caption_y = foreground["top"] + foreground["height"] + 110
    elif plan["mode"] == "split" and plan["panels"][0]["role"] == "facecam":
        seam = plan["panels"][0]["height"]
        hook_y = seam + 40
        caption_y = seam + (TEXT_BOTTOM_LIMIT - seam) * 0.6
    elif plan["mode"] == "split":
        seam = plan["panels"][0]["height"]
        hook_y = 440
        caption_y = seam + 70
    else:
        hook_y = CENTER_CROP_HOOK_Y
        caption_y = CENTER_CROP_CAPTION_Y
    return {"hook_y": int(keep_between(hook_y, TEXT_TOP_LIMIT + 100, TEXT_BOTTOM_LIMIT)),
            "caption_y": int(keep_between(caption_y, TEXT_TOP_LIMIT, TEXT_BOTTOM_LIMIT))}

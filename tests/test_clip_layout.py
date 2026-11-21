# =============================================================
# Offline tests for the three phone layouts (fit-blur, split,
# center-crop): exact FFmpeg filter text, lanczos resizing,
# sharpening only when enlarging, and safe text positions.
# =============================================================

import pytest

from src.clips.demo import demo_config
from src.clips.settings import FacecamBox, load_clip_settings
from src.video import clip_layout
from src.video.clip_layout import build_video_filter, plan_layout, text_positions

JASON_CAMERA = FacecamBox(x=0.77734375, y=0.55972222, width=0.22265625, height=0.26527778)


def test_fit_blur_is_the_default_layout():
    """A channel that says nothing about layout gets fit-blur."""
    assert load_clip_settings(demo_config()).layout.mode == "fit-blur"
    assert load_clip_settings({"type": "clips"}).layout.mode == "fit-blur"


def test_fit_blur_keeps_the_whole_1080p_picture_without_sharpening():
    """1920x1080 shrinks to 1080x606 (uncropped), sits slightly above centre, and is not sharpened."""
    plan = plan_layout(1920, 1080, "fit-blur", foreground_center=0.45)
    assert plan["foreground"] == {"width": 1080, "height": 606, "top": 560, "enlarged": False}
    text = build_video_filter(plan, 60, blur=20, darken=0.12)
    assert text == (
        "[0:v]fps=60,split=2[bg][fg];"
        "[bg]scale=270:480:force_original_aspect_ratio=increase:flags=lanczos,crop=270:480,"
        "boxblur=20:2,eq=brightness=-0.12,scale=1080:1920:flags=lanczos[back];"
        "[fg]scale=1080:606:flags=lanczos+accurate_rnd+full_chroma_int[front];"
        "[back][front]overlay=0:560,setsar=1,format=rgb24[out]"
    )
    assert "unsharp" not in text and "crop=1080" not in text


def test_fit_blur_sharpens_only_an_enlarged_small_source():
    """A 960-wide source is enlarged to 1080 wide, so the foreground (only) gets the light sharpen."""
    plan = plan_layout(960, 540, "fit-blur")
    assert plan["foreground"]["enlarged"]
    text = build_video_filter(plan, 30)
    assert "[fg]scale=1080:606:flags=lanczos+accurate_rnd+full_chroma_int,unsharp=5:5:0.6[front]" in text
    assert text.count("unsharp") == 1


def test_split_puts_gameplay_on_top_and_facecam_below():
    """Split: a 1080x1152 gameplay panel above a 1080x768 facecam panel, both lanczos, both sharpened (enlarged)."""
    plan = plan_layout(1920, 1080, "split", JASON_CAMERA, gameplay_share=0.6, focus_x=0.5)
    gameplay, camera = plan["panels"]
    assert (gameplay["height"], camera["height"]) == (1152, 768)
    assert gameplay["crop"] == {"x": 452, "y": 0, "width": 1012, "height": 1080}
    # Jason's camera box is x 1492-1920, y 604-891 at 1080p; the crop stays inside it.
    assert camera["crop"]["x"] >= 1492 and camera["crop"]["x"] + camera["crop"]["width"] <= 1920
    assert camera["crop"]["y"] >= 604 and camera["crop"]["y"] + camera["crop"]["height"] <= 892
    text = build_video_filter(plan, 60)
    assert text.startswith("[0:v]fps=60,split=2[game][cam];")
    assert "[game]crop=1012:1080:452:0,scale=1080:1152:flags=lanczos+accurate_rnd+full_chroma_int,unsharp=5:5:0.6,setsar=1[top];" in text
    # The 2.8x webcam enlargement gets the light clean-up and the stronger sharpen.
    assert ("hqdn3d=1.5:1.5:3:3,scale=1080:768:flags=lanczos+accurate_rnd+full_chroma_int,"
            "unsharp=5:5:0.9,setsar=1[bottom];") in text
    assert text.endswith("[top][bottom]vstack=inputs=2,format=rgb24[out]")


def test_split_can_put_the_facecam_on_top():
    """Facecam on top: the camera panel comes first, and text stays off the face and above the app buttons."""
    plan = plan_layout(1920, 1080, "split", JASON_CAMERA, gameplay_share=0.6, facecam_on_top=True)
    camera, gameplay = plan["panels"]
    assert (camera["role"], gameplay["role"]) == ("facecam", "gameplay")
    assert (camera["height"], gameplay["height"]) == (768, 1152)
    text = build_video_filter(plan, 60)
    assert text.startswith("[0:v]fps=60,split=2[cam][game];[cam]crop=")
    assert text.endswith("[top][bottom]vstack=inputs=2,format=rgb24[out]")
    positions = text_positions(plan)
    # Research (docs/VIRAL_CONTENT_RESEARCH.md): captions sit on the seam between
    # face and gameplay; the hook sits just below them in the gameplay.
    assert 768 < positions["hook_y"] < positions["caption_y"] <= clip_layout.TEXT_BOTTOM_LIMIT
    assert positions["hook_y"] - 768 < 150   # the context line sits just under the face


def test_facecam_position_setting_is_checked():
    """split_facecam_position accepts top or bottom only."""
    config = demo_config()
    config["layout"] = {"mode": "split", "split_facecam_position": "top"}
    assert load_clip_settings(config).layout.split_facecam_position == "top"
    config["layout"]["split_facecam_position"] = "left"
    with pytest.raises(ValueError):
        load_clip_settings(config)


def test_center_crop_is_still_available():
    """center-crop cuts one full-height 9:16 slice and enlarges it (so it is sharpened)."""
    plan = plan_layout(1920, 1080, "center-crop")
    text = build_video_filter(plan, 30)
    assert text == ("[0:v]fps=30,crop=606:1080:656:0,scale=1080:1920:flags=lanczos+accurate_rnd+full_chroma_int,"
                    "unsharp=5:5:0.6,setsar=1,format=rgb24[out]")


def test_inner_crop_matches_the_panel_shape_and_stays_inside():
    """The facecam crop has the panel's shape, is centred, and keeps a 2% margin from the box edges."""
    crop = clip_layout.inner_crop(1000, 400, 400, 300, 1080 / 768, 0.02)
    assert abs(crop["width"] / crop["height"] - 1080 / 768) < 0.02
    assert crop["x"] >= 1000 + 0.02 * 400 - 2 and crop["x"] + crop["width"] <= 1400
    assert crop["y"] >= 400 and crop["y"] + crop["height"] <= 700
    for value in crop.values():
        assert value % 2 == 0


def test_a_shrinking_panel_is_never_sharpened():
    """A crop wider than 1080 source pixels is shrunk, so no sharpen is added."""
    panel = clip_layout.make_panel({"x": 0, "y": 0, "width": 2160, "height": 3840}, 1920)
    assert not panel["enlarged"]
    assert "unsharp" not in clip_layout.crop_and_scale(panel)


@pytest.mark.parametrize("mode, camera", [("fit-blur", None), ("split", JASON_CAMERA), ("center-crop", None)])
def test_text_stays_out_of_the_app_buttons(mode, camera):
    """Hook and captions stay below the top tabs and above the bottom ~20% covered by the app."""
    positions = text_positions(plan_layout(1920, 1080, mode, camera))
    for y in positions.values():
        assert clip_layout.TEXT_TOP_LIMIT <= y <= clip_layout.TEXT_BOTTOM_LIMIT < 1920 * 0.8


def test_fit_blur_text_frames_the_picture_instead_of_covering_it():
    """In fit-blur the hook is above the picture and the captions below it."""
    plan = plan_layout(1920, 1080, "fit-blur")
    positions = text_positions(plan)
    top = plan["foreground"]["top"]
    bottom = top + plan["foreground"]["height"]
    assert positions["hook_y"] < top and positions["caption_y"] > bottom


@pytest.mark.parametrize("mode", ["fit-blur", "split", "center-crop"])
def test_layout_mode_parses_and_bad_names_are_refused(mode):
    """The three layout names load from YAML; anything else is refused."""
    config = demo_config()
    config["layout"] = {"mode": mode}
    assert load_clip_settings(config).layout.mode == mode
    config["layout"] = {"mode": "zoom"}
    with pytest.raises(ValueError):
        load_clip_settings(config)

# =============================================================
# Tests for the clips channel (src/clips/, L-series).
#
# Covers the three rules the clips channel depends on:
#   1. Permission: an unapproved streamer is always refused.
#   2. Duplicates: running the finder twice never downloads twice.
#   3. Moment picking: whatever the AI answers, the moment stays
#      inside the clip and the length limits, and the hook title
#      stays short.
# Plus the layout maths, captions, the post caption, the TikTok
# draft upload and the dashboard's "re-cut" form.
#
# Everything runs offline: Twitch, Whisper and the AI are replaced
# by small fakes. The two render tests use FFmpeg (skipped if absent).
# =============================================================

import json
import os
import shutil
import subprocess
from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import yaml

from src.clips import finder, maker, moments, store, twitch_login
from src.clips.settings import (
    ClipChannelSettings,
    ClipLimits,
    FacecamBox,
    StreamerNotApproved,
    approved_streamers_to_use,
    find_approved_streamer,
    load_clip_settings,
)
from src.clips.transcribe import words_from_whisper_json
from src.storage import database
from src.storage.database import create_tables
from src.storage.queries import get_video_by_id
from src.video import clip_render
from src.video.clip_layout import build_video_filter, plan_layout

HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def channel_config(streamers=None):
    """Return a minimal clips channel config with one approved streamer by default."""
    if streamers is None:
        streamers = [{
            "username": "CoolStreamer",
            "permission_note": "Said yes in a Twitch DM",
            "permission_date": "2026-11-01",
            "facecam": {"x": 0.75, "y": 0.0, "width": 0.25, "height": 0.3},
        }, {
            "username": "kickstar",
            "platform": "kick",
            "permission_note": "Agreed on Discord",
            "permission_date": "2026-11-02",
        }]
    return {
        "name": "Test Clips",
        "type": "clips",
        "niche": "Stream highlights",
        "audience": "Gamers",
        "models": {"clip_pick": "claude-haiku-4-5"},
        "llm": {"max_calls_per_run": 5, "clip_pick_max_tokens": 300},
        "streamers": streamers,
        "publishing": {"hashtags": ["#twitch", "#clips"]},
        "platforms": ["tiktok"],
    }


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """Use a temporary folder and database so no real files are touched."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("SESSION_SECRET", "clips-test-secret-that-is-at-least-32-characters")
    monkeypatch.delenv("ACCOUNT_TOKEN_KEY", raising=False)
    create_tables()
    return tmp_path


# ---------------------------------------------------------------------------
# 1. Permission check
# ---------------------------------------------------------------------------

def test_unapproved_streamer_is_refused_with_a_clear_message():
    """A streamer who is not on the list is refused, and told how to fix it."""
    settings = load_clip_settings(channel_config())
    with pytest.raises(StreamerNotApproved) as error:
        find_approved_streamer(settings, "randomperson", "twitch_clips")
    message = str(error.value)
    assert "randomperson" in message and "channels/twitch_clips.yaml" in message
    assert "permission" in message


def test_approval_ignores_upper_and_lower_case():
    """Twitch logins are not case-sensitive."""
    settings = load_clip_settings(channel_config())
    assert find_approved_streamer(settings, "coolstreamer").username == "CoolStreamer"


def test_empty_approved_list_is_refused():
    """With nobody approved, a run stops instead of doing nothing silently."""
    settings = load_clip_settings(channel_config(streamers=[]))
    with pytest.raises(ValueError, match="No approved streamers"):
        approved_streamers_to_use(settings)


@pytest.mark.parametrize("change, expected", [
    ({"permission_date": (date.today() + timedelta(days=3)).isoformat()}, "in the future"),
    ({"permission_note": ""}, "permission_note"),
    ({"facecam": {"x": 0.9, "y": 0, "width": 0.25, "height": 0.3}}, "past the edge"),
    ({"username": "bad name!"}, "username"),
])
def test_bad_approval_entries_are_refused(change, expected):
    """A future date, empty note, impossible webcam box or bad login stops with a message."""
    entry = {"username": "ok_name", "permission_note": "Said yes in a DM",
             "permission_date": "2026-11-01"}
    entry.update(change)
    with pytest.raises(ValueError, match=expected):
        load_clip_settings(channel_config(streamers=[entry]))


def test_same_streamer_listed_twice_is_refused():
    """Two entries for one person would make the permission unclear."""
    entry = {"username": "dup", "permission_note": "Said yes", "permission_date": "2026-11-01"}
    second = dict(entry, username="DUP")
    with pytest.raises(ValueError, match="listed twice"):
        load_clip_settings(channel_config(streamers=[entry, second]))


def test_news_channel_is_not_a_clips_channel():
    """Clip code refuses a channel without `type: clips`."""
    with pytest.raises(ValueError, match="not a clips channel"):
        load_clip_settings({"name": "News"})


def test_finder_refuses_unapproved_streamer_before_calling_twitch(workspace, monkeypatch):
    """No Twitch request is made for someone who is not approved."""
    monkeypatch.setattr(finder.twitch, "get_app_token", Mock(side_effect=AssertionError("called Twitch")))
    with pytest.raises(StreamerNotApproved):
        finder.find_and_download("twitch_clips", channel_config(), only_streamer="stranger")


def test_render_refuses_streamer_removed_from_the_list():
    """Permission is checked again at render time, in case it was withdrawn."""
    settings = load_clip_settings(channel_config())
    clip = {"source": "twitch", "streamer": "someone_removed", "platform": "twitch"}
    with pytest.raises(StreamerNotApproved):
        maker.facecam_for(clip, settings)


def test_real_channel_file_is_valid():
    """channels/twitch_clips.yaml itself loads without errors."""
    with open("channels/twitch_clips.yaml", encoding="utf-8") as file:
        config = yaml.safe_load(file)
    settings = load_clip_settings(config)
    assert settings.clips.moment_min_seconds == 20
    assert settings.clips.moment_max_seconds == 60


# ---------------------------------------------------------------------------
# 2. Finding, downloading and the duplicate check
# ---------------------------------------------------------------------------

def fake_twitch_clip(clip_id, duration, views):
    """Build a clip object shaped like Twitch's Get Clips answer."""
    return {"id": clip_id, "url": f"https://clips.twitch.tv/{clip_id}", "title": f"Clip {clip_id}",
            "view_count": views, "created_at": "2026-11-17T01:00:00Z", "duration": duration}


@pytest.fixture
def fake_twitch(workspace, monkeypatch):
    """Replace every Twitch call with a fake; count the downloads."""
    downloads = []

    def fake_download(url, path):
        """Pretend to download: write a small file and remember it."""
        downloads.append(path)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as file:
            file.write(b"video")
        return path

    monkeypatch.setattr(finder.twitch, "get_app_token", Mock(return_value="app-token"))
    monkeypatch.setattr(finder.twitch, "get_user", Mock(return_value={"id": "42", "login": "coolstreamer"}))
    monkeypatch.setattr(finder.twitch, "fetch_top_clips", Mock(return_value=[
        fake_twitch_clip("GoodClip", 30.0, 900),
        fake_twitch_clip("TinyClip", 4.0, 5000),
    ]))
    links = Mock(return_value={"GoodClip": "https://example.com/good.mp4"})
    monkeypatch.setattr(finder.twitch, "get_download_links", links)
    monkeypatch.setattr(finder.twitch, "download_file", fake_download)
    monkeypatch.setattr(twitch_login, "user_token_for", Mock(return_value=("42", "user-token")))
    return SimpleNamespace(downloads=downloads, links=links)


def test_new_clip_is_saved_and_downloaded(fake_twitch):
    """A new top clip is saved with its details and downloaded once; tiny clips are skipped."""
    totals = finder.find_and_download("twitch_clips", channel_config())

    assert totals == {"new": 1, "already_saved": 0, "skipped": 1, "downloaded": 1}
    rows = store.clips_with_status("twitch_clips", "downloaded")
    assert len(rows) == 1
    row = rows[0]
    assert row["streamer"] == "coolstreamer" and row["view_count"] == 900
    assert row["clip_url"] == "https://clips.twitch.tv/GoodClip"
    assert row["credit_text"] == "twitch.tv/coolstreamer"
    assert len(fake_twitch.downloads) == 1


def test_running_twice_never_downloads_twice(fake_twitch):
    """The second run finds the clip already saved and downloads nothing."""
    finder.find_and_download("twitch_clips", channel_config())
    totals = finder.find_and_download("twitch_clips", channel_config())

    assert totals["new"] == 0 and totals["already_saved"] == 1 and totals["downloaded"] == 0
    assert len(fake_twitch.downloads) == 1


def test_same_clip_cannot_be_saved_twice(workspace):
    """The database itself refuses a second copy of the same clip."""
    clip = {"source": "twitch", "source_clip_id": "SameClip", "streamer": "a", "credit_text": "twitch.tv/a"}
    assert store.save_found_clip("twitch_clips", clip) is not None
    assert store.save_found_clip("twitch_clips", clip) is None


def test_dry_run_saves_and_downloads_nothing(fake_twitch):
    """--dry-run only lists what would happen."""
    totals = finder.find_and_download("twitch_clips", channel_config(), dry_run=True)
    assert totals["new"] == 1
    assert store.clips_with_status("twitch_clips", "found") == []
    assert fake_twitch.downloads == []
    twitch_login.user_token_for.assert_not_called()


def test_clip_without_download_link_is_retried_next_run(fake_twitch):
    """If Twitch gives no link, the clip stays "found" with a note, and the next run tries again."""
    fake_twitch.links.return_value = {}
    finder.find_and_download("twitch_clips", channel_config())
    waiting = store.clips_with_status("twitch_clips", "found")
    assert len(waiting) == 1 and "no download link" in waiting[0]["last_error"]

    fake_twitch.links.return_value = {"GoodClip": "https://example.com/good.mp4"}
    totals = finder.find_and_download("twitch_clips", channel_config())
    assert totals["downloaded"] == 1


def test_upload_needs_i_own_this(workspace):
    """A Kick clip file is refused without the confirmation flag."""
    video = workspace / "mine.mp4"
    video.write_bytes(b"not really a video")
    with pytest.raises(ValueError, match="--i-own-this"):
        finder.add_uploaded_video("twitch_clips", channel_config(), str(video), "kickstar", "kick", False)


def test_upload_from_unapproved_streamer_is_refused(workspace):
    """Even with --i-own-this, only approved streamers' clips can be added."""
    video = workspace / "mine.mp4"
    video.write_bytes(b"not really a video")
    with pytest.raises(StreamerNotApproved, match="on Kick"):
        finder.add_uploaded_video("twitch_clips", channel_config(), str(video), "stranger", "kick", True)
    # A Twitch-approved name is not automatically approved on Kick.
    with pytest.raises(StreamerNotApproved):
        finder.add_uploaded_video("twitch_clips", channel_config(), str(video), "coolstreamer", "kick", True)


def test_same_upload_is_only_added_once(workspace, monkeypatch):
    """The file's fingerprint is the duplicate check for uploads."""
    from src.video import clip_render as render_module
    monkeypatch.setattr(render_module, "probe_video", Mock(return_value={"duration": 25.0}))
    video = workspace / "mine.mp4"
    video.write_bytes(b"pretend video bytes")

    first = finder.add_uploaded_video("twitch_clips", channel_config(), str(video), "KickStar", "kick", True)
    second = finder.add_uploaded_video("twitch_clips", channel_config(), str(video), "kickstar", "kick", True)

    assert first is not None and second is None
    clip = store.get_clip(first)
    assert clip["status"] == "downloaded" and "--i-own-this" in clip["ownership_note"]
    assert clip["platform"] == "kick" and clip["credit_text"] == "kick.com/kickstar"


def test_kick_streamers_are_never_fetched_from_an_api(workspace, monkeypatch, capsys):
    """Kick has no clip API: the finder only explains how to upload their clips."""
    monkeypatch.setattr(finder.twitch, "get_app_token", Mock(side_effect=AssertionError("called Twitch")))
    totals = finder.find_and_download("twitch_clips", channel_config(), only_streamer="kickstar")
    assert totals["new"] == 0
    assert "upload FILE --streamer kickstar" in capsys.readouterr().out


def test_connect_is_only_for_twitch_streamers(workspace, monkeypatch):
    """There is no Kick login to run, so 'connect' refuses a Kick streamer."""
    from src.clips import __main__ as cli
    args = SimpleNamespace(streamer="kickstar", channel="twitch_clips", dry_run=True)
    with pytest.raises(StreamerNotApproved, match="on Twitch"):
        cli.run_connect(args, channel_config())


def test_same_name_on_twitch_and_kick_can_both_be_approved():
    """Two different people may use one name on the two sites."""
    entries = [{"username": "sam", "platform": "twitch", "permission_note": "Yes by DM", "permission_date": "2026-11-01"},
               {"username": "sam", "platform": "kick", "permission_note": "Yes by DM", "permission_date": "2026-11-01"}]
    settings = load_clip_settings(channel_config(streamers=entries))
    assert find_approved_streamer(settings, "sam", platform="kick").platform == "kick"


# ---------------------------------------------------------------------------
# Streamer login (device code flow)
# ---------------------------------------------------------------------------

def test_login_used_by_the_wrong_account_is_not_saved(workspace, monkeypatch):
    """If someone else types the code, their token is refused."""
    monkeypatch.setattr(twitch_login, "start_device_login", Mock(return_value={
        "device_code": "d", "user_code": "ABCD", "verification_uri": "https://www.twitch.tv/activate",
        "expires_in": 60, "interval": 1}))
    monkeypatch.setattr(twitch_login, "check_device_login", Mock(return_value={
        "access_token": "a", "refresh_token": "r", "expires_in": 3600, "scope": ["channel:manage:clips"]}))
    monkeypatch.setattr(twitch_login, "get_user", Mock(return_value={"id": "42", "login": "coolstreamer",
                                                                     "display_name": "CoolStreamer"}))
    monkeypatch.setattr(twitch_login, "whoami", Mock(return_value="someoneelse"))
    monkeypatch.setattr(twitch_login.time, "sleep", Mock())

    with pytest.raises(twitch_login.TwitchError, match="someoneelse"):
        twitch_login.connect_streamer("coolstreamer")
    assert twitch_login.get_login("coolstreamer") is None


def test_saved_login_is_encrypted_and_refreshed(workspace, monkeypatch):
    """Tokens are stored encrypted, and an expired one is swapped for a new pair."""
    user = {"id": "42", "login": "CoolStreamer"}
    twitch_login.save_login(user, ["channel:manage:clips"],
                            {"access_token": "old", "refresh_token": "r1", "expires_at": 0})
    saved = twitch_login.get_login("coolstreamer")
    assert "old" not in saved["credentials"]

    refresh = Mock(return_value={"access_token": "new", "refresh_token": "r2", "expires_in": 14000})
    monkeypatch.setattr(twitch_login, "refresh_tokens", refresh)
    assert twitch_login.user_token_for("coolstreamer") == ("42", "new")
    refresh.assert_called_once_with("r1")
    # The new token is saved, so the next call needs no refresh.
    assert twitch_login.user_token_for("coolstreamer") == ("42", "new")
    assert refresh.call_count == 1


def test_streamer_without_login_gets_the_connect_command(workspace):
    """A streamer who never logged in is told exactly what to run."""
    with pytest.raises(twitch_login.TwitchError, match="connect coolstreamer"):
        twitch_login.user_token_for("coolstreamer")


# ---------------------------------------------------------------------------
# 3. Moment picking
# ---------------------------------------------------------------------------

LIMITS = ClipLimits(moment_min_seconds=20, moment_max_seconds=60)


@pytest.mark.parametrize("start, end, length, expected", [
    (10, 30, 100, (10, 30)),     # already fine
    (10, 90, 100, (10, 70)),     # too long → cut to 60 s
    (50, 55, 60, (40, 60)),      # too short → grown to 20 s, backwards at the end
    (0, 15, 15, (0, 15)),        # clip shorter than 20 s → kept whole
    (-5, 200, 45, (0, 45)),      # outside the clip → pulled inside
    (30, 10, 45, (0, 45)),       # end before start → whole clip
])
def test_moment_always_fits_the_limits(start, end, length, expected):
    """Whatever times are proposed, the result is inside the clip and 20-60 s long."""
    assert moments.fit_to_limits(start, end, length, 20, 60) == expected


def make_words(text, start=0.0, step=0.5):
    """Make evenly spaced timed words for tests."""
    words = []
    time = start
    for word in text.split():
        words.append({"word": word, "start": round(time, 2), "end": round(time + step * 0.8, 2)})
        time += step
    return words


def test_good_ai_answer_is_used():
    """A sensible answer is kept as given."""
    clip = {"duration_seconds": 50.0, "title": "big play"}
    answer = {"keep_whole": False, "start": 10, "end": 40, "hook_title": "He did NOT expect that",
              "reason": "the reaction"}
    moment = moments.decide_moment(answer, clip, [], LIMITS, 8)
    assert moment == {"start": 10.0, "end": 40.0, "hook_title": "He did NOT expect that",
                      "reason": "the reaction"}


def test_keep_whole_keeps_a_short_strong_clip():
    """'keep_whole' uses the whole clip when it fits the limits."""
    clip = {"duration_seconds": 34.0, "title": "x"}
    moment = moments.decide_moment({"keep_whole": True, "hook_title": "Wait for it"}, clip, [], LIMITS, 8)
    assert (moment["start"], moment["end"]) == (0.0, 34.0)


def test_unreadable_answer_falls_back_to_whole_clip_and_twitch_title():
    """Garbage from the AI never breaks a video."""
    clip = {"duration_seconds": 30.0, "title": "insane clutch in ranked"}
    moment = moments.decide_moment(moments.read_json_answer("Sorry, I can't."), clip, [], LIMITS, 8)
    assert (moment["start"], moment["end"]) == (0.0, 30.0)
    assert moment["hook_title"] == "insane clutch in ranked"


def test_hook_title_is_cut_to_eight_words_without_hashtags():
    """Long titles are shortened; hashtags and quotes are removed."""
    title = moments.clean_hook_title('"This is the most unbelievable ending you will ever see" #twitch', 8)
    assert title == "This is the most unbelievable ending you will"
    assert len(title.split()) == 8


def test_cut_points_never_split_a_word():
    """A start or end inside a word moves to the word's edge."""
    words = [{"word": "watch", "start": 9.8, "end": 10.4}, {"word": "this", "start": 39.7, "end": 40.3}]
    clip = {"duration_seconds": 60.0, "title": "x"}
    moment = moments.decide_moment({"start": 10.0, "end": 40.0, "hook_title": "t"}, clip, words, LIMITS, 8)
    assert moment["start"] == 9.8 and moment["end"] == 40.3


def test_json_is_found_inside_extra_words():
    """Models sometimes add a sentence around the JSON."""
    answer = moments.read_json_answer('Here you go: {"start": 3, "end": 25} Hope that helps')
    assert answer == {"start": 3, "end": 25}


def test_clip_without_speech_makes_no_ai_call():
    """No words → no paid call; the whole clip and Twitch title are used."""
    llm = Mock()
    clip = {"duration_seconds": 25.0, "title": "silent but deadly", "streamer": "a"}
    moment = moments.pick_moment(clip, [], channel_config(), ClipChannelSettings(), llm)
    llm.ask.assert_not_called()
    assert (moment["start"], moment["end"]) == (0.0, 25.0)


def test_ai_is_asked_with_the_cheap_model_and_timed_transcript():
    """The configured cheap model gets the transcript with times in brackets."""
    llm = Mock()
    llm.ask.return_value = '{"keep_whole": false, "start": 1, "end": 30, "hook_title": "Chat lost it"}'
    clip = {"duration_seconds": 40.0, "title": "t", "streamer": "a"}
    words = make_words("okay chat watch this")
    moment = moments.pick_moment(clip, words, channel_config(), ClipChannelSettings(), llm)

    arguments = llm.ask.call_args.kwargs
    assert arguments["model"] == "claude-haiku-4-5" and arguments["purpose"] == "clip_pick"
    assert arguments["max_tokens"] == 300
    assert "[0.0] okay chat watch this" in arguments["user_message"]
    assert moment["hook_title"] == "Chat lost it"


def test_whisper_output_becomes_timed_words():
    """Sound labels are dropped and a lone punctuation mark joins the word before it."""
    data = {"transcription": [
        {"text": " Okay", "offsets": {"from": 0, "to": 400}},
        {"text": " [MUSIC]", "offsets": {"from": 400, "to": 900}},
        {"text": " wow", "offsets": {"from": 900, "to": 1300}},
        {"text": "!", "offsets": {"from": 1300, "to": 1350}},
    ]}
    assert words_from_whisper_json(data) == [
        {"word": "Okay", "start": 0.0, "end": 0.4},
        {"word": "wow!", "start": 0.9, "end": 1.35},
    ]


# ---------------------------------------------------------------------------
# Layout and captions
# ---------------------------------------------------------------------------

def test_split_layout_keeps_the_whole_webcam_in_view():
    """The webcam crop contains the full webcam box, so faces are never cut off."""
    facecam = FacecamBox(x=0.75, y=0.0, width=0.25, height=0.3)
    plan = plan_layout(1920, 1080, facecam, 720, 0.5)
    camera = plan["panels"][0]["crop"]

    assert plan["mode"] == "split"
    assert camera["x"] <= 1440 and camera["x"] + camera["width"] >= 1920 - 2
    assert camera["y"] == 0 and camera["height"] >= 324 - 2
    # The panels fill the phone screen exactly.
    assert plan["panels"][0]["height"] + plan["panels"][1]["height"] == 1920


def test_every_crop_is_even_and_inside_the_picture():
    """H.264 needs even sizes; no crop may reach outside the stream picture."""
    for facecam in (None, FacecamBox(x=0, y=0.7, width=0.3, height=0.3)):
        plan = plan_layout(1280, 720, facecam, 720, 0.9)
        for panel in plan["panels"]:
            crop = panel["crop"]
            for value in crop.values():
                assert value % 2 == 0
            assert crop["x"] >= 0 and crop["x"] + crop["width"] <= 1280
            assert crop["y"] >= 0 and crop["y"] + crop["height"] <= 720


def test_no_webcam_means_a_centred_full_height_crop():
    """Without a webcam position, the video is one centred 9:16 slice."""
    plan = plan_layout(1920, 1080, None, 720, 0.5)
    crop = plan["panels"][0]["crop"]
    assert plan["mode"] == "full" and crop["height"] == 1080
    assert abs((crop["x"] + crop["width"] / 2) - 960) <= 2
    assert "vstack" not in build_video_filter(plan, 30)
    assert "vstack=inputs=2" in build_video_filter(plan_layout(1920, 1080, FacecamBox(
        x=0, y=0, width=0.2, height=0.2), 720, 0.5), 30)


def test_captions_break_at_sentences_and_highlight_the_spoken_word():
    """Groups end at a sentence end; the latest started word is highlighted."""
    words = make_words("No way. He actually hit that")
    groups = clip_render.group_caption_words(words, 3)
    texts = []
    for group in groups:
        words_in_group = []
        for word in group["words"]:
            words_in_group.append(word["word"])
        texts.append(" ".join(words_in_group))
    assert texts == ["No way.", "He actually hit", "that"]

    assert clip_render.caption_at(groups, -0.1) == (None, None)
    group_number, word_number = clip_render.caption_at(groups, 1.6)
    assert group_number == 1 and word_number == 1   # "actually"


def test_words_are_shifted_to_the_moment():
    """Only the moment's words are kept, timed from its start."""
    words = [{"word": "before", "start": 1, "end": 2}, {"word": "inside", "start": 10, "end": 11}]
    assert clip_render.words_in_moment(words, 9, 20) == [{"word": "inside", "start": 1, "end": 2}]


# ---------------------------------------------------------------------------
# Posting: caption credit and TikTok drafts
# ---------------------------------------------------------------------------

def test_post_caption_credits_and_links_the_streamer():
    """The caption names and links the streamer and adds the channel hashtags."""
    clip = {"credit_text": "twitch.tv/coolstreamer", "source": "twitch"}
    caption = maker.build_post_caption("He did NOT expect that", clip, ["#twitch", "#clips"])
    assert caption.startswith("He did NOT expect that")
    assert "https://twitch.tv/coolstreamer" in caption
    assert caption.endswith("#twitch #clips")
    kick = maker.build_post_caption("t", {"credit_text": "kick.com/kickstar", "source": "upload"}, [])
    assert "https://kick.com/kickstar" in kick


def test_youtube_gets_the_clip_caption_with_shorts_tag(clip_portal, monkeypatch):
    """A clip posted to YouTube credits the streamer and is tagged #Shorts."""
    from src.publishing import scheduler, youtube
    _, _, video_id = clip_portal
    upload = Mock(return_value={"id": "yt1", "public": False})
    monkeypatch.setattr(youtube, "upload_short", upload)
    scheduler.send_to_platform("youtube", get_video_by_id(video_id), channel_config(), {"token": "t"})
    title, description = upload.call_args.args[1], upload.call_args.args[2]
    assert title == "Chat was NOT ready"
    assert "https://twitch.tv/coolstreamer" in description and description.endswith("#Shorts")


def test_channel_posts_to_tiktok_and_youtube():
    """The real clips channel sends approved videos to TikTok and YouTube."""
    from src.publishing.scheduler import channel_platforms
    with open("channels/twitch_clips.yaml", encoding="utf-8") as file:
        config = yaml.safe_load(file)
    assert channel_platforms(config) == ["tiktok", "youtube"]
    assert config["youtube"]["category_id"] == 20


def test_tiktok_draft_upload_sends_only_the_file_details(tmp_path, monkeypatch):
    """TikTok's inbox endpoint takes only source_info; no caption fields are sent."""
    from src.publishing import tiktok
    video = tmp_path / "v.mp4"
    video.write_bytes(b"12345")
    sent = Mock(return_value={"data": {"publish_id": "p1", "upload_url": "https://upload.example"}})
    monkeypatch.setattr(tiktok, "_tiktok_post", sent)
    response = Mock(status=201)
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    monkeypatch.setattr(tiktok.urllib.request, "urlopen", Mock(return_value=response))

    assert tiktok.upload_to_drafts(str(video), access_token="t") == "p1"
    payload = sent.call_args.args[1]
    assert list(payload) == ["source_info"]
    assert payload["source_info"]["video_size"] == 5


# ---------------------------------------------------------------------------
# Rendering (needs FFmpeg)
# ---------------------------------------------------------------------------

def make_test_clip(path, seconds=3):
    """Create a small 1920x1080 test video with a beep, using FFmpeg."""
    subprocess.run([
        "ffmpeg", "-v", "error", "-y",
        "-f", "lavfi", "-i", f"testsrc2=size=1920x1080:rate=30:duration={seconds}",
        "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
        "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(path),
    ], check=True)


@pytest.mark.skipif(not HAS_FFMPEG, reason="FFmpeg is not installed")
def test_render_makes_a_vertical_video_of_the_moment(tmp_path, monkeypatch):
    """The finished file is 1080x1920, as long as the moment, with sound."""
    monkeypatch.setattr(clip_render, "credit_icon", Mock(return_value=None))
    source = tmp_path / "clip.mp4"
    make_test_clip(source, seconds=3)
    output = tmp_path / "out.mp4"

    clip_render.render_clip_video(str(source), 0.5, 2.5, make_words("no way he did that"),
                                  "He did that", "twitch.tv/coolstreamer",
                                  FacecamBox(x=0.75, y=0, width=0.25, height=0.3),
                                  ClipChannelSettings(), str(output))

    result = clip_render.probe_video(str(output))
    assert (result["width"], result["height"]) == (1080, 1920)
    assert abs(result["duration"] - 2.0) < 0.2
    assert result["has_audio"]


@pytest.mark.skipif(not HAS_FFMPEG, reason="FFmpeg is not installed")
def test_clip_becomes_a_drafted_video_in_the_review_queue(workspace, monkeypatch):
    """Downloaded clip → moment picked → rendered → drafted video with notes and credit."""
    monkeypatch.setattr(clip_render, "credit_icon", Mock(return_value=None))
    monkeypatch.setattr(maker, "transcribe_clip", Mock(return_value=make_words("okay chat watch this")))
    source = workspace / "clip.mp4"
    make_test_clip(source, seconds=3)
    clip_id = store.save_found_clip("twitch_clips", {
        "source": "twitch", "source_clip_id": "abc", "streamer": "CoolStreamer",
        "credit_text": "twitch.tv/coolstreamer", "clip_url": "https://clips.twitch.tv/abc",
        "title": "watch this", "view_count": 10, "duration_seconds": 3.0})
    store.update_clip(clip_id, status="downloaded", file_path=str(source))

    llm = SimpleNamespace(cost_tracker=None, ask=Mock(
        return_value='{"keep_whole": true, "hook_title": "Chat was NOT ready", "reason": "short"}'))
    config = channel_config()
    video_ids = maker.make_videos("twitch_clips", config, llm)

    assert len(video_ids) == 1
    video = get_video_by_id(video_ids[0])
    assert video["status"] == "drafted" and video["story_title"] == "Chat was NOT ready"
    assert video["story_url"] == "https://clips.twitch.tv/abc"
    clip = store.get_clip(clip_id)
    assert clip["status"] == "rendered" and clip["video_id"] == video["id"]
    with open(video["script_path"], encoding="utf-8") as file:
        notes = file.read()
    assert "Said yes in a Twitch DM" in notes and "https://twitch.tv/coolstreamer" in notes

    # Running again does nothing: the clip is already rendered.
    assert maker.make_videos("twitch_clips", config, llm) == []
    assert llm.ask.call_count == 1


# ---------------------------------------------------------------------------
# Dashboard: review page and the re-cut form
# ---------------------------------------------------------------------------

@pytest.fixture
def clip_portal(portal):
    """A logged-in dashboard with a clips channel and one drafted clip video."""
    client, root = portal
    with open(root / "channels" / "twitch_clips.yaml", "w", encoding="utf-8") as file:
        yaml.safe_dump(channel_config(), file)
    notes = root / "output" / "twitch_clips" / "clip.md"
    notes.parent.mkdir(parents=True)
    notes.write_text("# notes")
    from src.storage.queries import save_video
    video_id = save_video("twitch_clips", "Chat was NOT ready", "https://clips.twitch.tv/abc", str(notes), 0)
    clip_id = store.save_found_clip("twitch_clips", {
        "source": "twitch", "source_clip_id": "abc", "streamer": "coolstreamer",
        "credit_text": "twitch.tv/coolstreamer", "clip_url": "https://clips.twitch.tv/abc",
        "duration_seconds": 45.0})
    store.update_clip(clip_id, status="rendered", video_id=video_id, start_seconds=5.0,
                      end_seconds=35.0, hook_title="Chat was NOT ready")
    return client, root, video_id


from tests.test_control_center import execute, portal, token  # noqa: E402,F401 (portal is a fixture)


def test_review_page_shows_recut_form_and_caption(clip_portal):
    """A clip video's page offers the re-cut form and the caption to paste."""
    client, _, video_id = clip_portal
    page = client.get(f"/video/{video_id}").text
    assert "Change the moment or hook title" in page
    assert "Caption to paste in TikTok" in page
    assert "https://twitch.tv/coolstreamer" in page
    assert "Make an animated video" not in page


def test_recut_form_queues_a_free_clip_render_job(clip_portal):
    """A valid new moment starts one free clip_render job and resets the review."""
    client, _, video_id = clip_portal
    response = client.post(f"/video/{video_id}/clip", follow_redirects=False, data={
        "start": "10", "end": "40", "hook_title": "New title here", "submission_id": "s1",
        "form_token": token(client)})
    assert response.status_code == 303

    job = execute("SELECT * FROM jobs")[0]
    payload = json.loads(job["payload"])
    assert job["kind"] == "clip_render" and job["budget_usd"] == 0
    assert (payload["start"], payload["end"], payload["hook_title"]) == (10.0, 40.0, "New title here")
    assert get_video_by_id(video_id)["status"] == "drafted"


def test_recut_form_refuses_a_moment_outside_the_clip(clip_portal):
    """Times past the end of the clip are refused before any job starts."""
    client, _, video_id = clip_portal
    response = client.post(f"/video/{video_id}/clip", data={
        "start": "10", "end": "99", "hook_title": "t", "submission_id": "s2",
        "form_token": token(client)})
    assert response.status_code == 400 and "between 0 and 45.0" in response.text
    assert execute("SELECT * FROM jobs") == []


def test_recut_job_saves_the_moment_and_renders_again(clip_portal, monkeypatch):
    """The worker saves the new moment and title, then re-renders."""
    from src.dashboard import worker
    _, _, video_id = clip_portal
    rerender = Mock()
    monkeypatch.setattr(maker, "rerender_clip", rerender)
    job = {"id": "j1", "dry_run": 0, "payload": {"video_id": video_id, "start": 2.0, "end": 30.0,
                                                 "hook_title": "Better title", "config": channel_config()}}
    monkeypatch.setattr(worker, "update_job", Mock())

    worker.recut_clip(job)

    clip = store.get_clip_for_video(video_id)
    assert (clip["start_seconds"], clip["end_seconds"], clip["hook_title"]) == (2.0, 30.0, "Better title")
    assert get_video_by_id(video_id)["story_title"] == "Better title"
    rerender.assert_called_once()


def test_clips_channel_cannot_start_article_jobs(clip_portal):
    """The browser can't start news-style jobs for a clips channel."""
    from src.dashboard import jobs
    with pytest.raises(ValueError, match="python -m src.clips"):
        jobs.enqueue_job("twitch_clips", "discover", {}, True, "s3")
    client, _, _ = clip_portal
    assert "twitch_clips" not in client.get("/new").text


def test_keeping_the_whole_clip_keeps_the_last_word():
    """Whisper's last word may end just past the file; it must not be cut off."""
    words = [{"word": "top", "start": 28.9, "end": 29.3}, {"word": "that", "start": 29.4, "end": 29.6}]
    clip = {"duration_seconds": 29.52, "title": "x"}
    moment = moments.decide_moment({"keep_whole": True, "hook_title": "t"}, clip, words, LIMITS, 8)
    assert moment["end"] == 29.52


def test_dashboard_pages_load_with_a_clips_channel(clip_portal):
    """The overview, channel, library and review pages work for a clips channel."""
    client, _, video_id = clip_portal
    for path in ("/", "/channels", "/channels/twitch_clips", "/library", "/videos", f"/video/{video_id}"):
        assert client.get(path).status_code == 200, path

# =============================================================
# tests/test_tiktok_research.py — checks for src/clips/tiktok_research.py
#
# Uses small made-up videos so the maths (rates, groups, hashtag lift)
# can be checked by hand. No network, no real data needed.
# =============================================================

import json

from src.clips import tiktok_research as research

NOW = 1_800_000_000   # a fixed "current time" so ages never change


def make_video(video_id, views, duration=25, hashtags=None, desc="caption", followers=1000,
               age_days=2, comments=10, shares=5, saves=3, likes=100, is_ad=False):
    """Build one fake saved video with sensible defaults."""
    return {"id": video_id, "views": views, "duration": duration, "hashtags": hashtags or [],
            "desc": desc, "followers": followers, "created": NOW - age_days * 86400,
            "comments": comments, "shares": shares, "saves": saves, "likes": likes,
            "is_ad": is_ad, "source": "foryou"}


def test_load_videos_skips_adverts_repeats_and_missing_views(tmp_path):
    path = tmp_path / "videos.jsonl"
    lines = [make_video("1", 100), make_video("1", 100), make_video("2", 50, is_ad=True),
             {"id": "3", "views": None}, make_video("4", 70)]
    path.write_text("\n".join(json.dumps(line) for line in lines) + "\n\n")
    videos = research.load_videos(path)
    assert [video["id"] for video in videos] == ["1", "4"]


def test_caption_words_ignores_hashtags_and_names():
    assert research.caption_words("he did NOT see that coming #fyp @bob") == 6
    assert research.caption_words("") == 0
    assert research.caption_words(None) == 0


def test_add_measures_rates_and_speed():
    video = research.add_measures(make_video("1", 20_000, comments=40, shares=10, age_days=4), now=NOW)
    assert video["views_per_day"] == 5_000
    assert video["comments_per_1k"] == 2
    assert video["shares_per_1k"] == 0.5
    assert video["views_per_follower"] == 20


def test_add_measures_skips_rates_for_tiny_videos():
    video = research.add_measures(make_video("1", 500), now=NOW)
    assert video["comments_per_1k"] is None


def test_bucket_name_edges():
    assert research.bucket_name(25, research.DURATION_BUCKETS) == "20-30 s"
    assert research.bucket_name(30, research.DURATION_BUCKETS) == "30-45 s"
    assert research.bucket_name(None, research.DURATION_BUCKETS) == "unknown"


def test_hashtag_lift_finds_tag_common_among_hits():
    videos = []
    for number in range(8):
        # the 2 fastest videos use #funny, the slow ones use #fyp
        if number < 2:
            videos.append(make_video(str(number), 1_000_000, hashtags=["funny"]))
        else:
            videos.append(make_video(str(number), 1_000, hashtags=["fyp"]))
    for video in videos:
        research.add_measures(video, now=NOW)
    rows = research.hashtag_lift(videos, minimum_uses=2)
    assert rows[0]["hashtag"] == "funny"
    assert rows[0]["lift"] > 1
    assert rows[-1]["hashtag"] == "fyp"
    assert rows[-1]["generic"] is True


def test_group_summary_uses_medians():
    videos = [make_video("1", 10_000, duration=5), make_video("2", 30_000, duration=8),
              make_video("3", 5_000_000, duration=25)]
    for video in videos:
        research.add_measures(video, now=NOW)
    rows = research.group_summary(videos, lambda v: research.bucket_name(v["duration"], research.DURATION_BUCKETS))
    short = [row for row in rows if row["group"] == "under 10 s"][0]
    assert short["videos"] == 2
    assert short["median_views"] == 20_000


def test_format_number():
    assert research.format_number(1_234_567) == "1.2M"
    assert research.format_number(34_000) == "34K"
    assert research.format_number(None) == "-"


def test_build_report_runs_on_small_set():
    videos = [make_video(str(n), 10_000 * (n + 1), hashtags=["a", "b"]) for n in range(12)]
    for video in videos:
        research.add_measures(video, now=NOW)
    report = research.build_report(videos)
    assert "Most views" in report
    assert "Length" in report

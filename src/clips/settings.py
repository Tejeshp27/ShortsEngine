# =============================================================
# src/clips/settings.py — read a clips channel's settings and
#                         check that a streamer is approved
#
# A clips channel's YAML file (e.g. channels/twitch_clips.yaml)
# lists the streamers who gave written permission, plus the clip
# length limits, layout and caption style. This file turns that
# YAML into checked Python objects using pydantic, so a typo (for
# example a negative length) stops with a clear message instead
# of producing a broken video.
#
# The most important rule lives here: find_approved_streamer()
# refuses any streamer who is not on the approved list. Every
# step that touches a streamer's clips calls it first.
#
# Streamers can be on Twitch or Kick. Twitch clips are fetched
# automatically (official API). Kick has no official clip API, so
# a Kick streamer's clips are added by hand with the upload command.
# =============================================================

from datetime import date

from typing import Literal

from pydantic import BaseModel, Field, ValidationError, field_validator


# The streaming sites a streamer can be on, and their channel web address.
# The credit on screen and in captions is "<address>/<username>".
# YouTube channels are addressed by their @handle (youtube.com/@ishowspeed).
STREAM_PLATFORM_ADDRESSES = {"twitch": "twitch.tv", "kick": "kick.com", "youtube": "youtube.com"}


# The three ways a wide stream picture can be fitted to a tall phone screen
# (drawn in src/video/clip_layout.py).
LAYOUT_MODE_PATTERN = r"^(fit-blur|split|center-crop)$"


class StreamerNotApproved(Exception):
    """Raised when someone tries to use clips from a streamer who is not approved."""


class FacecamBox(BaseModel):
    """
    Where a streamer's webcam sits in their 16:9 stream picture.

    All four numbers are fractions of the picture (0.0 to 1.0), so the
    same settings work for 720p and 1080p streams. For example
    x=0.75, y=0.0, width=0.25, height=0.3 is a webcam in the top-right corner.
    """
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    width: float = Field(gt=0, le=1)
    height: float = Field(gt=0, le=1)


class ApprovedStreamer(BaseModel):
    """
    One streamer who agreed in writing that we may use their clips.

    Fields:
        username: Their login name, e.g. "examplestreamer".
        platform: "twitch" (clips fetched automatically), "kick" or "youtube" (live recording only).
        permission_note: How and where they agreed, e.g. "Said yes in a Twitch DM".
        permission_date: The day they agreed.
        permission_proof: Optional path to your screenshot of the agreement.
        facecam: Optional webcam position, needed for the "split" layout.
        layout: Optional layout for just this streamer ("fit-blur", "split"
                or "center-crop"); empty uses the channel's layout.mode.
        display_name: The name viewers know them by, used in every hook title
                      (e.g. "Kai Cenat"); empty uses the username.
    """
    # Logins are letters, numbers, underscores (and hyphens on Kick).
    username: str = Field(min_length=1, max_length=25, pattern=r"^[A-Za-z0-9_-]+$")
    platform: str = Field(default="twitch", pattern=r"^(twitch|kick|youtube)$")
    permission_note: str = Field(min_length=5, max_length=500)
    permission_date: date
    permission_proof: str = ""
    facecam: FacecamBox | None = None
    enabled: bool = True
    recording_directory: str = ""
    layout: str = Field(default="", pattern=LAYOUT_MODE_PATTERN + "|^$")
    display_name: str = Field(default="", max_length=40)


class ClipLimits(BaseModel):
    """Which clips to look at and how long finished videos may be (see the YAML for each field)."""
    hours_back: int = Field(default=24, ge=1, le=168)
    clips_per_streamer: int = Field(default=5, ge=1, le=20)
    min_views: int = Field(default=0, ge=0)
    skip_shorter_than_seconds: float = Field(default=8, ge=1, le=60)
    moment_min_seconds: float = Field(default=20, ge=3, le=120)
    moment_max_seconds: float = Field(default=60, ge=5, le=180)


class DiscoverySettings(BaseModel):
    """Validate YAML discovery intervals and request limits; missing inputs use free metadata-only defaults."""

    enabled: bool = True
    interval_seconds: int = Field(default=21600, ge=60, le=604800)
    hours_back: int = Field(default=168, ge=1, le=720)
    max_pages: int = Field(default=10, ge=1, le=10)


class TranscriptionSettings(BaseModel):
    """
    Which local Whisper program and model to use, and the language spoken.

    engine: "auto" uses whisper.cpp when it is installed (it kept shouted
            speech that faster-whisper skipped in testing) and
            faster-whisper otherwise.
    """
    whisper_model: str = Field(default="small.en", pattern=r"^[a-z0-9.\-]+$")
    language: str = Field(default="en", min_length=2, max_length=5)
    engine: str = Field(default="auto", pattern=r"^(auto|faster-whisper|whisper\.cpp)$")


class LayoutSettings(BaseModel):
    """
    How the wide stream picture is fitted to the tall phone screen.

    mode: "fit-blur" (the whole picture, on a blurred copy of itself),
          "split" (gameplay on top, the streamer's facecam below) or
          "center-crop" (a tall slice from the middle; cuts off the sides).
    split_gameplay_share: How much of the screen height the gameplay gets in "split".
    split_facecam_position: "top" puts the facecam above the gameplay in "split";
                            "bottom" puts it below.
    foreground_center: Where the middle of the picture sits in "fit-blur",
                       as a fraction of the screen height (0.5 = exact middle).
    gameplay_focus_x: Which part of a cropped picture to keep (0 left, 1 right).
    background_blur: How strongly the "fit-blur" background is blurred.
    background_darken: How much darker the background is (0 = not at all).
    """
    mode: str = Field(default="fit-blur", pattern=LAYOUT_MODE_PATTERN)
    split_gameplay_share: float = Field(default=0.6, ge=0.4, le=0.8)
    split_facecam_position: str = Field(default="bottom", pattern=r"^(top|bottom)$")
    foreground_center: float = Field(default=0.45, ge=0.3, le=0.6)
    gameplay_focus_x: float = Field(default=0.5, ge=0, le=1)
    background_blur: int = Field(default=20, ge=2, le=40)
    background_darken: float = Field(default=0.12, ge=0, le=0.5)


class ClipEncodingSettings(BaseModel):
    """
    How the finished MP4 is compressed (H.264 video, AAC sound).

    frame_rate: "source" keeps the stream's own frame rate (60 stays 60),
                or a fixed number such as 30.
    max_frame_rate: The highest frame rate "source" may produce.
    crf: Picture quality; lower keeps more detail but makes bigger files.
    preset: Encoding effort; slower presets make smaller files at the same quality.
    profile: The H.264 feature level; "high" is what phones and platforms expect.
    """
    frame_rate: int | Literal["source"] = "source"
    max_frame_rate: int = Field(default=60, ge=24, le=60)
    crf: int = Field(default=18, ge=14, le=28)
    preset: str = Field(default="slow", pattern=r"^(ultrafast|superfast|veryfast|faster|fast|medium|slow|slower|veryslow)$")
    profile: str = Field(default="high", pattern=r"^(baseline|main|high)$")

    @field_validator("frame_rate")
    @classmethod
    def check_frame_rate(cls, value):
        """
        Refuse fixed frame rates outside 24-60 (pydantic calls this automatically).

        Args:
            value: The YAML value, either "source" or a number.

        Returns:
            The same value when it is allowed.
        """
        # @field_validator tells pydantic to run this check when the
        # settings are loaded; @classmethod is required by pydantic.
        if value != "source" and not 24 <= value <= 60:
            raise ValueError("frame_rate must be 'source' or a number from 24 to 60")
        return value


class ClipAudioSettings(BaseModel):
    """
    How the clip's sound is cleaned and levelled (see src/video/clip_audio.py).

    speech_enhancement / dialogue_boost: optional FFmpeg dialogue emphasis
        that lifts centred voices above stereo game sound.
    highpass_hz: Remove rumble below this pitch (0 = off).
    noise_reduction_db: Light FFT noise reduction strength (0 = off).
    compressor: Gently even out sudden loud shouts and effects.
    target_lufs / true_peak_db: Final loudness; -14 LUFS and -1 dBTP are
        what TikTok and YouTube aim for.
    """
    speech_enhancement: bool = False
    dialogue_boost: float = Field(default=2.0, ge=0, le=3)
    highpass_hz: int = Field(default=80, ge=0, le=300)
    noise_reduction_db: float = Field(default=10, ge=0, le=40)
    compressor: bool = True
    target_lufs: float = Field(default=-14, ge=-30, le=-8)
    true_peak_db: float = Field(default=-1.0, ge=-9, le=0)


class BleepSettings(BaseModel):
    """
    Profanity bleeping (see src/clips/profanity.py).

    enabled: Turn bleeping on or off for this channel.
    word_list: The editable YAML list of words to bleep.
    padding_seconds: Extra muted time before and after each word.
    min_seconds: The shortest beep, so a quick word is not just a click.
    tone_hz: The beep's pitch (1000 Hz is the classic TV bleep).
    volume: Beep loudness compared with the voice it replaces (1 = the same;
            0 = no tone at all, the word is simply muted).
    mask_captions: Show "f***" instead of the word in the burned-in captions.
    precise_timing: Re-time each swear word with faster-whisper so the beep sits
                    exactly on it (see src/clips/bleep_timing.py).
    max_seconds_per_letter: A swear word that can't be re-timed is bleeped for
                            at most this long per letter, so following words stay audible.
    """
    enabled: bool = False
    word_list: str = "config/profanity.yaml"
    padding_seconds: float = Field(default=0.04, ge=0, le=0.3)
    min_seconds: float = Field(default=0.15, ge=0.05, le=1)
    tone_hz: int = Field(default=1000, ge=200, le=4000)
    volume: float = Field(default=0.8, ge=0, le=2)
    mask_captions: bool = True
    precise_timing: bool = True
    max_seconds_per_letter: float = Field(default=0.09, ge=0.04, le=0.3)


class SourceQualitySettings(BaseModel):
    """
    The lowest source quality a clip may be made from.

    min_height: Sources shorter than this many pixels (e.g. 480p) are flagged.
    reject_below_min: True refuses to render them; False renders with a warning.
    """
    min_height: int = Field(default=720, ge=240, le=2160)
    reject_below_min: bool = False


class AnalysisSettings(BaseModel):
    """Control local highlight estimates; inputs are YAML values, returned as checked settings."""

    max_clips_per_recording: int = Field(default=3, ge=1, le=20)
    minimum_score: float = Field(default=55, ge=0, le=100)
    candidate_seconds: float = Field(default=35, ge=5, le=180)
    context_seconds: float = Field(default=5, ge=0, le=30)
    use_llm: bool = False
    weights: dict[str, float] = {
        "entertainment": 0.25, "hook": 0.20, "audience": 0.15,
        "clarity": 0.20, "audio": 0.10, "visual": 0.10,
    }
    excitement_terms: list[str] = ["wow", "what", "wait", "unbelievable", "no way", "amazing", "why"]


class LiveRecordingSettings(BaseModel):
    """
    How approved streamers' public live streams are recorded in rolling chunks.

    The recorder saves each stream in chunks (for example one hour), turns
    every finished chunk into clips, then deletes the chunk so the disk is
    reused. Live chat activity and viewer-made Twitch clips are counted
    while recording, because they show which moments people reacted to.

    Fields (see channels/twitch_clips.yaml for a comment on each):
        enabled: Record live streams at all.
        quality: Streamlink quality names to try, best first.
        chunk_minutes: Length of each saved piece of a stream.
        overlap_seconds: Seconds of the previous chunk added to the start of
            the next one, so a moment on the boundary is not cut in half.
        max_concurrent: Most streams recorded at the same time.
        max_hours_per_streamer_per_day: Daily recording limit per streamer.
        check_seconds: How often to check who is live.
        min_free_gb_to_start: Free disk needed before a new recording starts.
        stop_below_free_gb: Running recordings stop when free disk drops below this.
        chat: Count Twitch chat messages while recording (no text is stored).
        chat_bin_seconds: Chat messages are counted in buckets of this many seconds.
        chat_reaction_seconds: How long after a moment chat usually reacts.
        viewer_clip_weight: How much one viewer-made clip counts, compared
            with a normal amount of chat in one bucket.
        keep_source_padding_seconds: Seconds of footage kept on each side of
            every chosen moment after the full chunk is deleted (for re-cuts).
        delete_raw_after_processing: Delete each chunk once its clips are made.
        delete_posted_after_hours: Delete a finished video's file this many
            hours after it was delivered everywhere (or rejected).
        folder: Where chunks are written while a stream is being recorded.
    """
    enabled: bool = False
    quality: str = Field(default="1080p60,1080p,best", pattern=r"^[A-Za-z0-9_,]+$")
    chunk_minutes: float = Field(default=60, ge=2, le=180)
    overlap_seconds: float = Field(default=120, ge=0, le=600)
    max_concurrent: int = Field(default=2, ge=1, le=20)
    max_hours_per_streamer_per_day: float = Field(default=6, gt=0, le=24)
    check_seconds: int = Field(default=60, ge=30, le=3600)
    min_free_gb_to_start: float = Field(default=20, ge=1)
    stop_below_free_gb: float = Field(default=10, ge=0.5)
    chat: bool = True
    chat_bin_seconds: int = Field(default=5, ge=1, le=60)
    chat_reaction_seconds: float = Field(default=4, ge=0, le=30)
    viewer_clip_weight: float = Field(default=3, ge=0, le=50)
    keep_source_padding_seconds: float = Field(default=30, ge=0, le=300)
    delete_raw_after_processing: bool = True
    delete_posted_after_hours: float = Field(default=24, ge=0, le=8760)
    folder: str = "recordings/live"


class AutomationSettings(BaseModel):
    """Control repeated runs; YAML inputs set intervals, retries, review, and daily upload limits."""

    enabled: bool = False
    poll_seconds: int = Field(default=300, ge=30, le=86400)
    stable_file_seconds: int = Field(default=60, ge=5, le=3600)
    max_recordings_per_run: int = Field(default=1, ge=1, le=20)
    max_attempts: int = Field(default=3, ge=1, le=10)
    retry_seconds: int = Field(default=300, ge=10, le=86400)
    require_approval: bool = True
    publish_dry_run: bool = True
    max_uploads_per_day: int = Field(default=3, ge=0, le=100)


class CaptionSettings(BaseModel):
    """How the burned-in word-by-word captions look."""
    words_per_caption: int = Field(default=3, ge=1, le=6)
    font_size: int = Field(default=84, ge=30, le=160)
    text_color: str = Field(default="#FFFFFF", pattern=r"^#[0-9A-Fa-f]{6}$")
    highlight_color: str = Field(default="#FFE14D", pattern=r"^#[0-9A-Fa-f]{6}$")
    # Empty (null) lets the layout choose a spot that never covers the
    # gameplay or sits under the app's buttons; a number forces a height.
    center_y: int | None = Field(default=None, ge=300, le=1480)
    font_path: str = ""
    outline_color: str = Field(default="#000000", pattern=r"^#[0-9A-Fa-f]{6}$")
    outline_width: int = Field(default=6, ge=0, le=20)
    # Captions show only the streamer's own voice (src/clips/speaker_filter.py);
    # pieces less similar than voice_match_min (0-1) are left out.
    streamer_voice_only: bool = False
    voice_match_min: float = Field(default=0.6, ge=0.2, le=0.95)
    # Look: "clean" (sentence case, one key word coloured), "bold" (capitals,
    # spoken word coloured), "boxed" (text on a dark box), "quote" (no word
    # captions; the hook line stays up the whole clip), "punch" (1-2 big
    # capital words at a time, like the top streamer-clip channels).
    style: str = Field(default="bold", pattern=r"^(clean|bold|boxed|quote|punch)$")
    # Colour for words from another voice (friend, guest) so viewers see who is talking.
    other_speaker_color: str = Field(default="#7FE7FF", pattern=r"^#[0-9A-Fa-f]{6}$")
    # Show *LAUGHING* / *SCREAMING* when those sounds are heard and nobody is talking.
    sound_labels: bool = False
    # Other voices: "exchange" keeps them when they talk WITH the streamer,
    # "none" keeps only the streamer, "all" keeps every voice.
    other_voices: str = Field(default="none", pattern=r"^(exchange|none|all)$")
    # Remove Whisper mistakes (stock phrases over silence, loops, unheard words).
    check_accuracy: bool = True


class PackagingSettings(BaseModel):
    """
    Packaging experiments (src/clips/packaging.py): titles, hashtags and caption looks
    keep varying, guided by how earlier posts performed, until winners are clear.

    enabled: Vary packaging per clip (False = always the channel's fixed settings).
    explore_min_tries: Each option is used at least this many times before results steer.
    winner_confidence: An option is reported as the winner once it is this likely to be best.
    dimensions: Optional override of the options to test, e.g. {hashtags: [minimal, game]}.
    """
    enabled: bool = False
    explore_min_tries: int = Field(default=3, ge=1, le=20)
    winner_confidence: float = Field(default=0.95, ge=0.5, le=0.999)
    dimensions: dict[str, list[str]] = {}


class EffectSettings(BaseModel):
    """
    Small edits seen in top clips (src/clips/production.py).

    payoff_zoom: Slowly zoom the face (or picture) this much during the reaction
                 after the payoff (1.0 = off; 1.12 = 12% closer).
    payoff_black_white: Turn the reaction black-and-white (used by some top clippers).
    """
    payoff_zoom: float = Field(default=1.0, ge=1.0, le=1.4)
    payoff_black_white: bool = False


class FramingSettings(BaseModel):
    """
    Face-aware framing (src/clips/framing.py): find the streamer's face in
    every clip and choose a layout that keeps the whole head visible.

    enabled: Detect faces per clip (False = the fixed layout settings above).
    sample_frames: How many frames of the moment are checked for faces.
    full_camera_face_height: A face at least this tall (share of the picture
        height) means a full-camera/IRL shot, cropped tall around the person.
    gameplay_share: Gameplay's share of the screen in the stacked layout.
    reaction_gameplay_share: Same, for reaction/conversation moments (bigger face).
    face_share: How much of the facecam panel's height the face fills.
    max_zoom: Largest allowed enlargement of the facecam (beyond it: fall back).
    """
    enabled: bool = True
    sample_frames: int = Field(default=12, ge=4, le=40)
    full_camera_face_height: float = Field(default=0.2, ge=0.08, le=0.6)
    gameplay_share: float = Field(default=0.55, ge=0.4, le=0.75)
    reaction_gameplay_share: float = Field(default=0.5, ge=0.35, le=0.75)
    face_share: float = Field(default=0.3, ge=0.15, le=0.6)
    max_zoom: float = Field(default=4.0, ge=1.5, le=8)


class RankingSettings(BaseModel):
    """
    Moment ranking (src/clips/ranking.py + src/clips/editor.py).

    enabled: Use this ranking (False = the older analysis.weights scoring).
    profile: The versioned weights/thresholds file (config/clip_ranking/).
             Roll back by pointing at an older file.
    ab_profile: Optional second profile for an A/B test: odd-numbered
                recordings use it, even ones use `profile`; the decision log
                records which one chose each clip (compare with `feedback`).
    use_editor_model: Let the cheap model read and judge shortlisted moments
                      (one call per recording, ~1–2 cents per recorded hour).
    shortlist_size: How many locally found moments the model reviews.
    context_seconds: Extra transcript shown before each moment (for its setup).
    """
    enabled: bool = True
    profile: str = "config/clip_ranking/v1.yaml"
    ab_profile: str = ""
    use_editor_model: bool = True
    shortlist_size: int = Field(default=6, ge=1, le=12)
    context_seconds: float = Field(default=20, ge=0, le=60)


class TrimSettings(BaseModel):
    """
    Smart trimming (src/clips/trimming.py). Seconds unless stated.

    enabled: Tighten every clip (False = keep the chosen start/end as they are).
    lead_seconds: Sound kept before the first line.
    max_dead_air_seconds: Silent gaps longer than this may be shortened...
    keep_gap_seconds: ...down to this much.
    comedic_pause_seconds: The pause just before the payoff may stay this long.
    reaction_tail_seconds: Kept after the payoff line.
    max_tail_seconds: Longest tail while people are still laughing.
    max_cuts: Most jump cuts per clip (prevents over-editing).
    """
    enabled: bool = True
    lead_seconds: float = Field(default=0.25, ge=0, le=2)
    max_dead_air_seconds: float = Field(default=1.4, ge=0.5, le=10)
    keep_gap_seconds: float = Field(default=0.5, ge=0.1, le=3)
    comedic_pause_seconds: float = Field(default=2.5, ge=0.5, le=6)
    reaction_tail_seconds: float = Field(default=1.0, ge=0.2, le=5)
    max_tail_seconds: float = Field(default=3.0, ge=0.5, le=8)
    max_cuts: int = Field(default=3, ge=0, le=10)


class TitleSettings(BaseModel):
    """
    Title rules (src/clips/titles.py).

    hook_max_words: On-screen hook text length.
    youtube_max_words: YouTube Shorts title length.
    max_emoji: Most emoji in a title.
    max_hashtags: Most hashtags in a post (TikTok shows at most 5).
    minimum_score: Model title ideas scoring below this (0–100) are not used.
    """
    hook_max_words: int = Field(default=8, ge=2, le=15)
    youtube_max_words: int = Field(default=12, ge=4, le=20)
    max_emoji: int = Field(default=1, ge=0, le=3)
    max_hashtags: int = Field(default=4, ge=0, le=5)
    minimum_score: float = Field(default=55, ge=0, le=100)


class QASettings(BaseModel):
    """
    Final quality control (src/clips/qa.py).

    minimum_score: Clips scoring below this (0–100) are rejected.
    flag_below: Clips below this are kept but flagged for the owner.
    max_repairs: Automatic re-renders for fixable problems (no endless loops).
    min_caption_confidence: Captions below this confidence fail the caption check.
    """
    minimum_score: float = Field(default=60, ge=0, le=100)
    flag_below: float = Field(default=75, ge=0, le=100)
    max_repairs: int = Field(default=1, ge=0, le=3)
    min_caption_confidence: float = Field(default=0.6, ge=0, le=1)


class HookTitleSettings(BaseModel):
    """Validate opening title duration, size, word limit and optional model generation; missing YAML uses defaults.

    seconds: how long the hook/context line stays up; 0 = the whole clip.
    style: "box" (dark text on a white box) or "outline" (white text with a black outline).
    """
    seconds: float = Field(default=3.0, ge=0, le=10)
    style: str = Field(default="box", pattern=r"^(box|outline)$")
    font_size: int = Field(default=80, ge=30, le=160)
    max_words: int = Field(default=8, ge=2, le=15)
    use_llm: bool = False


class CreditSettings(BaseModel):
    """
    The small streamer credit tag (e.g. "twitch.tv/name") in the top-left corner.

    enabled: Show the tag in the video (post captions always credit the streamer).
    seconds: How long it stays on screen from the start (0 = the whole video).
    font_size: Text height in pixels.
    """
    font_size: int = Field(default=34, ge=20, le=100)
    enabled: bool = True
    seconds: float = Field(default=4.0, ge=0, le=60)


class ClipChannelSettings(BaseModel):
    """
    Everything a clips channel's YAML file controls, already checked.

    Sections that are missing from the YAML use the defaults above.
    """
    streamers: list[ApprovedStreamer] = []
    clips: ClipLimits = ClipLimits()
    discovery: DiscoverySettings = DiscoverySettings()
    transcription: TranscriptionSettings = TranscriptionSettings()
    layout: LayoutSettings = LayoutSettings()
    encoding: ClipEncodingSettings = ClipEncodingSettings()
    audio: ClipAudioSettings = ClipAudioSettings()
    bleep: BleepSettings = BleepSettings()
    source: SourceQualitySettings = SourceQualitySettings()
    captions: CaptionSettings = CaptionSettings()
    hook_title: HookTitleSettings = HookTitleSettings()
    credit: CreditSettings = CreditSettings()
    analysis: AnalysisSettings = AnalysisSettings()
    automation: AutomationSettings = AutomationSettings()
    live_recording: LiveRecordingSettings = LiveRecordingSettings()
    framing: FramingSettings = FramingSettings(enabled=False)
    ranking: RankingSettings = RankingSettings(enabled=False, use_editor_model=False)
    trimming: TrimSettings = TrimSettings(enabled=False)
    titles: TitleSettings = TitleSettings()
    qa: QASettings = QASettings()
    effects: EffectSettings = EffectSettings()
    packaging: PackagingSettings = PackagingSettings()


def is_clips_channel(config):
    """
    Check whether a channel cuts stream clips (instead of writing news scripts).

    Args:
        config (dict): The channel's YAML settings.

    Returns:
        bool: True when the YAML says `type: clips`.
    """
    return config.get("type") == "clips"


def describe_validation_error(error):
    """
    Turn pydantic's detailed error into one short readable sentence.

    Args:
        error (ValidationError): The error pydantic raised.

    Returns:
        str: For example "streamers → 0 → permission_date: Field required".
    """
    first_problem = error.errors()[0]
    place_parts = []
    for part in first_problem["loc"]:
        place_parts.append(str(part))
    place = " → ".join(place_parts)
    return f"{place}: {first_problem['msg']}"


def check_facecam_fits(streamer):
    """
    Make sure a streamer's webcam box lies inside the stream picture.

    Args:
        streamer (ApprovedStreamer): The streamer to check.

    Raises:
        ValueError: If the box reaches past the right or bottom edge.
    """
    box = streamer.facecam
    if box is None:
        return
    # A small tolerance allows values like 0.75 + 0.25 that add up to
    # 1.0000000001 because of how computers store decimals.
    if box.x + box.width > 1.001 or box.y + box.height > 1.001:
        raise ValueError(f"The facecam box for '{streamer.username}' reaches past the edge "
                         "of the picture (x + width and y + height must be at most 1).")


def check_permission_dates(streamers):
    """
    Refuse permission dates in the future (they would mean "not agreed yet").

    Args:
        streamers (list[ApprovedStreamer]): The approved list.

    Raises:
        ValueError: If any permission date is after today.
    """
    today = date.today()
    for streamer in streamers:
        if streamer.permission_date > today:
            raise ValueError(f"The permission date for '{streamer.username}' is in the future. "
                             "Only add a streamer after they have agreed.")


def check_no_repeats(streamers):
    """
    Refuse the same streamer listed twice (it would be unclear which entry counts).

    Args:
        streamers (list[ApprovedStreamer]): The approved list.

    Raises:
        ValueError: If a username appears more than once.
    """
    seen = set()
    for streamer in streamers:
        # The same name on Twitch and on Kick can be two different people.
        key = (streamer.platform, streamer.username.lower())
        if key in seen:
            raise ValueError(f"'{streamer.username}' ({streamer.platform}) is listed twice under streamers.")
        seen.add(key)


def load_clip_settings(config):
    """
    Read and check the clip settings from a channel's YAML.

    Args:
        config (dict): The channel's YAML settings (from load_channel_config).

    Returns:
        ClipChannelSettings: The checked settings.

    Raises:
        ValueError: With a plain message if the channel is not a clips
                    channel or a setting is wrong.

    Example:
        settings = load_clip_settings(load_channel_config("twitch_clips"))
        settings.clips.moment_max_seconds   # → 60.0
    """
    if not is_clips_channel(config):
        raise ValueError("This channel is not a clips channel (its YAML needs `type: clips`).")

    # Only pass the sections this file understands; the rest (name,
    # schedule, publishing...) is used by other parts of the program.
    sections = {}
    known_sections = ["streamers", "clips", "discovery", "transcription", "layout", "encoding",
                      "audio", "bleep", "source", "captions", "hook_title", "credit", "analysis", "automation",
                      "live_recording", "framing", "ranking", "trimming", "titles", "qa", "effects", "packaging"]
    for key in known_sections:
        if config.get(key) is not None:
            sections[key] = config[key]

    try:
        settings = ClipChannelSettings(**sections)
    except ValidationError as error:
        raise ValueError(f"Fix the channel YAML: {describe_validation_error(error)}") from error

    if settings.clips.moment_min_seconds >= settings.clips.moment_max_seconds:
        raise ValueError("clips.moment_min_seconds must be smaller than clips.moment_max_seconds.")
    for streamer in settings.streamers:
        check_facecam_fits(streamer)
    check_permission_dates(settings.streamers)
    check_no_repeats(settings.streamers)
    expected_weights = {"entertainment", "hook", "audience", "clarity", "audio", "visual"}
    if set(settings.analysis.weights) != expected_weights:
        raise ValueError("analysis.weights must contain entertainment, hook, audience, clarity, audio and visual.")
    total = 0
    for weight in settings.analysis.weights.values():
        if not 0 <= weight <= 100:
            raise ValueError("Analysis weights must be finite numbers between 0 and 100.")
        total += weight
    if total <= 0:
        raise ValueError("At least one analysis weight must be positive.")
    live = settings.live_recording
    if live.stop_below_free_gb >= live.min_free_gb_to_start:
        raise ValueError("live_recording.stop_below_free_gb must be smaller than min_free_gb_to_start.")
    if live.overlap_seconds >= live.chunk_minutes * 60:
        raise ValueError("live_recording.overlap_seconds must be shorter than one chunk.")
    return settings


def credit_for(streamer):
    """
    Write the credit shown on screen and in captions for a streamer.

    Args:
        streamer (ApprovedStreamer): An approved streamer.

    Returns:
        str: For example "twitch.tv/coolstreamer", "kick.com/coolstreamer" or "youtube.com/@coolstreamer".
    """
    if streamer.platform == "youtube":
        return f"youtube.com/@{streamer.username.lower()}"
    return f"{STREAM_PLATFORM_ADDRESSES[streamer.platform]}/{streamer.username.lower()}"


def display_name_for(streamer):
    """
    Get the name a streamer is shown as in hook titles.

    Args:
        streamer (ApprovedStreamer): An approved streamer.

    Returns:
        str: Their display_name, or their username when none is set.
    """
    return streamer.display_name or streamer.username


def find_approved_streamer(settings, username, channel_key="twitch_clips", platform=None):
    """
    Get a streamer's approval entry, or refuse if they are not approved.

    Logins are not case-sensitive, so "CoolStreamer" and
    "coolstreamer" are the same person.

    Args:
        settings (ClipChannelSettings): From load_clip_settings().
        username (str): The login to check.
        channel_key (str): The channel's file name, used in the message.
        platform (str or None): "twitch" or "kick" to match only that
                                site, or None to accept either.

    Returns:
        ApprovedStreamer: The streamer's entry.

    Raises:
        StreamerNotApproved: If the streamer is not on the approved list.

    Example:
        find_approved_streamer(settings, "randomperson")
        # → StreamerNotApproved: 'randomperson' is not an approved streamer ...
    """
    wanted = username.strip().lower()
    for streamer in settings.streamers:
        if streamer.username.lower() != wanted:
            continue
        if streamer.enabled and (platform is None or streamer.platform == platform):
            return streamer
    where = ""
    if platform:
        where = f" on {platform.capitalize()}"
    raise StreamerNotApproved(
        f"'{username}'{where} is not an approved streamer for this channel. "
        f"Add them to the streamers list in channels/{channel_key}.yaml with a "
        "permission note and date — only after they have agreed in writing."
    )


def approved_streamers_to_use(settings, only_username=None, channel_key="twitch_clips"):
    """
    Decide which streamers a run should process.

    Args:
        settings (ClipChannelSettings): From load_clip_settings().
        only_username (str or None): Process just this streamer (it must
                                     be approved), or None for all of them.
        channel_key (str): The channel's file name, used in messages.

    Returns:
        list[ApprovedStreamer]: The streamers to process.

    Raises:
        StreamerNotApproved: If only_username is not approved.
        ValueError: If the approved list is empty.
    """
    if only_username:
        return [find_approved_streamer(settings, only_username, channel_key)]
    if not settings.streamers:
        raise ValueError(f"No approved streamers yet. Add one to channels/{channel_key}.yaml "
                         "after they agree in writing.")
    result = []
    for streamer in settings.streamers:
        if streamer.enabled:
            result.append(streamer)
    return result

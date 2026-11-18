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

from pydantic import BaseModel, Field, ValidationError


# The streaming sites a streamer can be on, and their channel web address.
# The credit on screen and in captions is "<address>/<username>".
STREAM_PLATFORM_ADDRESSES = {"twitch": "twitch.tv", "kick": "kick.com"}


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
        platform: "twitch" (clips fetched automatically) or "kick" (clips added by hand).
        permission_note: How and where they agreed, e.g. "Said yes in a Twitch DM".
        permission_date: The day they agreed.
        permission_proof: Optional path to your screenshot of the agreement.
        facecam: Optional webcam position; without it the video is a centred crop.
    """
    # Logins are letters, numbers, underscores (and hyphens on Kick).
    username: str = Field(min_length=1, max_length=25, pattern=r"^[A-Za-z0-9_-]+$")
    platform: str = Field(default="twitch", pattern=r"^(twitch|kick)$")
    permission_note: str = Field(min_length=5, max_length=500)
    permission_date: date
    permission_proof: str = ""
    facecam: FacecamBox | None = None
    enabled: bool = True
    recording_directory: str = ""


class ClipLimits(BaseModel):
    """Which clips to look at and how long finished videos may be (see the YAML for each field)."""
    hours_back: int = Field(default=24, ge=1, le=168)
    clips_per_streamer: int = Field(default=5, ge=1, le=20)
    min_views: int = Field(default=0, ge=0)
    skip_shorter_than_seconds: float = Field(default=8, ge=1, le=60)
    moment_min_seconds: float = Field(default=20, ge=3, le=120)
    moment_max_seconds: float = Field(default=60, ge=5, le=180)


class TranscriptionSettings(BaseModel):
    """Which local Whisper model to use and the language spoken on the streams."""
    whisper_model: str = Field(default="small.en", pattern=r"^[a-z0-9.\-]+$")
    language: str = Field(default="en", min_length=2, max_length=5)


class LayoutSettings(BaseModel):
    """How the vertical video is split between webcam and gameplay."""
    facecam_panel_height: int = Field(default=720, ge=300, le=1200)
    gameplay_focus_x: float = Field(default=0.5, ge=0, le=1)
    template: str = Field(default="gaming", pattern=r"^(gaming|reaction|conversation)$")


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
    center_y: int = Field(default=1330, ge=300, le=1600)
    font_path: str = ""
    outline_color: str = Field(default="#000000", pattern=r"^#[0-9A-Fa-f]{6}$")
    outline_width: int = Field(default=6, ge=0, le=20)


class HookTitleSettings(BaseModel):
    """How the hook title at the start of the video looks."""
    seconds: float = Field(default=3.0, ge=0.5, le=10)
    font_size: int = Field(default=80, ge=30, le=160)
    max_words: int = Field(default=8, ge=2, le=15)


class CreditSettings(BaseModel):
    """How the on-screen streamer credit looks."""
    font_size: int = Field(default=40, ge=20, le=100)


class ClipChannelSettings(BaseModel):
    """
    Everything a clips channel's YAML file controls, already checked.

    Sections that are missing from the YAML use the defaults above.
    """
    streamers: list[ApprovedStreamer] = []
    clips: ClipLimits = ClipLimits()
    transcription: TranscriptionSettings = TranscriptionSettings()
    layout: LayoutSettings = LayoutSettings()
    captions: CaptionSettings = CaptionSettings()
    hook_title: HookTitleSettings = HookTitleSettings()
    credit: CreditSettings = CreditSettings()
    analysis: AnalysisSettings = AnalysisSettings()
    automation: AutomationSettings = AutomationSettings()


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
    for key in ("streamers", "clips", "transcription", "layout", "captions", "hook_title", "credit", "analysis", "automation"):
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
    return settings


def credit_for(streamer):
    """
    Write the credit shown on screen and in captions for a streamer.

    Args:
        streamer (ApprovedStreamer): An approved streamer.

    Returns:
        str: For example "twitch.tv/coolstreamer" or "kick.com/coolstreamer".
    """
    return f"{STREAM_PLATFORM_ADDRESSES[streamer.platform]}/{streamer.username.lower()}"


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

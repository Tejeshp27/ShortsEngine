# =============================================================
# src/video/sound_design.py — music, sound effects and loudness
#
# Professional short videos are never just a voice. Under the
# narration there is quiet music, and small sounds mark the edits:
# a whoosh when the section changes, a pop when a key number
# appears, an impact on the very first frame.
#
# This file builds the video's whole soundtrack BEFORE any frame is
# drawn, so the quality gate can check its loudness first:
#
#   1. Find sounds: only sounds licensed for commercial use.
#        - Effects: Freesound, filtered to CC0 (public domain).
#        - Music: the channel's own licensed tracks in
#          assets/music/<channel>/ (listed in credits.yaml), or else a
#          CC0 track from Freesound.
#      Every sound's credit is returned for the video's credits file.
#   2. Mix: voice at full volume, music quiet and "ducking" (it gets
#      even quieter whenever the voice speaks), effects at their times.
#   3. Loudness: bring the mix to -14 LUFS, the level TikTok, Reels and
#      YouTube play at, in two passes (measure, then adjust exactly).
#
# How it fits in:
#   renderer.py → build_soundtrack() (here) → quality gate → FFmpeg
# =============================================================

import hashlib
import json
import os
import re
import subprocess
import urllib.parse
import urllib.request

import yaml

FREESOUND_SEARCH_URL = "https://freesound.org/apiv2/search/text/"
SOUND_CACHE = os.path.join("cache", "sounds")
MUSIC_FOLDER = os.path.join("assets", "music")

# Freesound's name for the public-domain licence (free for any use, no credit needed).
CC0_FILTER = 'license:"Creative Commons 0"'

# Sound settings used when the channel YAML does not set its own
# (under video.sound).
DEFAULT_SOUND = {
    "enabled": True,
    "hook_query": "whoosh impact",        # first-frame hit
    "transition_query": "whoosh",          # section changes
    "pop_query": "pop",                    # key numbers and stamps
    "music_query": "ambient electronic loop",  # used only if no own tracks
    "music": True,
    "music_volume": 0.12,                  # music level before ducking (voice = 1.0)
    "effects_volume": 0.35,                # effect level (voice = 1.0)
    "target_lufs": -14.0,                  # final loudness for social media
    "true_peak": -1.5,                     # highest allowed peak, in dBTP
    "min_effect_gap": 1.2,                 # seconds between two effects, at least
}

# Templates whose appearance gets a "pop" (a number or a stamped fact).
POP_TEMPLATES = {"big_number", "stamp", "bar_chart"}


def load_sound_settings(channel_video_settings):
    """
    Read the channel's sound settings, filling gaps with the defaults.

    Args:
        channel_video_settings (dict or None): The channel's "video" block.

    Returns:
        dict: Every key of DEFAULT_SOUND.
    """
    settings = dict(DEFAULT_SOUND)
    channel_sound = (channel_video_settings or {}).get("sound") or {}
    for key in settings:
        if key in channel_sound:
            settings[key] = channel_sound[key]
    return settings


# ---------------------------------------------------------------------------
# Finding licensed sounds
# ---------------------------------------------------------------------------

def freesound_search(query, min_seconds, max_seconds):
    """
    Search Freesound for CC0 (public-domain) sounds of a given length.

    Args:
        query (str): What to look for, e.g. "whoosh".
        min_seconds (float): Shortest acceptable length.
        max_seconds (float): Longest acceptable length.

    Returns:
        list[dict]: Matching sounds (best rated first), or [] if the key
                    is missing or the search failed.
    """
    api_key = os.getenv("FREESOUND_API_KEY", "")
    if not api_key:
        return []
    params = urllib.parse.urlencode({
        "query": query,
        "token": api_key,
        "fields": "id,name,username,previews,duration,license,url",
        "filter": f"{CC0_FILTER} duration:[{min_seconds} TO {max_seconds}]",
        "page_size": 10,
        "sort": "rating_desc",
    })
    try:
        request = urllib.request.Request(f"{FREESOUND_SEARCH_URL}?{params}",
                                         headers={"User-Agent": "ShortsEngine/0.1"})
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.loads(response.read()).get("results", [])
    except Exception as error:
        print(f"  Freesound search for '{query}' failed: {error}")
        return []


def get_cc0_sound(query, min_seconds, max_seconds, kind):
    """
    Get one CC0 sound for a search, downloading it only the first time.

    Args:
        query (str): What to look for, e.g. "whoosh".
        min_seconds (float): Shortest acceptable length.
        max_seconds (float): Longest acceptable length.
        kind (str): "effect" or "music" (kept in the credit).

    Returns:
        dict or None: {"path": .mp3 file, "credit": {...}} or None.

    Example:
        whoosh = get_cc0_sound("whoosh", 0.2, 1.5, "effect")
        whoosh["credit"]["licence"]   # → "CC0 1.0 (public domain)"
    """
    key = f"{kind}:{query}:{min_seconds}:{max_seconds}"
    fingerprint = hashlib.sha256(key.encode()).hexdigest()[:16]
    sound_path = os.path.join(SOUND_CACHE, f"cc0_{fingerprint}.mp3")
    credit_path = os.path.join(SOUND_CACHE, f"cc0_{fingerprint}.json")

    if os.path.exists(sound_path) and os.path.exists(credit_path):
        with open(credit_path, "r", encoding="utf-8") as f:
            return {"path": sound_path, "credit": json.load(f)}

    os.makedirs(SOUND_CACHE, exist_ok=True)
    for sound in freesound_search(query, min_seconds, max_seconds):
        preview = sound.get("previews", {}).get("preview-hq-mp3")
        if not preview:
            continue
        try:
            request = urllib.request.Request(preview, headers={"User-Agent": "ShortsEngine/0.1"})
            with urllib.request.urlopen(request, timeout=30) as response:
                with open(sound_path, "wb") as f:
                    f.write(response.read())
        except Exception as error:
            print(f"  Could not download sound {sound.get('id')}: {error}")
            continue
        credit = {
            "kind": kind,
            "source": "Freesound",
            "title": sound.get("name", ""),
            "author": sound.get("username", "Unknown"),
            "licence": "CC0 1.0 (public domain)",
            "licence_url": sound.get("license", "https://creativecommons.org/publicdomain/zero/1.0/"),
            "page_url": sound.get("url", ""),
        }
        with open(credit_path, "w", encoding="utf-8") as f:
            json.dump(credit, f, indent=2)
        return {"path": sound_path, "credit": credit}
    return None


def find_own_music(channel_key):
    """
    Pick a music track the channel owner added and licensed themselves.

    Tracks live in assets/music/<channel>/ and each must be listed in that
    folder's credits.yaml with its licence. A track missing from the list
    is ignored, because we can't prove we may use it.

    Args:
        channel_key (str): The channel's file name, e.g. "news".

    Returns:
        dict or None: {"path", "credit"} for the first listed track, or None.
    """
    folder = os.path.join(MUSIC_FOLDER, channel_key)
    credits_path = os.path.join(folder, "credits.yaml")
    if not os.path.exists(credits_path):
        return None
    with open(credits_path, "r", encoding="utf-8") as f:
        listed = yaml.safe_load(f) or {}
    for file_name, details in listed.items():
        path = os.path.join(folder, file_name)
        if os.path.exists(path) and isinstance(details, dict) and details.get("licence"):
            credit = {"kind": "music", "source": details.get("source", "Own library"),
                      "title": details.get("title", file_name),
                      "author": details.get("author", "Unknown"),
                      "licence": details["licence"],
                      "licence_url": details.get("licence_url", ""),
                      "page_url": details.get("page_url", "")}
            return {"path": path, "credit": credit}
    return None


# ---------------------------------------------------------------------------
# Planning when effects play
# ---------------------------------------------------------------------------

def plan_effect_times(scenes, settings):
    """
    Decide when each sound effect plays.

    Args:
        scenes (list[dict]): Storyboard scenes with "start",
                             "starts_section" and "template".
        settings (dict): Output of load_sound_settings().

    Returns:
        list[dict]: {"time": seconds, "kind": "hook" | "transition" | "pop"},
                    in time order, never closer together than min_effect_gap.

    Example:
        plan_effect_times(scenes, settings)[0]   # → {"time": 0.0, "kind": "hook"}
    """
    wanted = [{"time": 0.0, "kind": "hook"}]
    for scene_index, scene in enumerate(scenes):
        if scene_index == 0:
            continue
        if scene.get("starts_section"):
            # The whoosh peaks as the section wipe covers the screen.
            wanted.append({"time": max(0.0, scene["start"] - 0.2), "kind": "transition"})
        elif scene.get("template") in POP_TEMPLATES:
            wanted.append({"time": scene["start"] + 0.1, "kind": "pop"})

    planned = []
    for effect in wanted:
        if planned and effect["time"] - planned[-1]["time"] < settings["min_effect_gap"]:
            continue
        planned.append(effect)
    return planned


# ---------------------------------------------------------------------------
# Measuring and mixing
# ---------------------------------------------------------------------------

def measure_loudness(audio_path):
    """
    Measure a file's loudness the way social platforms do (EBU R128).

    Args:
        audio_path (str): Any audio or video file.

    Returns:
        dict: {"lufs": integrated loudness, "true_peak": highest peak in dBTP}.
              Values are None if they could not be measured.
    """
    result = subprocess.run(
        ["ffmpeg", "-nostdin", "-hide_banner", "-i", audio_path,
         "-af", "ebur128=peak=true", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    # The summary at the end of FFmpeg's log holds the final numbers.
    summary = result.stderr[result.stderr.rfind("Summary:"):]
    lufs = re.search(r"I:\s+(-?[\d.]+) LUFS", summary)
    peak = re.search(r"Peak:\s+(-?[\d.]+) dBFS", summary)
    return {"lufs": float(lufs.group(1)) if lufs else None,
            "true_peak": float(peak.group(1)) if peak else None}


def build_mix_command(voice_path, duration, music, effects, settings, output_path):
    """
    Build the FFmpeg command that mixes voice, music and effects.

    Args:
        voice_path (str): The (paced) voiceover.
        duration (float): Length of the finished video, in seconds.
        music (dict or None): {"path", ...} from find_own_music/get_cc0_sound.
        effects (list[dict]): {"time", "path"} for each effect to play.
        settings (dict): Output of load_sound_settings().
        output_path (str): Where to write the (not yet loudness-adjusted) mix.

    Returns:
        list[str]: The FFmpeg command.
    """
    command = ["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", voice_path]
    # Every input is converted to the same format (44.1 kHz stereo) first.
    same_format = "aresample=44100,aformat=sample_fmts=fltp:channel_layouts=stereo"
    # The voice is padded with silence to the full length (the end card).
    parts = [f"[0:a]{same_format},apad=whole_dur={duration:.3f}[voice_full]",
             "[voice_full]asplit=2[voice][duck_key]"]
    mix_inputs = ["[voice]"]
    next_input = 1

    if music is not None:
        command += ["-stream_loop", "-1", "-i", music["path"]]
        # sidechaincompress: whenever the voice (the "key") is loud, turn the
        # music down further. This is called ducking.
        parts.append(f"[{next_input}:a]{same_format},atrim=0:{duration:.3f},"
                     f"volume={settings['music_volume']}[music_raw]")
        parts.append("[music_raw][duck_key]sidechaincompress=threshold=0.02:ratio=8:"
                     "attack=20:release=400[music]")
        mix_inputs.append("[music]")
        next_input += 1
    else:
        parts.append("[duck_key]anullsink")

    for effect in effects:
        command += ["-i", effect["path"]]
        delay_ms = int(effect["time"] * 1000)
        label = f"[effect{next_input}]"
        parts.append(f"[{next_input}:a]{same_format},volume={settings['effects_volume']},"
                     f"adelay={delay_ms}|{delay_ms}{label}")
        mix_inputs.append(label)
        next_input += 1

    # normalize=0 keeps every track at the level set above (amix would
    # otherwise turn everything down as more tracks are added).
    parts.append("".join(mix_inputs) + f"amix=inputs={len(mix_inputs)}:normalize=0:"
                 f"duration=first,atrim=0:{duration:.3f}[mix]")
    command += ["-filter_complex", ";".join(parts), "-map", "[mix]",
                "-ar", "44100", output_path]
    return command


def normalize_loudness(input_path, output_path, settings):
    """
    Bring a mix to the target loudness exactly, in two passes.

    Pass 1 measures the mix; pass 2 adjusts it using those measurements.
    One pass would guess, and could pump the volume up and down.

    Args:
        input_path (str): The mix from build_mix_command().
        output_path (str): Where to save the finished soundtrack (.wav).
        settings (dict): Output of load_sound_settings() (target_lufs, true_peak).
    """
    target = f"I={settings['target_lufs']}:TP={settings['true_peak']}:LRA=11"
    first = subprocess.run(
        ["ffmpeg", "-nostdin", "-hide_banner", "-i", input_path,
         "-af", f"loudnorm={target}:print_format=json", "-f", "null", "-"],
        capture_output=True, text=True, check=True,
    )
    # The measurements are printed as a JSON block at the end of the log.
    measured = json.loads(first.stderr[first.stderr.rfind("{"):first.stderr.rfind("}") + 1])
    second_filter = (
        f"loudnorm={target}:measured_I={measured['input_i']}:"
        f"measured_TP={measured['input_tp']}:measured_LRA={measured['input_lra']}:"
        f"measured_thresh={measured['input_thresh']}:offset={measured['target_offset']}:linear=true"
    )
    subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", input_path,
         "-af", second_filter, "-ar", "44100", output_path],
        check=True,
    )


def gather_sounds(scenes, settings, channel_key):
    """
    Find the music and effect files for a video, with their credits.

    Args:
        scenes (list[dict]): Storyboard scenes with times.
        settings (dict): Output of load_sound_settings().
        channel_key (str): The channel's file name, e.g. "news".

    Returns:
        tuple: (music or None, list of {"time", "path"}, list of credits)
    """
    credits = []
    music = None
    if settings["music"]:
        music = find_own_music(channel_key) or get_cc0_sound(settings["music_query"], 45, 600, "music")
        if music is not None:
            credits.append(music["credit"])

    sounds_by_kind = {
        "hook": get_cc0_sound(settings["hook_query"], 0.3, 2.5, "effect"),
        "transition": get_cc0_sound(settings["transition_query"], 0.2, 1.5, "effect"),
        "pop": get_cc0_sound(settings["pop_query"], 0.05, 0.8, "effect"),
    }
    effects = []
    for planned in plan_effect_times(scenes, settings):
        sound = sounds_by_kind.get(planned["kind"])
        if sound is not None:
            effects.append({"time": planned["time"], "path": sound["path"]})
    for sound in sounds_by_kind.values():
        if sound is not None and sound["credit"] not in credits:
            credits.append(sound["credit"])
    return music, effects, credits


def build_soundtrack(voice_path, duration, scenes, channel_video_settings, channel_key, output_path):
    """
    Make the video's finished soundtrack: voice, music, effects, loudness.

    Args:
        voice_path (str): The (paced) voiceover.
        duration (float): Length of the finished video, in seconds.
        scenes (list[dict]): Storyboard scenes with times and sections.
        channel_video_settings (dict): The channel's "video" block.
        channel_key (str): The channel's file name, e.g. "news".
        output_path (str): Where to save the soundtrack (.wav).

    Returns:
        dict: {"path": output_path, "credits": [...], "loudness": {...},
               "effects": number of effects, "music": True/False}

    Example:
        track = build_soundtrack("cache/audio/a.wav", 80.0, scenes, config["video"],
                                 "news", "cache/render/a_mix.wav")
    """
    settings = load_sound_settings(channel_video_settings)
    music, effects, credits = None, [], []
    if settings["enabled"]:
        music, effects, credits = gather_sounds(scenes, settings, channel_key)

    raw_mix = output_path.replace(".wav", "_raw.wav")
    subprocess.run(build_mix_command(voice_path, duration, music, effects, settings, raw_mix),
                   check=True)
    normalize_loudness(raw_mix, output_path, settings)
    os.remove(raw_mix)

    loudness = measure_loudness(output_path)
    print(f"  Soundtrack ready: {'music + ' if music else ''}{len(effects)} effects, "
          f"{loudness['lufs']} LUFS")
    return {"path": output_path, "credits": credits, "loudness": loudness,
            "effects": len(effects), "music": music is not None}

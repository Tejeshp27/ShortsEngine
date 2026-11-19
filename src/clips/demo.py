# =============================================================
# Credential-free end-to-end demo using our own generated video.
# macOS uses a system voice and REAL local Whisper transcription.
# Other hosts use an explicit synthetic tone/timing fixture. An isolated
# demo database is used; publishing is always a no-network dry run.
# Run: python -m src.clips.demo [--fixture] [--output output/clips_demo]
# =============================================================

import argparse
import json
import shutil
import subprocess
from datetime import date
from pathlib import Path


def demo_config():
    """
    Return a free demo config for locally generated content, with no live creator or upload credentials.

    Returns:
        A free configuration describing original synthetic test media.
    """
    return {"name": "Synthetic clips demo", "type": "clips", "enabled": True,
            "streamers": [{"username": "shortsengine_demo", "platform": "kick",
                           "permission_note": "Generated synthetic test media; no real streamer content",
                           "permission_date": date.today().isoformat()}],
            "clips": {"moment_min_seconds": 10, "moment_max_seconds": 22},
            "analysis": {"minimum_score": 0, "candidate_seconds": 16, "max_clips_per_recording": 2},
            "automation": {"require_approval": True, "publish_dry_run": True},
            "layout": {"mode": "fit-blur"}, "platforms": ["youtube", "tiktok"],
            "publishing": {"hashtags": ["#demo"]}, "models": {}, "llm": {"max_calls_per_run": 0}}


def make_sample(folder, fixture):
    """
    Create original demo video in a folder; return its path and optional explicitly synthetic timed words.

    Args:
        folder: The local directory that will hold generated demo files.
        fixture: True to use synthetic timing data instead of speech recognition.

    Returns:
        The generated video path and optional synthetic timed words.
    """
    speech = ("Wait, why is the timer still running? I thought we already finished the race. "
              "Watch the little blue square on the screen. It is going the wrong way. "
              "Wow! No way! We just crossed the finish line backwards. That was unbelievable. "
              "Now let us try one more round. The final jump is harder than it looks. "
              "Wait, what? Amazing! We landed on the smallest platform. I cannot believe that worked.")
    video = folder / "sample.mp4"
    words = None
    audio = folder / "speech.aiff"
    duration = 32
    if not fixture and shutil.which("say"):
        subprocess.run(["say", "-r", "175", "-o", str(audio), speech], check=True)
        duration = sample_speech_duration(audio)
        sound = ["-i", str(audio)]
    else:
        sound = ["-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100:duration=32"]
        words = []
        for index, text in enumerate(speech.split()):
            words.append({"word": text, "start": round(index * 0.4, 2), "end": round(index * 0.4 + 0.32, 2)})
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i",
                    f"testsrc2=size=960x540:rate=30:duration={duration}", *sound, "-shortest", "-c:v", "libx264",
                    "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", str(video)], check=True)
    return video, words


def sample_speech_duration(audio):
    """
    Check the generated demo speech before starting video creation and return its length in seconds.

    Args:
        audio: The AIFF file written by the macOS speech command.

    Returns:
        A positive duration of at most two minutes; raises RuntimeError when speech is empty or unusable.
    """
    result = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a:0",
                             "-show_entries", "stream=duration", "-of", "json", str(audio)],
                            capture_output=True, text=True, check=True)
    streams = json.loads(result.stdout).get("streams", [])
    duration = 0
    if streams:
        try:
            duration = float(streams[0].get("duration", 0))
        except (ValueError, TypeError):
            duration = 0
    # Sandboxed macOS speech can return success but write no samples.
    # A finite source also prevents FFmpeg waiting forever on empty audio.
    if not 0 < duration <= 120:
        raise RuntimeError("macOS speech produced no usable demo audio. Run the demo with speech access "
                           "or use --fixture for a synthetic timing demonstration.")
    return duration


def main():
    """
    Read demo options and exercise ingestion, transcription, scoring, render, quality, review and publishing dry run.

    Returns:
        Nothing; output paths and results are printed for the user.
    """
    from src.clips.maker import make_clip_video
    from src.clips.metadata import create_metadata
    from src.clips.recordings import analyze_recording, import_recording, recording_row, recording_words
    from src.clips.settings import load_clip_settings
    from src.clips.store import get_clip
    from src.clips.transcribe import TRANSCRIPT_CACHE_FOLDER
    from src.generation.llm import LLMClient
    from src.storage import database

    parser = argparse.ArgumentParser(description="Original local media demo. Never posts or uses paid APIs.")
    parser.add_argument("--fixture", action="store_true", help="Use synthetic audio/timing fixture instead of speech recognition")
    parser.add_argument("--output", default="output/clips_demo", help="Demo database/sample/report folder")
    args = parser.parse_args()
    folder = Path(args.output).resolve()
    folder.mkdir(parents=True, exist_ok=True)
    database.DB_PATH = str(folder / "demo.db")
    database.create_tables()
    config = demo_config()
    settings = load_clip_settings(config)
    video, fixture_words = make_sample(folder, args.fixture)
    recording_id = import_recording("clips_demo", config, video, "shortsengine_demo", "kick", True)
    recording = recording_row(recording_id)
    if fixture_words is not None:
        cache = Path(TRANSCRIPT_CACHE_FOLDER)
        cache.mkdir(parents=True, exist_ok=True)
        filename = f"recording_{recording['fingerprint']}_small.en_en.json"
        (cache / filename).write_text(json.dumps(fixture_words))
    words = recording_words(recording, settings)
    print(f"Transcribed {len(words)} words ({'synthetic timing fixture' if fixture_words is not None else 'real whisper.cpp'}).", flush=True)
    clip_ids = analyze_recording(recording_id, config)
    llm = LLMClient(dry_run=True, max_calls=0)
    report = {"transcription": "fixture" if fixture_words is not None else "whisper.cpp", "clips": [],
              "publishing": "DRY RUN: YouTube upload and TikTok inbox delivery simulated; no network requests."}
    for clip_id in clip_ids:
        connection = database.get_connection()
        try:
            # This is our own synthetic media; never imply it came from a real Kick user.
            connection.execute("UPDATE clips SET credit_text='example.com/shortsengine-demo' WHERE id=?", (clip_id,))
            connection.commit()
        finally:
            connection.close()
        clip = get_clip(clip_id)
        create_metadata(clip, words, config, settings)
        video_id = make_clip_video("clips_demo", config, settings, get_clip(clip_id), llm)
        clip = get_clip(clip_id)
        from src.storage.queries import get_video_by_id
        draft = get_video_by_id(video_id)
        report["clips"].append({"video": draft["video_path"], "score": clip["highlight_score"],
                                "quality": json.loads(clip["quality_json"]), "review": draft["status"]})
        print(f"Draft {video_id}: {draft['video_path']} — score {clip['highlight_score']}", flush=True)
        from src.dashboard.worker import publish_saved_video
        simulated = publish_saved_video({"dry_run": True, "payload": {"video_id": video_id, "config": config}})
        report["clips"][-1]["publish_plan"] = simulated["message"]
    (folder / "report.json").write_text(json.dumps(report, indent=2))
    print(report["publishing"])
    print(f"Report: {folder / 'report.json'}")


if __name__ == "__main__":
    main()

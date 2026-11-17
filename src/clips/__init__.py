# =============================================================
# src/clips/ — the clips channel type (Twitch highlights)
#
# A clips channel turns real stream moments into vertical
# short videos. The steps, each in its own file:
#
#   settings.py     read the channel YAML and check permissions
#   twitch_login.py let a streamer allow us to download their clips
#   store.py        save clips in the database (no clip is used twice)
#   finder.py       find top clips and download them (official API)
#   transcribe.py   turn the clip's speech into timed words (local Whisper)
#   moments.py      let the cheap AI pick the best 20-60 seconds + hook title
#   maker.py        render the vertical video and send it to review
#   __main__.py     the command line: python -m src.clips ...
# =============================================================

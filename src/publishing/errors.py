# =============================================================
# src/publishing/errors.py — the error shared by every publisher
#
# Each publisher (instagram.py, facebook.py, youtube.py) raises
# NothingPublishedError when an upload fails at a step where nothing
# can have become public yet. The scheduler then knows it is safe to
# try again. Any other error might have happened after the platform
# accepted the video, so those uploads must be checked by hand first.
# =============================================================


class NothingPublishedError(RuntimeError):
    """
    An upload failed at a step where nothing has been made public yet.

    The scheduler treats this error as safe to retry. Every other error
    might have happened after the platform accepted the post, so those
    uploads are marked "uncertain" and must be checked by hand first.
    """

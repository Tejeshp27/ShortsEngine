# =============================================================
# src/video/animation/easing.py — maths that makes motion look smooth
#
# If an object moves at a constant speed it looks robotic. Real
# motion speeds up and slows down. An "easing function" takes a
# number from 0.0 (animation just started) to 1.0 (animation
# finished) and bends it, so the movement starts fast and settles
# gently, or overshoots and bounces back.
#
# Every animation in scenes.py and overlays.py is built from the
# small functions in this file.
# =============================================================


def clamp(value, low=0.0, high=1.0):
    """
    Keep a number inside a range.

    Args:
        value (float): The number to limit.
        low (float): The smallest allowed result.
        high (float): The largest allowed result.

    Returns:
        float: value, or low/high if value was outside the range.

    Example:
        clamp(1.7)    # → 1.0
        clamp(-0.2)   # → 0.0
    """
    if value < low:
        return low
    if value > high:
        return high
    return value


def progress(now, start, duration):
    """
    Work out how far through an animation we are, from 0.0 to 1.0.

    Args:
        now (float): The current time in seconds.
        start (float): The time the animation begins.
        duration (float): How long the animation lasts, in seconds.

    Returns:
        float: 0.0 before the animation starts, 1.0 after it ends,
               and a value in between while it is running.

    Example:
        progress(now=1.5, start=1.0, duration=1.0)   # → 0.5 (halfway)
    """
    if duration <= 0:
        # A zero-length animation is simply "finished" once it has started.
        if now >= start:
            return 1.0
        return 0.0
    return clamp((now - start) / duration)


def lerp(start_value, end_value, amount):
    """
    Blend between two numbers ("linear interpolation").

    Args:
        start_value (float): The value when amount is 0.0.
        end_value (float): The value when amount is 1.0.
        amount (float): How far to move from start to end (0.0 to 1.0).

    Returns:
        float: A value between start_value and end_value.

    Example:
        lerp(100, 200, 0.25)   # → 125.0
    """
    return start_value + (end_value - start_value) * amount


def ease_out_cubic(amount):
    """
    Start fast and slow down gently at the end.

    Used for things sliding into place.

    Args:
        amount (float): Raw progress from 0.0 to 1.0.

    Returns:
        float: Eased progress from 0.0 to 1.0.
    """
    inverse = 1.0 - amount
    return 1.0 - inverse * inverse * inverse


def ease_in_cubic(amount):
    """
    Start slowly and speed up at the end.

    Used for things slamming onto the screen.

    Args:
        amount (float): Raw progress from 0.0 to 1.0.

    Returns:
        float: Eased progress from 0.0 to 1.0.
    """
    return amount * amount * amount


def ease_out_back(amount):
    """
    Overshoot the target slightly, then settle back — a "pop" effect.

    The result goes a little above 1.0 partway through, which is what
    makes an icon look like it bounces into place.

    Args:
        amount (float): Raw progress from 0.0 to 1.0.

    Returns:
        float: Eased progress. Ends at exactly 1.0 but peaks near 1.1.
    """
    # These two constants are the standard values for this curve.
    # A bigger overshoot number gives a bigger bounce.
    overshoot = 1.70158
    shifted = amount - 1.0
    return 1.0 + (overshoot + 1.0) * shifted ** 3 + overshoot * shifted ** 2

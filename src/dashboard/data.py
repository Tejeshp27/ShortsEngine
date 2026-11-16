# =============================================================
# src/dashboard/data.py — numbers for the overview and channel pages
#
# Reads the database and prepares totals, per-channel cards and the
# 30-day history charts.
#
# Important: platforms report CUMULATIVE counts (total views so far,
# total followers now). Adding up every daily snapshot would count
# the same views many times, so only the LATEST snapshot of each
# video/account is used.
# =============================================================

from datetime import datetime, timedelta, timezone

from src.analytics.collector import LATEST_METRICS_JOIN, _ensure_metrics_table
from src.dashboard.settings import list_channels
from src.storage.database import get_connection
from src.storage.finance import ledger

# Size of the history chart drawing (SVG units). The chart box is
# 560 x 140; points are kept 12 units away from the left edge and
# 10 units away from the top so lines are not cut off.
CHART_LEFT = 12
CHART_WIDTH = 536
CHART_BOTTOM = 130
CHART_HEIGHT = 110


def video_rows(channel=None):
    """
    List videos with their latest views and likes, newest first.

    Args:
        channel (str or None): Only this channel, or None for all.

    Returns:
        list[dict]: One dict per video with every videos column plus
                    "views", "likes" (None if never measured),
                    "metric_count" and "confirmed_posts".
    """
    _ensure_metrics_table()
    # LATEST_METRICS_JOIN (from collector.py) joins only the newest
    # metrics row per video and platform.
    query = f"""
        SELECT v.*,
               SUM(m.views) AS views,
               SUM(m.likes) AS likes,
               COUNT(m.id) AS metric_count,
               (SELECT COUNT(*) FROM platform_posts p WHERE p.video_id = v.id) AS confirmed_posts
        FROM videos v {LATEST_METRICS_JOIN}
    """
    parameters = []
    if channel:
        query += " WHERE v.channel = ?"
        parameters.append(channel)
    query += " GROUP BY v.id ORDER BY v.created_at DESC, v.id DESC"

    connection = get_connection()
    try:
        rows = []
        for row in connection.execute(query, parameters):
            rows.append(dict(row))
        return rows
    finally:
        connection.close()


def token_note(account):
    """
    Describe whether an account's token will keep working.

    Args:
        account (dict): An account row with "platform" and "token_expires_at".

    Returns:
        str: A short note for the channel page.
    """
    expires_at = account["token_expires_at"]
    if expires_at is None:
        return ("Token expiry unknown (token was pasted by hand). If statistics stop "
                "updating, reconnect the account.")
    if account["platform"] == "tiktok":
        return "TikTok token renews automatically."
    expiry_day = datetime.fromtimestamp(expires_at, timezone.utc).date().isoformat()
    if expires_at < datetime.now(timezone.utc).timestamp():
        return f"Token expired on {expiry_day}. Reconnect the account."
    return f"Token expires on {expiry_day}. Reconnect before then."


def account_rows(channel=None):
    """
    List connected accounts for display. Tokens are never included.

    Args:
        channel (str or None): Only this channel, or None for all.

    Returns:
        list[dict]: One dict per account with its name, latest follower
                    count, last collection time, last error and a
                    "token_note" about expiry.
    """
    query = """
        SELECT a.id, a.channel, a.platform, a.platform_account_id, a.display_name,
               a.last_synced_at, a.last_error, a.connected_at, a.token_expires_at,
               (SELECT followers FROM account_snapshots s
                WHERE s.account_id = a.id
                ORDER BY day DESC, id DESC LIMIT 1) AS followers
        FROM accounts a
    """
    values = []
    if channel:
        query += " WHERE channel = ?"
        values.append(channel)

    connection = get_connection()
    try:
        rows = []
        for row in connection.execute(query, values):
            account = dict(row)
            account["token_note"] = token_note(account)
            rows.append(account)
        return rows
    finally:
        connection.close()


def empty_totals():
    """
    Make a fresh set of counters for one channel (or for everything).

    Views and followers start as None, meaning "unknown", so a channel
    with no data shows a dash instead of a made-up 0.

    Returns:
        dict: The counters.
    """
    return {"scripts": 0, "made": 0, "posted": 0, "drafted": 0,
            "views": None, "followers": None,
            "income": 0.0, "expense": 0.0, "profit": 0.0}


def add_known(current, value):
    """
    Add a number to a total that may still be "unknown" (None).

    Args:
        current (float or None): The running total.
        value (float): The number to add.

    Returns:
        float: The new total (None counts as 0 once a value is known).
    """
    if current is None:
        return value
    return current + value


def start_channel_cards():
    """
    Make one empty card per channel YAML file.

    Returns:
        dict: Channel key → card dict with "totals" and "accounts".
    """
    cards = {}
    for channel in list_channels():
        card = dict(channel)
        card["totals"] = empty_totals()
        card["accounts"] = []
        cards[channel["key"]] = card
    return cards


def archived_card(key):
    """
    Make a card for a channel that has videos but no YAML file any more.

    Args:
        key (str): The channel key saved with its videos.

    Returns:
        dict: A card marked "Archived channel".
    """
    return {"key": key, "name": key, "niche": "Archived channel", "platforms": [],
            "error": "Config missing", "totals": empty_totals(), "accounts": []}


def count_videos(cards, videos):
    """
    Add each video to its channel's counters.

    Args:
        cards (dict): Channel key → card (changed in place).
        videos (list[dict]): From video_rows().
    """
    for video in videos:
        key = video["channel"]
        if key not in cards:
            cards[key] = archived_card(key)
        totals = cards[key]["totals"]
        totals["scripts"] += 1
        if video["video_path"]:
            totals["made"] += 1
        if video["confirmed_posts"] > 0:
            totals["posted"] += 1
        if video["status"] == "drafted":
            totals["drafted"] += 1
        if video["metric_count"]:
            totals["views"] = add_known(totals["views"], video["views"] or 0)


def count_followers(cards):
    """
    Add each connected account's latest followers to its channel.

    Args:
        cards (dict): Channel key → card (changed in place).
    """
    for account in account_rows():
        card = cards.get(account["channel"])
        if card is None:
            continue
        card["accounts"].append(account)
        if account["followers"] is not None:
            card["totals"]["followers"] = add_known(card["totals"]["followers"], account["followers"])


def count_money(cards):
    """
    Add every ledger entry to its channel's income and expense.

    Args:
        cards (dict): Channel key → card (changed in place).
    """
    for entry in ledger():
        card = cards.get(entry["channel"])
        if card is None:
            continue
        card["totals"]["income"] += entry["income"]
        card["totals"]["expense"] += entry["expense"]


def overview():
    """
    Build everything the overview page shows.

    Returns:
        dict: "totals" (all channels added together), "channel_cards"
              (one per channel) and "recent_videos" (the 8 newest).
    """
    cards = start_channel_cards()
    videos = video_rows()
    count_videos(cards, videos)
    count_followers(cards)
    count_money(cards)

    grand_total = empty_totals()
    for card in cards.values():
        totals = card["totals"]
        totals["profit"] = totals["income"] - totals["expense"]
        for key, value in totals.items():
            if value is not None:
                grand_total[key] = add_known(grand_total[key], value)

    return {"totals": grand_total, "channel_cards": list(cards.values()),
            "recent_videos": videos[:8]}


def load_history_rows(metric, channel=None):
    """
    Load every saved snapshot for a history chart, oldest first.

    Args:
        metric (str): "followers" (account snapshots) or "views" (video metrics).
        channel (str or None): Only this channel, or None for all.

    Returns:
        list: Rows with "series" (which account or video+platform the
              value belongs to), "value" and "collected_at".
    """
    _ensure_metrics_table()
    if metric == "followers":
        query = """
            SELECT s.account_id AS series, s.followers AS value, s.collected_at
            FROM account_snapshots s JOIN accounts a ON a.id = s.account_id
        """
        channel_column = "a.channel"
        order_column = "s.id"
    else:
        # One series per video per platform, e.g. "12:tiktok".
        query = """
            SELECT CAST(m.video_id AS TEXT) || ':' || m.platform AS series,
                   m.views AS value, m.collected_at
            FROM metrics m JOIN videos v ON v.id = m.video_id
        """
        channel_column = "v.channel"
        order_column = "m.id"

    values = []
    if channel:
        query += f" WHERE {channel_column} = ?"
        values.append(channel)
    query += f" ORDER BY collected_at, {order_column}"

    connection = get_connection()
    try:
        return connection.execute(query, values).fetchall()
    finally:
        connection.close()


def daily_points(rows, start, days):
    """
    Turn snapshots into one total per day.

    For each day, every series uses its latest value up to that day.
    A day with no new snapshot repeats the previous value; days before
    the first snapshot are None ("no data yet"), not 0.

    Args:
        rows (list): From load_history_rows(), oldest first.
        start (date): The first day of the chart.
        days (int): How many days the chart covers.

    Returns:
        list[dict]: One dict per day with "day", "value" and "offset".
    """
    latest_by_series = {}
    next_row = 0
    points = []
    for offset in range(days):
        day = (start + timedelta(days=offset)).isoformat()
        # Take in every snapshot collected on or before this day.
        while next_row < len(rows) and rows[next_row]["collected_at"][:10] <= day:
            row = rows[next_row]
            latest_by_series[row["series"]] = row["value"] or 0
            next_row += 1

        if latest_by_series:
            value = sum(latest_by_series.values())
        else:
            value = None
        points.append({"day": day, "value": value, "offset": offset})
    return points


def chart_coordinates(points, days, maximum):
    """
    Convert daily totals into "x,y" positions for an SVG line.

    Args:
        points (list[dict]): From daily_points().
        days (int): How many days the chart covers.
        maximum (float): The largest value (drawn at the top).

    Returns:
        str: For example "12.0,130.0 30.5,90.2 ...".
    """
    coordinates = []
    for point in points:
        if point["value"] is None:
            continue
        x = CHART_LEFT + point["offset"] / max(days - 1, 1) * CHART_WIDTH
        # SVG's y axis points down, so bigger values get a smaller y.
        y = CHART_BOTTOM - point["value"] / maximum * CHART_HEIGHT
        coordinates.append(f"{x:.1f},{y:.1f}")
    return " ".join(coordinates)


def history_chart(metric, channel=None, days=30):
    """
    Build a daily history chart of followers or views.

    Args:
        metric (str): "followers" or "views".
        channel (str or None): Only this channel, or None for all.
        days (int): How many days to show, ending today (UTC).

    Returns:
        dict: "points" (one per day), "coordinates" (for the SVG line),
              "maximum", "start", "end" and "has_data".
    """
    rows = load_history_rows(metric, channel)
    start = datetime.now(timezone.utc).date() - timedelta(days=days - 1)
    points = daily_points(rows, start, days)

    maximum = 1   # never 0, because the chart divides by it
    for point in points:
        if point["value"] is not None:
            maximum = max(maximum, point["value"])

    coordinates = chart_coordinates(points, days, maximum)
    return {"points": points, "coordinates": coordinates, "maximum": maximum,
            "start": start.isoformat(), "end": points[-1]["day"],
            "has_data": bool(coordinates)}

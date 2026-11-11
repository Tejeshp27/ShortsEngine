# =============================================================
# Read models for overview and channel pages. Cumulative platform
# counters use their latest snapshot; summing daily counters would
# incorrectly inflate views and followers.
# =============================================================

from datetime import datetime, timedelta, timezone

from src.analytics.collector import LATEST_METRICS_JOIN, _ensure_metrics_table
from src.dashboard.settings import list_channels
from src.storage.database import get_connection
from src.storage.finance import ledger


def video_rows(channel=None):
    """Return videos with latest cumulative metrics, optionally for channel."""
    _ensure_metrics_table()
    connection = get_connection()
    try:
        query = f"""
            SELECT v.*, SUM(m.views) AS views, SUM(m.likes) AS likes,
                COUNT(m.id) AS metric_count,
                (SELECT COUNT(*) FROM platform_posts p WHERE p.video_id = v.id) AS confirmed_posts
            FROM videos v {LATEST_METRICS_JOIN}
        """
        parameters = []
        if channel:
            query += " WHERE v.channel = ?"
            parameters.append(channel)
        query += " GROUP BY v.id ORDER BY v.created_at DESC, v.id DESC"
        rows = []
        for row in connection.execute(query, parameters):
            rows.append(dict(row))
        return rows
    finally:
        connection.close()


def account_rows(channel=None):
    """Return safe account display data, never tokens, optionally for channel."""
    connection = get_connection()
    try:
        query = """
            SELECT a.id, a.channel, a.platform, a.platform_account_id, a.display_name,
                   a.last_synced_at, a.last_error, a.connected_at,
                   (SELECT followers FROM account_snapshots s WHERE s.account_id = a.id
                    ORDER BY day DESC, id DESC LIMIT 1) AS followers
            FROM accounts a
        """
        values = []
        if channel:
            query += " WHERE channel = ?"
            values.append(channel)
        rows = []
        for row in connection.execute(query, values):
            rows.append(dict(row))
        return rows
    finally:
        connection.close()


def empty_totals():
    """Return an empty set of overview counters with unknown audience values."""
    return {"scripts": 0, "made": 0, "posted": 0, "drafted": 0,
            "views": None, "followers": None, "income": 0.0, "expense": 0.0, "profit": 0.0}


def overview():
    """Return totals and per-channel cards built from saved records."""
    channels = {}
    for channel in list_channels():
        channels[channel["key"]] = dict(channel, totals=empty_totals(), accounts=[])
    videos = video_rows()
    for video in videos:
        key = video["channel"]
        if key not in channels:
            channels[key] = {"key": key, "name": key, "niche": "Archived channel",
                             "platforms": [], "error": "Config missing", "totals": empty_totals(), "accounts": []}
        totals = channels[key]["totals"]
        totals["scripts"] += 1
        totals["made"] += int(bool(video["video_path"]))
        totals["posted"] += int(video["confirmed_posts"] > 0)
        totals["drafted"] += int(video["status"] == "drafted")
        if video["metric_count"]:
            totals["views"] = (totals["views"] or 0) + (video["views"] or 0)
    for account in account_rows():
        channel = channels.get(account["channel"])
        if channel:
            channel["accounts"].append(account)
            if account["followers"] is not None:
                channel["totals"]["followers"] = (channel["totals"]["followers"] or 0) + account["followers"]
    for entry in ledger():
        channel = channels.get(entry["channel"])
        if channel:
            channel["totals"]["income"] += entry["income"]
            channel["totals"]["expense"] += entry["expense"]
    total = empty_totals()
    for channel in channels.values():
        values = channel["totals"]
        values["profit"] = values["income"] - values["expense"]
        for key, value in values.items():
            if value is not None:
                total[key] = (total[key] or 0) + value
    return {"totals": total, "channel_cards": list(channels.values()), "recent_videos": videos[:8]}


def history_chart(metric, channel=None, days=30):
    """Return daily cumulative totals and SVG points for followers or views.

    channel filters the history; days controls the chart window. Missing data
    stays missing until the first observation. Earlier observations carry forward.
    """
    _ensure_metrics_table()
    connection = get_connection()
    try:
        if metric == "followers":
            query = """SELECT s.account_id AS series, s.followers AS value, s.collected_at
                       FROM account_snapshots s JOIN accounts a ON a.id = s.account_id"""
            owner = "a"
            snapshot_id = "s.id"
        else:
            query = """SELECT CAST(m.video_id AS TEXT) || ':' || m.platform AS series,
                       m.views AS value, m.collected_at FROM metrics m
                       JOIN videos v ON v.id = m.video_id"""
            owner = "v"
            snapshot_id = "m.id"
        values = []
        if channel:
            query += f" WHERE {owner}.channel = ?"
            values.append(channel)
        query += f" ORDER BY collected_at, {snapshot_id}"
        rows = connection.execute(query, values).fetchall()
    finally:
        connection.close()
    start = datetime.now(timezone.utc).date() - timedelta(days=days - 1)
    latest = {}
    index = 0
    points = []
    maximum = 1
    for offset in range(days):
        day = (start + timedelta(days=offset)).isoformat()
        while index < len(rows) and rows[index]["collected_at"][:10] <= day:
            latest[rows[index]["series"]] = rows[index]["value"] or 0
            index += 1
        value = sum(latest.values()) if latest else None
        if value is not None:
            maximum = max(maximum, value)
        points.append({"day": day, "value": value, "offset": offset})
    coordinates = []
    for point in points:
        if point["value"] is not None:
            x = 12 + point["offset"] / max(days - 1, 1) * 536
            y = 130 - point["value"] / maximum * 110
            coordinates.append(f"{x:.1f},{y:.1f}")
    return {"points": points, "coordinates": " ".join(coordinates), "maximum": maximum,
            "start": start.isoformat(), "end": points[-1]["day"], "has_data": bool(coordinates)}

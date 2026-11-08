# =============================================================
# src/storage/reports.py — cost and activity reports
#
# Reads from the database and prints summary tables to the
# terminal. Run via:
#   python -m src.main --channel news --report
#   python -m src.main --report           (all channels)
# =============================================================

from src.storage.database import get_connection


def _row(label, value, width=28):
    """Print one labelled row in a simple two-column table."""
    print(f"  {label:<{width}} {value}")


def print_cost_report(channel=None):
    """
    Print API spending broken down by day, by channel, and by model.

    Args:
        channel (str or None): If given, limit the report to that channel.
                               If None, show all channels combined.
    """
    conn = get_connection()

    print()
    print("=" * 55)
    print("Cost report" + (f" — {channel}" if channel else " — all channels"))
    print("=" * 55)

    # --- Total spend ---
    if channel:
        total = conn.execute(
            "SELECT SUM(total_cost_usd) FROM videos WHERE channel = ?",
            (channel,),
        ).fetchone()[0] or 0.0
    else:
        total = conn.execute(
            "SELECT SUM(total_cost_usd) FROM videos"
        ).fetchone()[0] or 0.0

    _row("Total AI spend:", f"${total:.4f}")
    print()

    # --- Spend by channel ---
    rows = conn.execute(
        "SELECT channel, SUM(total_cost_usd) AS cost, COUNT(*) AS videos "
        "FROM videos "
        + ("WHERE channel = ? " if channel else "")
        + "GROUP BY channel ORDER BY cost DESC",
        (channel,) if channel else (),
    ).fetchall()

    print("  By channel:")
    for r in rows:
        _row(f"    {r['channel']}", f"${r['cost']:.4f}  ({r['videos']} videos)", width=30)
    print()

    # --- Spend by day ---
    rows = conn.execute(
        "SELECT substr(created_at, 1, 10) AS day, "
        "SUM(total_cost_usd) AS cost, COUNT(*) AS videos "
        "FROM videos "
        + ("WHERE channel = ? " if channel else "")
        + "GROUP BY day ORDER BY day DESC LIMIT 14",
        (channel,) if channel else (),
    ).fetchall()

    print("  By day (last 14 days):")
    if not rows:
        print("    No data yet.")
    for r in rows:
        _row(f"    {r['day']}", f"${r['cost']:.4f}  ({r['videos']} videos)", width=30)
    print()

    # --- Spend by model (from cost_events) ---
    rows = conn.execute(
        "SELECT model, purpose, SUM(cost_usd) AS cost, "
        "SUM(input_tokens) AS input_t, SUM(output_tokens) AS output_t, "
        "COUNT(*) AS calls "
        "FROM cost_events "
        + ("WHERE channel = ? " if channel else "")
        + "GROUP BY model, purpose ORDER BY cost DESC",
        (channel,) if channel else (),
    ).fetchall()

    print("  By model / purpose (cost_events table):")
    if not rows:
        print("    No detailed cost events recorded yet.")
        print("    (cost_events are saved on new runs after this update)")
    for r in rows:
        _row(
            f"    {r['model']} [{r['purpose']}]",
            f"${r['cost']:.4f}  "
            f"({r['input_t']}↑ {r['output_t']}↓ tokens, {r['calls']} calls)",
            width=40,
        )
    print()

    # --- Video status summary ---
    rows = conn.execute(
        "SELECT status, COUNT(*) AS n FROM videos "
        + ("WHERE channel = ? " if channel else "")
        + "GROUP BY status",
        (channel,) if channel else (),
    ).fetchall()

    print("  Video status breakdown:")
    for r in rows:
        _row(f"    {r['status']}", str(r["n"]) + " video(s)", width=20)
    print()

    conn.close()

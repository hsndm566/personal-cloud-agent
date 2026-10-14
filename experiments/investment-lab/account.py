"""Explicit account snapshots; estimates never become confirmed capital."""

from datetime import timedelta

from market import timestamp
from risk import number


def validate_snapshot(data, now):
    if data.get("user_verified") is not True:
        raise ValueError("Explicit current-account confirmation required")
    observed = timestamp(data["observed_at_utc"])
    if not timedelta(0) <= now - observed <= timedelta(hours=24):
        raise ValueError("Current timestamped account snapshot required")
    result = {"user_verified": True, "observed_at_utc": observed.isoformat()}
    for key in (
        "confirmed_cash_sar",
        "estimated_cash_sar",
        "buying_power_sar",
        "fx_sar_per_usd",
        "fee_sar",
        "spread_bps",
        "slippage_bps",
    ):
        raw = data.get(key)
        result[key] = str(number(raw)) if raw not in (None, "") else None
        if result[key] is not None and number(result[key]) < 0:
            raise ValueError("Account inputs cannot be negative")
    precision = data.get("share_precision")
    if precision not in (None, ""):
        if str(precision) not in [str(i) for i in range(9)]:
            raise ValueError("Broker precision must be an integer from zero to eight")
        precision = int(precision)
    result["share_precision"] = precision or (0 if precision == 0 else None)
    return result


def account_state(events, now):
    snapshots = [e for e in events if e["kind"] == "ACCOUNT_SNAPSHOT"]
    empty = {
        "confirmed_cash_sar": None,
        "estimated_cash_sar": None,
        "buying_power_sar": None,
        "reconciled": False,
        "capital": {"fx_sar_per_usd": None},
    }
    if not snapshots:
        return empty
    snapshot = snapshots[-1]
    data = snapshot["payload"]
    current = timedelta(0) <= now - timestamp(data["observed_at_utc"]) <= timedelta(hours=24)
    # Any later confirmed fill/correction requires another explicit broker reconciliation.
    changed = any(
        e["kind"] in ("CONFIRMED", "OPENING", "REVERSAL")
        and e["created_at"] > snapshot["created_at"]
        for e in events
    )
    state = {
        **empty,
        "estimated_cash_sar": data["estimated_cash_sar"],
        "confirmed_cash_sar": data["confirmed_cash_sar"],
        "buying_power_sar": data["buying_power_sar"],
        "observed_at_utc": data["observed_at_utc"],
        "reconciled": current and not changed,
    }
    if state["reconciled"]:
        state["capital"] = {
            key: data[key]
            for key in (
                "confirmed_cash_sar",
                "buying_power_sar",
                "fx_sar_per_usd",
                "fee_sar",
                "spread_bps",
                "slippage_bps",
                "share_precision",
            )
        }
    return state


def position_risks(positions, quotes, levels):
    result = []
    for position in positions:
        ticker = position["ticker"]
        quote, threshold = quotes.get(ticker), levels.get(ticker)
        near = bool(
            quote
            and quote.get("eligible")
            and threshold is not None
            and number(position["quantity"]) > 0
            and number(quote["price"]) <= number(threshold) * number("1.01")
        )
        result.append(
            {
                **position,
                "status": "RISK REVIEW" if near else "WAIT",
                "technical_reference_usd": threshold,
                "broker_stop_order_confirmed": False,
            }
        )
    return result

"""Independent scheduled freshness check. No accounting or brokerage mutations."""

import os
from datetime import UTC, datetime

from engine import report_state
from market import ExchangeCalendar, timestamp
from notifications import Notifier, format_alert, signal_id
from store import Store


def inspect(store, now, calendar):
    report = store.latest("REPORT")
    heartbeat = store.latest("SCHEDULER_HEARTBEAT")
    session = calendar.session(now)
    try:
        heartbeat_fresh = 0 <= (now - timestamp(heartbeat["at_utc"])).total_seconds() <= 300
    except (KeyError, ValueError, TypeError):
        heartbeat_fresh = False
    report_freshness = report_state(report, now)
    status = (
        "ok"
        if heartbeat_fresh and (session != "regular" or report_freshness == "FRESH")
        else "SCANNER_UNAVAILABLE"
    )
    return {
        "status": status,
        "report_freshness": report_freshness,
        "heartbeat_fresh": heartbeat_fresh,
        "market_session": session,
        "checked_at_utc": now.isoformat(),
    }


def main():
    store = Store(os.environ["DATABASE_URL"], os.environ["INVESTMENT_OWNER"])
    now, calendar = datetime.now(UTC), ExchangeCalendar()
    result = inspect(store, now, calendar)
    store.save("INDEPENDENT_FRESHNESS_CHECK", result)
    if result["status"] != "ok":
        notifier = Notifier(
            store,
            os.getenv("INVESTMENT_TELEGRAM_BOT_TOKEN"),
            os.getenv("INVESTMENT_TELEGRAM_CHAT_ID"),
        )
        notifier.enqueue(
            signal_id("stale", "scanner", now.date().isoformat()),
            "system",
            format_alert("system", now),
        )
        notifier.flush(now)
    print("Independent freshness check: " + result["status"])
    return 0 if result["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())

"""Licensed indicative quotes, independently observed timestamps and exchange sessions."""

from __future__ import annotations

import json
import math
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Protocol
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
RIYADH = ZoneInfo("Asia/Riyadh")


def timestamp(value: str) -> datetime:
    result = datetime.fromisoformat(value)
    if result.tzinfo is None:
        raise ValueError("Timezone required")
    return result.astimezone(UTC)


class ExchangeCalendar:
    def __init__(self):
        import exchange_calendars

        self.calendar = exchange_calendars.get_calendar("XNYS")

    def session(self, now: datetime) -> str:
        day = now.astimezone(NY).date().isoformat()
        if not self.calendar.is_session(day):
            return "closed"
        opening = self.calendar.session_open(day).to_pydatetime()
        closing = self.calendar.session_close(day).to_pydatetime()
        if opening <= now < closing:
            return "regular"
        local = now.astimezone(NY)
        return "extended" if 4 <= local.hour < 20 else "closed"

    def last_completed(self, now: datetime) -> str:
        day = now.astimezone(NY).date().isoformat()
        session = self.calendar.date_to_session(day, direction="previous")
        if self.calendar.session_close(session).to_pydatetime() > now:
            session = self.calendar.previous_session(session)
        return str(session.date())

    def next_open(self, now: datetime) -> str:
        return self.calendar.next_open(now).to_pydatetime().isoformat()


@dataclass(frozen=True)
class Entitlement:
    provider: str
    exchanges: tuple[str, ...] = ()
    internal_use_verified: bool = False
    residency_verified: bool = False
    realtime_verified: bool = False
    evidence_url: str | None = None
    verified_at_utc: str | None = None
    expires_at_utc: str | None = None
    documented_delay_seconds: float | None = None

    def eligible(self, exchange: str, now: datetime) -> bool:
        try:
            return bool(
                self.provider != "twelvedata"
                and exchange in self.exchanges
                and self.internal_use_verified
                and self.residency_verified
                and self.realtime_verified
                and self.evidence_url
                and timestamp(self.verified_at_utc) <= now < timestamp(self.expires_at_utc)
            )
        except (TypeError, ValueError, AttributeError):
            return False


@dataclass(frozen=True)
class MarketQuote:
    ticker: str
    exchange: str | None
    price: float | None
    currency: str | None
    source: str
    observed_at_utc: str | None
    estimated_delay_seconds: float | None = None
    classification: str = "unknown"
    quote_source: str | None = None
    executable: bool = False

    def record(self, now: datetime, calendar: ExchangeCalendar, entitlement: Entitlement) -> dict:
        result = asdict(self)
        result.update(
            observed_at_riyadh=None,
            observed_at_market_local=None,
            age_seconds=None,
            market_session="unknown",
            eligible=False,
            ineligibility="Missing or invalid observation",
            executable=False,
        )
        try:
            observed = timestamp(self.observed_at_utc)
            age = (now - observed).total_seconds()
            session = calendar.session(observed)
            result.update(
                observed_at_riyadh=observed.astimezone(RIYADH).isoformat(),
                observed_at_market_local=observed.astimezone(NY).isoformat(),
                age_seconds=age,
                market_session=session,
            )
            checks = [
                (
                    isinstance(self.price, (int, float))
                    and math.isfinite(self.price)
                    and self.price > 0,
                    "Invalid price",
                ),
                (self.currency == "USD", "Unsupported or missing currency"),
                (0 <= age <= 1200, "Stale or future observation"),
                (
                    self.source == entitlement.provider
                    and entitlement.eligible(self.exchange, now),
                    "Entitlement unverified",
                ),
                (self.classification == "real-time", "Realtime classification unverified"),
                (
                    self.estimated_delay_seconds is not None
                    and 0 <= self.estimated_delay_seconds <= 1200,
                    "Delay unverified",
                ),
                (
                    session == "regular" and calendar.session(now) == "regular",
                    "Outside regular session",
                ),
            ]
            result["ineligibility"] = next(
                (reason for passed, reason in checks if not passed), None
            )
            result["eligible"] = result["ineligibility"] is None
        except (ValueError, TypeError, AttributeError, OverflowError):
            pass
        return result


class ProviderError(Exception):
    """Only safe error codes; never serialize URLs containing credentials."""


class QuoteProvider(Protocol):
    name: str

    def quote(self, ticker: str, exchange: str, now: datetime) -> MarketQuote: ...


class Finnhub:
    name = "finnhub"

    def __init__(self, key: str, entitlement: Entitlement, transport=None):
        self.key, self.entitlement = key, entitlement
        self.transport = transport or urllib.request.urlopen

    def quote(self, ticker: str, exchange: str, now: datetime) -> MarketQuote:
        if not self.key:
            raise ProviderError("credential_unavailable")
        # Keys use a header, not URLs or exception messages.
        request = urllib.request.Request(
            "https://finnhub.io/api/v1/quote?" + urllib.parse.urlencode({"symbol": ticker}),
            headers={"X-Finnhub-Token": self.key},
        )
        try:
            with self.transport(request, timeout=10) as response:
                data = json.load(response)
            price, epoch = data.get("c"), data.get("t")
            if isinstance(epoch, bool) or not isinstance(epoch, (int, float)) or epoch <= 0:
                raise ProviderError("invalid_timestamp")
            if (
                isinstance(price, bool)
                or not isinstance(price, (int, float))
                or not math.isfinite(price)
                or price <= 0
            ):
                raise ProviderError("empty_or_invalid_price")
            observed = datetime.fromtimestamp(epoch, UTC).isoformat()
            licensed = self.entitlement.eligible(exchange, now)
            return MarketQuote(
                ticker,
                exchange,
                price,
                "USD",
                self.name,
                observed,
                self.entitlement.documented_delay_seconds if licensed else None,
                "real-time" if licensed else "unknown",
                "Finnhub quote endpoint; consolidation and executability unverified",
            )
        except urllib.error.HTTPError as error:
            raise ProviderError(f"http_{error.code}") from None
        except ProviderError:
            raise
        except (TimeoutError, OSError):
            raise ProviderError("network_timeout_or_failure") from None
        except (ValueError, TypeError, AttributeError, OverflowError):
            raise ProviderError("invalid_response") from None


def select_quote(providers, ticker, exchange, entitlements, now, calendar):
    observations, failures = [], []
    for provider in providers:
        entitlement = entitlements.get(provider.name, Entitlement(provider.name))
        # A smoke test is explicit. Production never contacts unapproved providers.
        if not entitlement.eligible(exchange, now or datetime.now(UTC)):
            failures.append({"provider": provider.name, "code": "entitlement_unverified"})
            continue
        try:
            quote = provider.quote(ticker, exchange, now or datetime.now(UTC))
            # Receipt time measures age only; it never replaces observation time.
            observations.append(quote.record(now or datetime.now(UTC), calendar, entitlement))
        except ProviderError as error:
            failures.append({"provider": provider.name, "code": str(error)})
    eligible = [q for q in observations if q["eligible"]]
    if (
        len(eligible) > 1
        and max(q["price"] for q in eligible) / min(q["price"] for q in eligible) - 1 > 0.01
    ):
        failures.append({"provider": "comparison", "code": "price_discrepancy_over_1_percent"})
        return None, observations, failures
    return (eligible[0] if eligible else None), observations, failures

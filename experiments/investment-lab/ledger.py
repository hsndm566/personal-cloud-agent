"""Auditable manual-fill ledger with unknown basis preservation and no trading API."""

import hashlib
import json
import re
from datetime import UTC, datetime
from decimal import Decimal

from market import timestamp
from risk import number


def validate_fill(data):
    required = ("ticker", "side", "quantity", "price", "currency", "executed_at_utc", "fees")
    if any(data.get(key) in (None, "") for key in required):
        raise ValueError("All required fill fields must be supplied")
    if not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,11}", data["ticker"]):
        raise ValueError("Invalid ticker")
    if data["side"] not in ("buy", "sell") or data["currency"] != "USD":
        raise ValueError("Only USD manual long-position fills supported")
    quantity, price, fees = map(number, (data["quantity"], data["price"], data["fees"]))
    if quantity <= 0 or price <= 0 or fees < 0:
        raise ValueError("Invalid quantity, price or fees (fees use price currency)")
    executed = timestamp(data["executed_at_utc"])
    if executed > datetime.now(UTC):
        raise ValueError("Future execution timestamp")
    fx = number(data["fx_sar_per_usd"]) if data.get("fx_sar_per_usd") else None
    if fx is not None and fx <= 0:
        raise ValueError("Invalid FX")
    return {
        "ticker": data["ticker"],
        "side": data["side"],
        "quantity": str(quantity.normalize()),
        "price": str(price.normalize()),
        "fees": str(fees.normalize()),
        "currency": "USD",
        "executed_at_utc": executed.isoformat(),
        "fx_sar_per_usd": str(fx) if fx else None,
        "reference": str(data.get("reference") or "")[:200],
    }


def fingerprint(fill):
    # Reference text is not accounting identity; changing it must not duplicate a purchase.
    canonical = {k: v for k, v in fill.items() if k != "reference"}
    return hashlib.sha256(json.dumps(canonical, sort_keys=True).encode()).hexdigest()


def portfolio(events, marks=None):
    marks = marks or {}
    reversed_ids = {e["payload"]["reverses"] for e in events if e["kind"] == "REVERSAL"}
    basis_corrections = {
        e["payload"]["opening_id"]: e["payload"]["total_cost_usd"]
        for e in events
        if e["kind"] == "BASIS_CORRECTION"
    }
    active = [
        e for e in events if e["kind"] in ("CONFIRMED", "OPENING") and e["id"] not in reversed_ids
    ]
    active.sort(
        key=lambda e: (
            e["payload"].get("as_of_utc", e["payload"].get("executed_at_utc")),
            str(e.get("created_at", "")),
            e["id"],
        )
    )
    positions = {}
    for event in active:
        fill = event["payload"]
        p = positions.setdefault(
            fill["ticker"],
            {
                "quantity": Decimal(0),
                "cost": Decimal(0),
                "realized": Decimal(0),
                "unknown_realized": False,
            },
        )
        quantity = number(fill["quantity"])
        if event["kind"] == "OPENING":
            p["quantity"] += quantity
            basis = basis_corrections.get(event["id"], fill.get("total_cost_usd"))
            p["cost"] = (
                p["cost"] + number(basis) if p["cost"] is not None and basis is not None else None
            )
            continue
        price, fee = number(fill["price"]), number(fill["fees"])
        if fill["side"] == "buy":
            p["quantity"] += quantity
            if p["cost"] is not None:
                p["cost"] += quantity * price + fee
        else:
            if quantity > p["quantity"]:
                raise ValueError(
                    "Sale exceeds confirmed holdings; reconcile historical fills first"
                )
            if p["cost"] is None:
                p["unknown_realized"] = True
            else:
                sold_basis = p["cost"] * quantity / p["quantity"]
                p["realized"] += quantity * price - fee - sold_basis
                p["cost"] -= sold_basis
            p["quantity"] -= quantity
            if p["quantity"] == 0:
                p["cost"] = Decimal(0)
    result = []
    for ticker, p in positions.items():
        value = p["quantity"] * number(marks[ticker]) if ticker in marks else None
        unrealized = value - p["cost"] if value is not None and p["cost"] is not None else None
        result.append(
            {
                "ticker": ticker,
                "quantity": str(p["quantity"]),
                "cost_basis_usd": str(p["cost"]) if p["cost"] is not None else None,
                "market_value_usd": str(value) if value is not None else None,
                "unrealized_pnl_usd": str(unrealized) if unrealized is not None else None,
                "realized_pnl_usd": None if p["unknown_realized"] else str(p["realized"]),
                "basis_method": "weighted_average",
                "broker_reconciled": False,
            }
        )
    return result

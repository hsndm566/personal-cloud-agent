"""Decimal arithmetic for provisional long-only sizing; no brokerage execution."""

from decimal import ROUND_DOWN, Decimal, InvalidOperation


def number(value):
    try:
        result = Decimal(str(value))
        if not result.is_finite():
            raise ValueError("Finite value required")
        return result
    except (InvalidOperation, TypeError):
        raise ValueError("Valid number required") from None


def size_position(
    *,
    entry,
    invalidation,
    fx_sar_per_usd,
    confirmed_cash_sar=None,
    buying_power_sar=None,
    max_position_sar=100,
    max_loss_sar="4.09",
    share_precision=None,
    fee_sar=None,
    spread_bps=None,
    slippage_bps=None,
):
    out = {
        "eligible": False,
        "shares": None,
        "allocation_sar": None,
        "planned_loss_sar": None,
        "broker_preview_required": True,
        "warning": "Gaps and slippage can exceed planned losses; no stop order is placed.",
    }
    values = [
        entry,
        invalidation,
        fx_sar_per_usd,
        confirmed_cash_sar,
        buying_power_sar,
        share_precision,
        fee_sar,
        spread_bps,
        slippage_bps,
    ]
    if any(value is None for value in values):
        return {
            **out,
            "reason": "Confirmed cash, buying power, FX, precision and cost assumptions required",
        }
    try:
        e, stop, fx, cash, power, limit, loss, fee, spread, slip = map(
            number,
            [
                entry,
                invalidation,
                fx_sar_per_usd,
                confirmed_cash_sar,
                buying_power_sar,
                max_position_sar,
                max_loss_sar,
                fee_sar,
                spread_bps,
                slippage_bps,
            ],
        )
        if not (
            e > stop > 0
            and fx > 0
            and min(cash, power, limit, loss) > 0
            and min(fee, spread, slip) >= 0
            and type(share_precision) is int
            and 0 <= share_precision <= 8
        ):
            raise ValueError("Invalid sizing input")
        # Defaults are ceilings until explicitly revised in a future risk policy.
        allocation = min(cash, power, limit, Decimal(100))
        budget = min(loss, Decimal("4.09"))
        unit_cost = e * fx * (1 + (spread + slip) / 10000)
        unit_loss = (e - stop) * fx + e * fx * (spread + slip) / 10000
        quantity = min((allocation - fee) / unit_cost, (budget - fee) / unit_loss)
        quantity = quantity.quantize(Decimal(1).scaleb(-share_precision), rounding=ROUND_DOWN)
        if quantity <= 0:
            raise ValueError("Insufficient risk or cash budget after costs")
        return {
            **out,
            "eligible": True,
            "shares": str(quantity),
            "allocation_sar": str(quantity * unit_cost + fee),
            "planned_loss_sar": str(quantity * unit_loss + fee),
            "reason": "Provisional only; recalculate against actual Awaed preview",
        }
    except ValueError as error:
        return {**out, "reason": str(error)}

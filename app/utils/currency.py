"""Approximate exchange rates (USD base) and member-rate helpers.

Mirrors the table historically defined in app/api/v1/reports.py so the
dashboard aggregates compute blocker cost identically to /reports/*.
"""

RATES_TO_USD: dict[str, float] = {
    "USD": 1.0,
    "INR": 0.012,
    "EUR": 1.08,
    "GBP": 1.27,
    "AED": 0.27,
    "SGD": 0.74,
    "CAD": 0.74,
    "AUD": 0.65,
}

# Fallback used by reports.py when a currency is unknown (≈ INR).
_DEFAULT_RATE = 0.012


def to_usd_rate(currency: str | None) -> float:
    return RATES_TO_USD.get((currency or "INR").upper(), _DEFAULT_RATE)


def member_hourly_usd(hourly_rate: float | None, currency: str | None) -> float | None:
    """Return the member's hourly rate in USD, or None if no rate is set."""
    if hourly_rate is None:
        return None
    return hourly_rate * to_usd_rate(currency)


def fmt_duration(hours: float) -> str:
    """Format an open-duration in hours as 'Xd Yh' / 'Xh' / '< 1h'.

    Identical output to reports.blocked_cost._fmt_duration.
    """
    if hours < 1:
        return "< 1h"
    total_min = int(hours * 60)
    days = total_min // (24 * 60)
    rem_h = (total_min % (24 * 60)) // 60
    if days == 0:
        return f"{rem_h}h"
    if rem_h == 0:
        return f"{days}d"
    return f"{days}d {rem_h}h"

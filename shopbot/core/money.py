"""Money in integer minor units. No floating point is used anywhere in monetary calculations."""

from __future__ import annotations

import re

# ISO 4217 minor-unit exponents for currencies whose exponent is not 2. Everything else defaults to 2.
_NON_STANDARD_EXPONENTS: dict[str, int] = {
    "BHD": 3,
    "BIF": 0,
    "CLF": 4,
    "CLP": 0,
    "DJF": 0,
    "GNF": 0,
    "IQD": 3,
    "ISK": 0,
    "JOD": 3,
    "JPY": 0,
    "KMF": 0,
    "KRW": 0,
    "KWD": 3,
    "LYD": 3,
    "OMR": 3,
    "PYG": 0,
    "RWF": 0,
    "TND": 3,
    "UGX": 0,
    "UYI": 0,
    "UYW": 4,
    "VND": 0,
    "VUV": 0,
    "XAF": 0,
    "XOF": 0,
    "XPF": 0,
}

MAX_AMOUNT_MINOR = 10**12


class MoneyError(ValueError):
    pass


def currency_exponent(currency: str, override: int = -1) -> int:
    if override >= 0:
        return override
    return _NON_STANDARD_EXPONENTS.get(currency.upper(), 2)


def format_amount(minor: int, exponent: int, decimal_separator: str = ".", thousands_separator: str = ",") -> str:
    """Format an amount without any currency symbol, e.g. 123456 (exp 2) -> '1,234.56'."""
    sign = "-" if minor < 0 else ""
    value = abs(minor)
    whole, fraction = divmod(value, 10**exponent) if exponent else (value, 0)
    whole_text = f"{whole:,}".replace(",", "\x00").replace("\x00", thousands_separator)
    if exponent == 0:
        return f"{sign}{whole_text}"
    return f"{sign}{whole_text}{decimal_separator}{fraction:0{exponent}d}"


def parse_amount(text: str, exponent: int, decimal_separator: str = ".", thousands_separator: str = ",") -> int:
    """Parse an amount typed by staff (e.g. '12.50', '1,250.00', '1250') into minor units.

    Rejects ambiguous or malformed input rather than guessing.
    """
    raw = text.strip()
    if not raw:
        raise MoneyError("amount is empty")
    if raw.startswith("-"):
        raise MoneyError("amount must not be negative")
    if thousands_separator:
        groups = raw.split(decimal_separator)[0] if decimal_separator else raw
        if thousands_separator in groups:
            parts = groups.split(thousands_separator)
            if not parts[0] or len(parts[0]) > 3 or any(len(p) != 3 for p in parts[1:]):
                raise MoneyError("thousands separators are misplaced")
        raw = raw.replace(thousands_separator, "")
    if decimal_separator and decimal_separator in raw:
        whole, _, fraction = raw.partition(decimal_separator)
    else:
        whole, fraction = raw, ""
    if not re.fullmatch(r"\d+", whole or "0") or (fraction and not re.fullmatch(r"\d+", fraction)):
        raise MoneyError("amount must contain only digits and separators")
    if len(fraction) > exponent:
        raise MoneyError(f"at most {exponent} decimal places are allowed")
    minor = int(whole or "0") * 10**exponent + (int(fraction.ljust(exponent, "0")) if exponent else 0)
    if minor > MAX_AMOUNT_MINOR:
        raise MoneyError("amount is too large")
    return minor


def percent_of(amount_minor: int, basis_points: int) -> int:
    """``amount * basis_points / 10000`` rounded half up, in integers."""
    if amount_minor < 0 or basis_points < 0:
        raise MoneyError("percent_of expects non-negative values")
    return (amount_minor * basis_points + 5000) // 10000

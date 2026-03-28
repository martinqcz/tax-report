"""CNB (Czech National Bank) daily exchange rate fetcher."""

import os
import json
import requests
from datetime import date, timedelta

CACHE_DIR = os.path.join(os.path.dirname(__file__), ".cnb_cache")
CNB_URL = "https://www.cnb.cz/cs/financni-trhy/devizovy-trh/kurzy-devizoveho-trhu/kurzy-devizoveho-trhu/denni_kurz.txt"


def _ensure_cache_dir():
    os.makedirs(CACHE_DIR, exist_ok=True)


def _cache_path(d: date) -> str:
    return os.path.join(CACHE_DIR, f"{d.isoformat()}.json")


def _parse_cnb_response(text: str) -> dict[str, float]:
    """Parse CNB text format into {currency_code: rate_per_1_unit} dict."""
    rates = {}
    lines = text.strip().split("\n")
    # First line is date, second is header, rest are rates
    for line in lines[2:]:
        parts = line.split("|")
        if len(parts) < 5:
            continue
        # format: country|currency_name|amount|code|rate
        amount = int(parts[2])
        code = parts[3].strip()
        rate = float(parts[4].replace(",", "."))
        rates[code] = rate / amount
    return rates


def _fetch_rates_for_date(d: date) -> dict[str, float] | None:
    """Fetch rates from CNB for a specific date. Returns None if no data."""
    url = f"{CNB_URL}?date={d.strftime('%d.%m.%Y')}"
    resp = requests.get(url, timeout=10)
    if resp.status_code != 200:
        return None
    rates = _parse_cnb_response(resp.text)
    if not rates:
        return None
    return rates


def get_rate(currency: str, d: date) -> float:
    """Get CZK exchange rate for 1 unit of currency on given date.

    For weekends/holidays, walks back to find the last available rate.
    Returns the rate (e.g., 23.5 means 1 USD = 23.5 CZK).
    """
    if currency == "CZK":
        return 1.0

    _ensure_cache_dir()

    # Try up to 7 days back for weekends/holidays
    for offset in range(8):
        check_date = d - timedelta(days=offset)
        cache_file = _cache_path(check_date)

        # Check cache first
        if os.path.exists(cache_file):
            with open(cache_file) as f:
                rates = json.load(f)
            if currency in rates:
                return rates[currency]
            # Cache exists but currency not found - try previous day
            continue

        # Fetch from CNB
        rates = _fetch_rates_for_date(check_date)
        if rates:
            with open(cache_file, "w") as f:
                json.dump(rates, f)
            if currency in rates:
                return rates[currency]

    raise ValueError(f"Could not find CNB rate for {currency} on or before {d}")


def convert_to_czk(amount: float, currency: str, d: date) -> float:
    """Convert amount in given currency to CZK using CNB rate for the date."""
    rate = get_rate(currency, d)
    return amount * rate

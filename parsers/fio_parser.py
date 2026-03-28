"""Fio e-Broker CSV statement parser."""

import re
from dataclasses import dataclass, field
from datetime import date, datetime


@dataclass
class FioTrade:
    symbol: str
    date: date
    direction: str  # "buy" or "sell"
    price: float
    quantity: float
    currency: str
    amount: float  # net amount in trade currency
    commission: float


@dataclass
class FioDividend:
    symbol: str
    date: date
    amount: float
    currency: str
    country: str


@dataclass
class FioWithholdingTax:
    symbol: str
    date: date
    amount: float  # negative value
    currency: str
    country: str


@dataclass
class FioSplit:
    symbol: str
    date: date
    new_shares: float
    split_ratio: tuple[int, int] | None  # (new, old) e.g., (20, 1)


@dataclass
class FioStatement:
    trades: list[FioTrade] = field(default_factory=list)
    dividends: list[FioDividend] = field(default_factory=list)
    withholding_taxes: list[FioWithholdingTax] = field(default_factory=list)
    splits: list[FioSplit] = field(default_factory=list)


def _parse_fio_number(val: str) -> float:
    """Parse Czech number format like '1 652,00' -> 1652.0."""
    val = val.strip()
    if not val:
        return 0.0
    val = val.replace("\xa0", "").replace(" ", "").replace(",", ".")
    return float(val)


def _parse_fio_date(val: str) -> date:
    """Parse Fio date format like '23.12.2025 00:00' or '23.12.2025 15:33'."""
    val = val.strip()
    # Take just the date part
    date_part = val.split(" ")[0]
    return datetime.strptime(date_part, "%d.%m.%Y").date()


def _extract_country_from_fio_text(text: str) -> str:
    """Extract country from Fio text like 'AAPL - Dividenda - USA' or 'AAPL - Daň z divid. zaplacená v USA'."""
    text = text.strip().rstrip(";")
    # Try "v USA" pattern (withholding tax)
    match = re.search(r"\bv\s+([A-Z]+)\s*$", text)
    if match:
        country_name = match.group(1)
        mapping = {"USA": "US", "CZ": "CZ"}
        return mapping.get(country_name, country_name)
    # Try "- USA" pattern (dividend)
    match = re.search(r"-\s+([A-Z]+)\s*$", text)
    if match:
        country_name = match.group(1)
        mapping = {"USA": "US", "CZ": "CZ"}
        return mapping.get(country_name, country_name)
    return "Unknown"


def _try_encodings(filepath: str) -> str:
    """Try multiple encodings to read the file."""
    for enc in ("utf-8", "windows-1250", "iso-8859-2", "cp1250"):
        try:
            with open(filepath, "r", encoding=enc) as f:
                return f.read()
        except (UnicodeDecodeError, UnicodeError):
            continue
    # Fallback with error replacement
    with open(filepath, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


def parse_fio_csv(filepath: str) -> FioStatement:
    """Parse a Fio e-Broker CSV statement file."""
    stmt = FioStatement()
    content = _try_encodings(filepath)
    lines = content.strip().split("\n")

    # Find the header line (starts with "Datum obchodu;")
    header_idx = None
    for i, line in enumerate(lines):
        if "Datum obchodu" in line:
            header_idx = i
            break

    if header_idx is None:
        return stmt

    # Process data lines after header
    for line in lines[header_idx + 1:]:
        line = line.strip()
        if not line or line.startswith(";"):
            # Summary line or empty
            if line.startswith(";;Sou"):
                break
            # Check if it's a summary line with just semicolons
            if line.startswith(";;") and "Sou" not in line:
                continue
            continue

        parts = line.split(";")
        if len(parts) < 13:
            continue

        date_str = parts[0].strip()
        direction = parts[1].strip()
        symbol = parts[2].strip()
        price_str = parts[3].strip()
        quantity_str = parts[4].strip()
        currency = parts[5].strip()
        amount_czk_str = parts[6].strip()
        fee_czk_str = parts[7].strip()
        amount_usd_str = parts[8].strip()
        fee_usd_str = parts[9].strip()
        amount_eur_str = parts[10].strip()
        fee_eur_str = parts[11].strip()
        text = parts[12].strip() if len(parts) > 12 else ""

        if not date_str:
            continue

        try:
            trade_date = _parse_fio_date(date_str)
        except ValueError:
            continue

        # Determine if this is a trade, dividend, or withholding tax
        # Fio uses Czech: Nákup=Buy, Prodej=Sell, Dividenda, Daň z divid.
        is_dividend = "Dividenda" in text or "dividenda" in text
        is_tax = "divid. zaplacen" in text or "Da" in text and "divid" in text

        if is_tax and symbol:
            country = _extract_country_from_fio_text(text)
            # Determine currency and amount
            if currency == "USD" or amount_usd_str:
                amt = _parse_fio_number(amount_usd_str) if amount_usd_str else _parse_fio_number(amount_czk_str)
                cur = "USD" if amount_usd_str else "CZK"
            elif currency == "EUR" or amount_eur_str:
                amt = _parse_fio_number(amount_eur_str) if amount_eur_str else _parse_fio_number(amount_czk_str)
                cur = "EUR" if amount_eur_str else "CZK"
            else:
                amt = _parse_fio_number(amount_czk_str) if amount_czk_str else 0.0
                cur = "CZK"

            stmt.withholding_taxes.append(FioWithholdingTax(
                symbol=symbol,
                date=trade_date,
                amount=amt,  # already negative from the data
                currency=cur,
                country=country,
            ))

        elif is_dividend and symbol:
            country = _extract_country_from_fio_text(text)
            if currency == "USD" or amount_usd_str:
                amt = _parse_fio_number(amount_usd_str) if amount_usd_str else _parse_fio_number(amount_czk_str)
                cur = "USD" if amount_usd_str else "CZK"
            elif currency == "EUR" or amount_eur_str:
                amt = _parse_fio_number(amount_eur_str) if amount_eur_str else _parse_fio_number(amount_czk_str)
                cur = "EUR" if amount_eur_str else "CZK"
            else:
                amt = _parse_fio_number(amount_czk_str) if amount_czk_str else 0.0
                cur = "CZK"

            stmt.dividends.append(FioDividend(
                symbol=symbol,
                date=trade_date,
                amount=amt,
                currency=cur,
                country=country,
            ))

        elif direction in ("N\xe1kup", "Nákup", "Prodej", "N kup"):
            # Check for stock split first (recorded as Nákup with price=0 and "Split" in text)
            if "Split" in text or "split" in text:
                quantity = _parse_fio_number(quantity_str)
                # Parse split ratio from text like "Split : GOOG - 20: 1"
                ratio_match = re.search(r"(\d+)\s*:\s*(\d+)", text)
                split_ratio = None
                if ratio_match:
                    split_ratio = (int(ratio_match.group(1)), int(ratio_match.group(2)))
                stmt.splits.append(FioSplit(
                    symbol=symbol,
                    date=trade_date,
                    new_shares=quantity,
                    split_ratio=split_ratio,
                ))
                continue

            # Normalize direction detection with encoding issues
            is_buy = "kup" in direction.lower()
            is_sell = "Prodej" in direction or "prodej" in direction

            if not is_buy and not is_sell:
                continue

            price = _parse_fio_number(price_str)
            quantity = _parse_fio_number(quantity_str)

            # Get amount and fee in trade currency
            if currency == "USD":
                amount = _parse_fio_number(amount_usd_str)
                fee = _parse_fio_number(fee_usd_str)
            elif currency == "EUR":
                amount = _parse_fio_number(amount_eur_str)
                fee = _parse_fio_number(fee_eur_str)
            elif currency == "CZK":
                amount = _parse_fio_number(amount_czk_str)
                fee = _parse_fio_number(fee_czk_str)
            else:
                amount = _parse_fio_number(amount_czk_str)
                fee = _parse_fio_number(fee_czk_str)

            stmt.trades.append(FioTrade(
                symbol=symbol,
                date=trade_date,
                direction="sell" if is_sell else "buy",
                price=price,
                quantity=quantity,
                currency=currency,
                amount=amount,
                commission=fee,
            ))

    return stmt

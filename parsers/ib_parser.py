"""Interactive Brokers CSV statement parser."""

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import date, datetime


@dataclass
class Trade:
    asset_category: str  # "Stocks" or "Equity and Index Options"
    currency: str
    symbol: str
    date: date
    quantity: float  # positive = buy, negative = sell
    price: float
    proceeds: float
    commission: float
    realized_pl: float
    code: str  # O=Open, C=Close, Ep=Expired, P=Partial


@dataclass
class Dividend:
    currency: str
    date: date
    symbol: str
    description: str
    amount: float
    country: str  # derived from ISIN or description


@dataclass
class WithholdingTax:
    currency: str
    date: date
    symbol: str
    description: str
    amount: float  # negative value
    country: str


@dataclass
class Transfer:
    asset_category: str
    currency: str
    symbol: str
    date: date
    direction: str  # "In" or "Out"
    from_account: str
    quantity: float


@dataclass
class CorporateAction:
    asset_category: str
    currency: str
    symbol: str
    date: date
    description: str
    quantity: float  # shares added (positive) or removed (negative)
    action_type: str  # "split", "spinoff", "merger", "other"
    split_ratio: tuple[int, int] | None = None  # (new, old) e.g., (20, 1) for 20:1


@dataclass
class IBStatement:
    account: str = ""
    account_alias: str = ""
    base_currency: str = "USD"
    trades: list[Trade] = field(default_factory=list)
    dividends: list[Dividend] = field(default_factory=list)
    withholding_taxes: list[WithholdingTax] = field(default_factory=list)
    transfers: list[Transfer] = field(default_factory=list)
    corporate_actions: list[CorporateAction] = field(default_factory=list)

    def merge(self, other: "IBStatement") -> None:
        """Merge another statement into this one, skipping duplicate entries."""
        trade_keys = {(t.symbol, t.date, t.quantity, t.price, t.asset_category, t.code) for t in self.trades}
        for t in other.trades:
            key = (t.symbol, t.date, t.quantity, t.price, t.asset_category, t.code)
            if key not in trade_keys:
                self.trades.append(t)
                trade_keys.add(key)

        div_keys = {(d.symbol, d.date, d.amount, d.currency) for d in self.dividends}
        for d in other.dividends:
            key = (d.symbol, d.date, d.amount, d.currency)
            if key not in div_keys:
                self.dividends.append(d)
                div_keys.add(key)

        wht_keys = {(w.symbol, w.date, w.amount, w.currency) for w in self.withholding_taxes}
        for w in other.withholding_taxes:
            key = (w.symbol, w.date, w.amount, w.currency)
            if key not in wht_keys:
                self.withholding_taxes.append(w)
                wht_keys.add(key)

        xfer_keys = {(x.symbol, x.date, x.quantity, x.direction) for x in self.transfers}
        for x in other.transfers:
            key = (x.symbol, x.date, x.quantity, x.direction)
            if key not in xfer_keys:
                self.transfers.append(x)
                xfer_keys.add(key)

        ca_keys = {(c.symbol, c.date, c.quantity, c.action_type) for c in self.corporate_actions}
        for c in other.corporate_actions:
            key = (c.symbol, c.date, c.quantity, c.action_type)
            if key not in ca_keys:
                self.corporate_actions.append(c)
                ca_keys.add(key)


def _parse_date(date_str: str) -> date:
    """Parse IB date format like '2025-06-20, 13:22:48' or '2025-06-20'."""
    date_str = date_str.strip().strip('"')
    if "," in date_str:
        return datetime.strptime(date_str.split(",")[0], "%Y-%m-%d").date()
    return datetime.strptime(date_str, "%Y-%m-%d").date()


def _extract_country_from_tax_desc(desc: str) -> str:
    """Extract country code from withholding tax description like '... - US Tax'."""
    match = re.search(r"- (\w+) Tax", desc)
    if match:
        return match.group(1)
    return "Unknown"


def _extract_symbol_from_dividend_desc(desc: str) -> str:
    """Extract symbol from dividend description like 'CSCO(US17275R1023) Cash Dividend...'"""
    match = re.match(r"(\w[\w.]*)\(", desc)
    if match:
        return match.group(1)
    return ""


def _extract_country_from_isin(desc: str) -> str:
    """Extract country from ISIN in description like 'CSCO(US17275R1023)'."""
    match = re.search(r"\(([A-Z]{2})\w+\)", desc)
    if match:
        isin_country = match.group(1)
        mapping = {
            "US": "US",
            "NL": "NL",
            "DE": "DE",
            "TW": "TW",
            "JP": "JP",
            "CN": "CN",
            "IE": "IE",
            "CA": "CA",
        }
        return mapping.get(isin_country, isin_country)
    # Numeric ISIN (e.g., Ford bonus dividends) - assume US
    if re.search(r"\(\d+\)", desc):
        return "US"
    # Check for NRA Withholding Exempt (foreign stocks listed as US ADRs)
    if "NRA Withholding Exempt" in desc:
        symbol = _extract_symbol_from_dividend_desc(desc)
        adr_mapping = {"TSM": "TW", "TM": "JP"}
        return adr_mapping.get(symbol, "US")
    return "Unknown"


def _safe_float(val: str) -> float:
    """Parse float, handling commas in numbers and empty strings."""
    val = val.strip().strip('"').replace(",", "")
    if not val or val == "--":
        return 0.0
    return float(val)


def _parse_split_ratio(desc: str) -> tuple[int, int] | None:
    """Parse split ratio from description like 'Split 20 for 1'."""
    match = re.search(r"Split\s+(\d+)\s+for\s+(\d+)", desc)
    if match:
        return (int(match.group(1)), int(match.group(2)))
    return None


def _classify_corporate_action(desc: str) -> str:
    """Classify a corporate action description."""
    if "Split" in desc:
        return "split"
    if "Spinoff" in desc:
        return "spinoff"
    if "Merged" in desc or "Acquisition" in desc:
        return "merger"
    return "other"


def parse_ib_csv(filepath: str) -> IBStatement:
    """Parse an Interactive Brokers activity statement CSV file."""
    stmt = IBStatement()

    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    reader = csv.reader(io.StringIO(content))

    # Track column layout per section to handle the extra "Account" column
    # that appears in consolidated statements (2021)
    trades_has_account_col = False
    transfers_has_account_col = False
    dividends_has_account_col = False
    whtax_has_account_col = False
    corp_actions_has_account_col = False

    for row in reader:
        if len(row) < 3:
            continue

        section = row[0]
        row_type = row[1]

        # Account info
        if section == "Account Information" and row_type == "Data":
            field_name = row[2]
            field_value = row[3] if len(row) > 3 else ""
            if field_name == "Account":
                # Remove "(Custom Consolidated)" suffix
                stmt.account = field_value.split(" ")[0] if field_value else ""
            elif field_name == "Account Alias":
                stmt.account_alias = field_value
            elif field_name == "Base Currency":
                stmt.base_currency = field_value

        # Section headers - detect column layout
        elif row_type == "Header":
            if section == "Trades":
                trades_has_account_col = "Account" in row
            elif section == "Transfers":
                transfers_has_account_col = "Account" in row
            elif section == "Dividends":
                dividends_has_account_col = "Account" in row
            elif section == "Withholding Tax":
                whtax_has_account_col = "Account" in row
            elif section == "Corporate Actions":
                corp_actions_has_account_col = "Account" in row

        # Trades data
        elif section == "Trades" and row_type == "Data" and len(row) > 3:
            discriminator = row[2]
            if discriminator != "Order":
                continue

            asset_category = row[3]
            if asset_category not in ("Stocks", "Equity and Index Options"):
                continue

            currency = row[4]

            # Adjust column indices based on whether there's an Account column
            offset = 1 if trades_has_account_col else 0
            symbol = row[5 + offset]
            date_str = row[6 + offset]
            quantity = _safe_float(row[7 + offset])
            price = _safe_float(row[8 + offset])
            proceeds = _safe_float(row[10 + offset])
            commission = _safe_float(row[11 + offset])
            realized_pl = _safe_float(row[13 + offset]) if len(row) > 13 + offset else 0.0
            code = row[15 + offset].strip() if len(row) > 15 + offset else ""

            stmt.trades.append(Trade(
                asset_category=asset_category,
                currency=currency,
                symbol=symbol,
                date=_parse_date(date_str),
                quantity=quantity,
                price=price,
                proceeds=proceeds,
                commission=commission,
                realized_pl=realized_pl,
                code=code,
            ))

        # Dividends
        elif section == "Dividends" and row_type == "Data":
            currency = row[2]
            if currency in ("Total", "Total in USD", "Total Dividends in USD"):
                continue
            d_offset = 1 if dividends_has_account_col else 0
            date_str = row[3 + d_offset]
            description = row[4 + d_offset]
            amount = _safe_float(row[5 + d_offset])

            symbol = _extract_symbol_from_dividend_desc(description)
            country = _extract_country_from_isin(description)

            stmt.dividends.append(Dividend(
                currency=currency,
                date=_parse_date(date_str),
                symbol=symbol,
                description=description,
                amount=amount,
                country=country,
            ))

        # Withholding Tax
        elif section == "Withholding Tax" and row_type == "Data":
            currency = row[2]
            if currency in ("Total", "Total in USD", "Total Withholding Tax in USD"):
                continue
            w_offset = 1 if whtax_has_account_col else 0
            date_str = row[3 + w_offset]
            description = row[4 + w_offset]
            amount = _safe_float(row[5 + w_offset])

            symbol = _extract_symbol_from_dividend_desc(description)
            country = _extract_country_from_tax_desc(description)

            stmt.withholding_taxes.append(WithholdingTax(
                currency=currency,
                date=_parse_date(date_str),
                symbol=symbol,
                description=description,
                amount=amount,
                country=country,
            ))

        # Transfers
        elif section == "Transfers" and row_type == "Data":
            asset_category = row[2]
            if asset_category in ("Total", "Total in USD"):
                continue
            currency = row[3]
            t_offset = 1 if transfers_has_account_col else 0
            symbol = row[4 + t_offset]
            date_str = row[5 + t_offset]
            direction = row[7 + t_offset]  # "In" or "Out"
            xfer_account = row[9 + t_offset]
            quantity = _safe_float(row[10 + t_offset])

            stmt.transfers.append(Transfer(
                asset_category=asset_category,
                currency=currency,
                symbol=symbol,
                date=_parse_date(date_str),
                direction=direction,
                from_account=xfer_account,
                quantity=quantity,
            ))

        # Corporate Actions (splits, spinoffs, mergers)
        elif section == "Corporate Actions" and row_type == "Data":
            asset_category = row[2]
            if asset_category in ("Total", "Total in USD"):
                continue
            currency = row[3]
            ca_offset = 1 if corp_actions_has_account_col else 0
            date_str = row[4 + ca_offset]
            description = row[6 + ca_offset] if len(row) > 6 + ca_offset else ""
            quantity = _safe_float(row[7 + ca_offset]) if len(row) > 7 + ca_offset else 0.0

            # Extract symbol from description like "NVDA(US67066G1040) Split 4 for 1 (NVDA, ...)"
            symbol = _extract_symbol_from_dividend_desc(description)
            action_type = _classify_corporate_action(description)
            split_ratio = _parse_split_ratio(description) if action_type == "split" else None

            if symbol and action_type in ("split", "spinoff"):
                stmt.corporate_actions.append(CorporateAction(
                    asset_category=asset_category,
                    currency=currency,
                    symbol=symbol,
                    date=_parse_date(date_str),
                    description=description,
                    quantity=quantity,
                    action_type=action_type,
                    split_ratio=split_ratio,
                ))

    return stmt

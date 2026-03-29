"""Parser for manual transaction CSV files."""

import csv
from dataclasses import dataclass
from datetime import date, datetime


@dataclass
class ManualTransaction:
    account: str
    symbol: str
    date: date
    quantity: float
    price: float
    commission: float
    currency: str
    type: str  # "buy" (default), "sell", or "split"


def parse_manual_csv(filepath: str) -> list[ManualTransaction]:
    """Parse a manual_transactions.csv file."""
    transactions = []
    with open(filepath, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            txn_type = (row.get("type") or "").strip() or "buy"
            transactions.append(ManualTransaction(
                account=row["account"].strip(),
                symbol=row["symbol"].strip(),
                date=datetime.strptime(row["date"].strip(), "%Y-%m-%d").date(),
                quantity=float(row["quantity"]),
                price=float(row["price"]),
                commission=float(row["commission"]),
                currency=row["currency"].strip(),
                type=txn_type,
            ))
    return transactions

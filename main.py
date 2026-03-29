"""Tax report generator - main entry point."""

import argparse
import glob
import io
import os
import re
import sys

from parsers.ib_parser import parse_ib_csv
from parsers.fio_parser import parse_fio_csv
from parsers.manual_parser import parse_manual_csv
from position_tracker import PositionTracker
from report import (
    generate_stock_sales_report,
    generate_dividend_report,
    generate_options_report,
    DividendEntry,
    WithholdingTaxEntry,
)

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output")


def process_ib_statement(tracker: PositionTracker, stmt, account: str):
    """Process all events from an IB statement in chronological order."""
    events = []

    for trade in stmt.trades:
        events.append(("trade", trade.date, trade))

    for xfer in stmt.transfers:
        if xfer.direction == "Out" and xfer.quantity < 0:
            events.append(("transfer", xfer.date, xfer))

    for ca in stmt.corporate_actions:
        events.append(("corporate_action", ca.date, ca))

    events.sort(key=lambda e: e[1])

    for event_type, _, event in events:
        if event_type == "trade":
            trade = event
            if trade.asset_category == "Stocks":
                if trade.quantity > 0:
                    tracker.add_buy(account, trade.symbol, trade.quantity,
                                    trade.price, trade.currency, trade.date, trade.commission)
                elif trade.quantity < 0:
                    tracker.process_sell(account, trade.symbol, trade.quantity,
                                         trade.price, trade.currency, trade.date, trade.commission)
            elif trade.asset_category == "Equity and Index Options":
                tracker.process_option_trade(
                    account, trade.symbol, trade.quantity, trade.price,
                    trade.currency, trade.date, trade.commission, trade.code,
                    realized_pl=trade.realized_pl, proceeds=trade.proceeds)

        elif event_type == "transfer":
            xfer = event
            to_account = xfer.from_account
            if xfer.asset_category == "Stocks":
                tracker.transfer(account, to_account, xfer.symbol,
                                 abs(xfer.quantity), xfer.date)
            elif xfer.asset_category == "Equity and Index Options":
                tracker.transfer_options(account, to_account, xfer.symbol,
                                          abs(xfer.quantity), xfer.date)

        elif event_type == "corporate_action":
            ca = event
            if ca.action_type == "split":
                tracker.process_split(account, ca.symbol, ca.quantity, ca.split_ratio)


def process_fio_statement(tracker: PositionTracker, stmt, account: str):
    """Process all events from a Fio statement in chronological order."""
    events = []

    for trade in stmt.trades:
        events.append(("trade", trade.date, trade))

    for split in stmt.splits:
        events.append(("split", split.date, split))

    events.sort(key=lambda e: e[1])

    for event_type, _, event in events:
        if event_type == "trade":
            trade = event
            if trade.direction == "buy":
                tracker.add_buy(account, trade.symbol, trade.quantity,
                                trade.price, trade.currency, trade.date, trade.commission)
            elif trade.direction == "sell":
                tracker.process_sell(account, trade.symbol, trade.quantity,
                                     trade.price, trade.currency, trade.date, trade.commission)
        elif event_type == "split":
            split = event
            tracker.process_split(account, split.symbol, split.new_shares, split.split_ratio)


def collect_dividends_and_taxes(ib_stmts, fio_stmts, year: int):
    """Collect dividends and withholding taxes for the given year."""
    dividends = []
    withholding_taxes = []

    for stmt in ib_stmts:
        for d in stmt.dividends:
            if d.date.year == year:
                dividends.append(DividendEntry(
                    date=d.date, symbol=d.symbol, amount=d.amount,
                    currency=d.currency, country=d.country,
                ))
        for t in stmt.withholding_taxes:
            if t.date.year == year:
                withholding_taxes.append(WithholdingTaxEntry(
                    date=t.date, symbol=t.symbol, amount=t.amount,
                    currency=t.currency, country=t.country,
                ))

    for stmt in fio_stmts:
        for d in stmt.dividends:
            if d.date.year == year:
                dividends.append(DividendEntry(
                    date=d.date, symbol=d.symbol, amount=d.amount,
                    currency=d.currency, country=d.country,
                ))
        for t in stmt.withholding_taxes:
            if t.date.year == year:
                withholding_taxes.append(WithholdingTaxEntry(
                    date=t.date, symbol=t.symbol, amount=t.amount,
                    currency=t.currency, country=t.country,
                ))

    return dividends, withholding_taxes


def discover_ib_files(person_dir: str) -> dict[str, list[tuple[int, str]]]:
    """Discover IB CSV files grouped by account, sorted by year.

    Returns: {account_id: [(year, filepath), ...]}
    """
    accounts: dict[str, list[tuple[int, str]]] = {}
    pattern = os.path.join(person_dir, "U*_*_*.csv")
    for filepath in glob.glob(pattern):
        basename = os.path.basename(filepath)
        # Format: U1234567_2025_2025.csv
        match = re.match(r"(U\d+)_(\d{4})_(\d{4})\.csv", basename)
        if match:
            account_id = match.group(1)
            year = int(match.group(2))
            accounts.setdefault(account_id, []).append((year, filepath))

    for account_id in accounts:
        accounts[account_id].sort(key=lambda x: x[0])

    return accounts


def discover_fio_files(person_dir: str) -> list[tuple[int, str]]:
    """Discover Fio CSV files sorted by year.

    Returns: [(year, filepath), ...]
    """
    files = []
    pattern = os.path.join(person_dir, "Fio Obchody *.csv")
    for filepath in glob.glob(pattern):
        basename = os.path.basename(filepath)
        match = re.search(r"(\d{4})", basename)
        if match:
            year = int(match.group(1))
            files.append((year, filepath))

    files.sort(key=lambda x: x[0])
    return files


def process_person(person_name: str, person_dir: str, year: int):
    """Process all accounts for a person and generate reports for the given year."""
    print("\n" + "=" * 80)
    print(f"  PROCESSING: {person_name}")
    print("=" * 80)

    tracker = PositionTracker()

    # Discover all available data files
    ib_accounts = discover_ib_files(person_dir)
    fio_files = discover_fio_files(person_dir)

    # --- Step 0: Load manual transactions (pre-broker data) ---
    manual_file = os.path.join(person_dir, "manual_transactions.csv")
    manual_txns = []
    if os.path.exists(manual_file):
        print(f"  Loading manual transactions: manual_transactions.csv")
        manual_txns = parse_manual_csv(manual_file)
        for txn in manual_txns:
            if txn.type == "buy":
                tracker.add_buy(txn.account, txn.symbol, txn.quantity,
                                txn.price, txn.currency, txn.date, txn.commission)

    # --- Step 1: Load historical data (years before target) to build position lots ---

    for account_id, year_files in sorted(ib_accounts.items()):
        for file_year, filepath in year_files:
            if file_year < year:
                print(f"  Loading historical: {os.path.basename(filepath)}")
                stmt = parse_ib_csv(filepath)
                process_ib_statement(tracker, stmt, account_id)

    for file_year, filepath in fio_files:
        if file_year < year:
            print(f"  Loading historical: {os.path.basename(filepath)}")
            stmt = parse_fio_csv(filepath)
            process_fio_statement(tracker, stmt, "Fio")

    # --- Step 2: Process target year data ---
    # Merge all IB events across accounts and process chronologically
    # to ensure transfers arrive before sells in other accounts.

    target_ib_stmts = []
    all_ib_events = []
    for account_id, year_files in sorted(ib_accounts.items()):
        for file_year, filepath in year_files:
            if file_year == year:
                print(f"  Loading {year}: {os.path.basename(filepath)}")
                stmt = parse_ib_csv(filepath)
                target_ib_stmts.append(stmt)

                for trade in stmt.trades:
                    all_ib_events.append(("trade", trade.date, trade, account_id))
                for xfer in stmt.transfers:
                    if xfer.direction == "Out" and xfer.quantity < 0:
                        all_ib_events.append(("transfer", xfer.date, xfer, account_id))
                for ca in stmt.corporate_actions:
                    all_ib_events.append(("corporate_action", ca.date, ca, account_id))

    # Add manual splits and sells into the event stream
    for txn in manual_txns:
        if txn.type == "split":
            all_ib_events.append(("manual_split", txn.date, txn, txn.account))
        elif txn.type == "sell":
            all_ib_events.append(("manual_sell", txn.date, txn, txn.account))

    # Sort with transfers before other events on the same date
    event_priority = {"transfer": 0, "corporate_action": 1, "manual_split": 1, "trade": 2, "manual_sell": 2}
    all_ib_events.sort(key=lambda e: (e[1], event_priority.get(e[0], 9)))

    for event_type, _, event, account_id in all_ib_events:
        if event_type == "trade":
            trade = event
            if trade.asset_category == "Stocks":
                if trade.quantity > 0:
                    tracker.add_buy(account_id, trade.symbol, trade.quantity,
                                    trade.price, trade.currency, trade.date, trade.commission)
                elif trade.quantity < 0:
                    tracker.process_sell(account_id, trade.symbol, trade.quantity,
                                         trade.price, trade.currency, trade.date, trade.commission)
            elif trade.asset_category == "Equity and Index Options":
                tracker.process_option_trade(
                    account_id, trade.symbol, trade.quantity, trade.price,
                    trade.currency, trade.date, trade.commission, trade.code,
                    realized_pl=trade.realized_pl, proceeds=trade.proceeds)
        elif event_type == "transfer":
            xfer = event
            to_account = xfer.from_account
            if xfer.asset_category == "Stocks":
                tracker.transfer(account_id, to_account, xfer.symbol,
                                 abs(xfer.quantity), xfer.date)
            elif xfer.asset_category == "Equity and Index Options":
                tracker.transfer_options(account_id, to_account, xfer.symbol,
                                          abs(xfer.quantity), xfer.date)
        elif event_type == "corporate_action":
            ca = event
            if ca.action_type == "split":
                tracker.process_split(account_id, ca.symbol, ca.quantity, ca.split_ratio)
        elif event_type == "manual_split":
            txn = event
            tracker.process_split(txn.account, txn.symbol, txn.quantity, None)
        elif event_type == "manual_sell":
            txn = event
            tracker.process_sell(txn.account, txn.symbol, -txn.quantity,
                                 txn.price, txn.currency, txn.date, txn.commission)

    target_fio_stmts = []
    for file_year, filepath in fio_files:
        if file_year == year:
            print(f"  Loading {year}: {os.path.basename(filepath)}")
            stmt = parse_fio_csv(filepath)
            process_fio_statement(tracker, stmt, "Fio")
            target_fio_stmts.append(stmt)

    # --- Step 3: Collect dividends and taxes ---
    dividends, withholding_taxes = collect_dividends_and_taxes(
        target_ib_stmts, target_fio_stmts, year)

    # --- Step 4: Generate reports ---
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    stock_sales = tracker.get_stock_sales(year)
    generate_stock_sales_report(stock_sales, person_name, OUTPUT_DIR, year)

    generate_dividend_report(dividends, withholding_taxes, person_name, OUTPUT_DIR, year)

    option_records = tracker.get_option_records(year)
    generate_options_report(option_records, person_name, OUTPUT_DIR, year)


class TeeWriter:
    """Write to two streams simultaneously."""

    def __init__(self, *streams):
        self.streams = streams

    def write(self, data):
        for s in self.streams:
            s.write(data)

    def flush(self):
        for s in self.streams:
            s.flush()


def main():
    parser = argparse.ArgumentParser(
        description="Czech tax report generator",
        usage="%(prog)s <year>\n\n  Example: python3 main.py 2025",
    )
    parser.add_argument("year", type=int,
                        help="Calendar year to generate the report for (e.g. 2025)")
    args = parser.parse_args()
    year = args.year

    print(f"\n  TAX REPORT GENERATOR {year}")
    print("  " + "=" * 40)

    # Process each person directory found in data/
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    for entry in sorted(os.listdir(DATA_DIR)):
        person_dir = os.path.join(DATA_DIR, entry)
        if os.path.isdir(person_dir) and not entry.startswith("."):
            # Tee stdout to capture output for file while still printing to console
            original_stdout = sys.stdout
            capture = io.StringIO()
            sys.stdout = TeeWriter(original_stdout, capture)
            try:
                process_person(entry, person_dir, year)
            finally:
                sys.stdout = original_stdout

            report_path = os.path.join(OUTPUT_DIR, f"{entry}_report_{year}.txt")
            with open(report_path, "w", encoding="utf-8") as f:
                f.write(capture.getvalue())
            print(f"\n  -> Report saved: {report_path}")

    print(f"\n{'='*80}")
    print(f"  All reports generated in: {OUTPUT_DIR}")
    print(f"{'='*80}\n")


if __name__ == "__main__":
    main()

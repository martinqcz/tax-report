"""Tax report generator with CZK conversion."""

import csv
import os
from collections import defaultdict
from dataclasses import dataclass
from datetime import date

from cnb_rates import convert_to_czk, get_rate
from position_tracker import SaleRecord, OptionRecord, OpenOptionRecord


@dataclass
class DividendEntry:
    date: date
    symbol: str
    amount: float
    currency: str
    country: str
    amount_czk: float = 0.0


@dataclass
class WithholdingTaxEntry:
    date: date
    symbol: str
    amount: float
    currency: str
    country: str
    amount_czk: float = 0.0


def generate_stock_sales_report(sales: list[SaleRecord], person_name: str, output_dir: str, year: int):
    """Generate stock sales report with 3-year exemption tracking."""
    print(f"\n{'='*80}")
    print(f"  STOCK SALES REPORT - {person_name} ({year})")
    print(f"{'='*80}\n")

    if not sales:
        print(f"  No stock sales in {year}.\n")
        return

    taxable_profit_czk = 0.0
    taxable_loss_czk = 0.0
    exempt_profit_czk = 0.0
    exempt_loss_czk = 0.0
    # Per-currency sums: {currency: {taxable_profit, taxable_loss, exempt_profit, exempt_loss}}
    by_currency: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))

    rows = []
    for sale in sorted(sales, key=lambda s: (s.sell_date, s.symbol)):
        rate = get_rate(sale.sell_currency, sale.sell_date)
        pl_czk = sale.profit_loss * rate
        cur = sale.sell_currency

        if sale.tax_exempt:
            if sale.profit_loss >= 0:
                exempt_profit_czk += pl_czk
                by_currency[cur]["exempt_profit"] += sale.profit_loss
            else:
                exempt_loss_czk += pl_czk
                by_currency[cur]["exempt_loss"] += sale.profit_loss
        else:
            if sale.profit_loss >= 0:
                taxable_profit_czk += pl_czk
                by_currency[cur]["taxable_profit"] += sale.profit_loss
            else:
                taxable_loss_czk += pl_czk
                by_currency[cur]["taxable_loss"] += sale.profit_loss

        status = "EXEMPT" if sale.tax_exempt else "TAXABLE"
        total_comm = sale.sell_commission + sale.buy_commission_per_share * sale.sell_quantity
        rows.append({
            "symbol": sale.symbol,
            "qty": sale.sell_quantity,
            "buy_date": sale.buy_date.isoformat(),
            "sell_date": sale.sell_date.isoformat(),
            "buy_price": sale.buy_price,
            "sell_price": sale.sell_price,
            "currency": sale.sell_currency,
            "comm_fee": round(total_comm, 2),
            "pl_orig": round(sale.profit_loss, 2),
            "cnb_rate": round(rate, 4),
            "pl_czk": round(pl_czk, 2),
            "holding_days": sale.holding_days,
            "status": status,
        })

    # Print table
    header = f"  {'Symbol':<10} {'Qty':>6} {'Buy Date':<12} {'Sell Date':<12} {'Buy Price':>10} {'Sell Price':>10} {'Curr':>4} {'Comm/Fee':>9} {'P/L Orig':>10} {'CNB Rate':>9} {'P/L CZK':>12} {'Days':>5} {'Status':<8}"
    print(header)
    print("  " + "-" * (len(header) - 2))

    for r in rows:
        print(f"  {r['symbol']:<10} {r['qty']:>6.0f} {r['buy_date']:<12} {r['sell_date']:<12} "
              f"{r['buy_price']:>10.2f} {r['sell_price']:>10.2f} {r['currency']:>4} "
              f"{r['comm_fee']:>9.2f} {r['pl_orig']:>10.2f} {r['cnb_rate']:>9.4f} {r['pl_czk']:>12.2f} "
              f"{r['holding_days']:>5} {r['status']:<8}")

    print(f"\n  SUMMARY")
    print(f"  {'-'*70}")
    print(f"  {'':18} {'CZK':>14}   {'Original currencies':}")
    print(f"  {'-'*70}")
    orig_taxable_profit = "  ".join(f"{v['taxable_profit']:>11.2f} {c}" for c, v in sorted(by_currency.items()) if v["taxable_profit"] != 0)
    orig_taxable_loss = "  ".join(f"{v['taxable_loss']:>11.2f} {c}" for c, v in sorted(by_currency.items()) if v["taxable_loss"] != 0)
    orig_exempt_profit = "  ".join(f"{v['exempt_profit']:>11.2f} {c}" for c, v in sorted(by_currency.items()) if v["exempt_profit"] != 0)
    orig_exempt_loss = "  ".join(f"{v['exempt_loss']:>11.2f} {c}" for c, v in sorted(by_currency.items()) if v["exempt_loss"] != 0)
    print(f"  Taxable profit:    {taxable_profit_czk:>14.2f}   {orig_taxable_profit}")
    print(f"  Taxable loss:      {taxable_loss_czk:>14.2f}   {orig_taxable_loss}")
    print(f"  Net taxable P/L:   {taxable_profit_czk + taxable_loss_czk:>14.2f}")
    print(f"  Tax-exempt profit: {exempt_profit_czk:>14.2f}   {orig_exempt_profit}")
    print(f"  Tax-exempt loss:   {exempt_loss_czk:>14.2f}   {orig_exempt_loss}")
    print()

    # Write CSV
    csv_path = os.path.join(output_dir, f"{person_name}_stock_sales_{year}.csv")
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else [])
        writer.writeheader()
        writer.writerows(rows)
    print(f"  -> CSV saved: {csv_path}")


def generate_dividend_report(dividends: list[DividendEntry], withholding_taxes: list[WithholdingTaxEntry],
                             person_name: str, output_dir: str, year: int):
    """Generate dividend report grouped by country."""
    print(f"\n{'='*80}")
    print(f"  DIVIDEND REPORT - {person_name} ({year})")
    print(f"{'='*80}\n")

    # Convert to CZK
    for d in dividends:
        d.amount_czk = convert_to_czk(d.amount, d.currency, d.date)
    for t in withholding_taxes:
        t.amount_czk = convert_to_czk(t.amount, t.currency, t.date)

    # Reconcile dividend country with withholding tax country per symbol
    # (e.g., TSM dividends show as "US" from ISIN but tax is "TW")
    tax_country_by_symbol: dict[str, str] = {}
    for t in withholding_taxes:
        if t.country and t.country != "Unknown":
            tax_country_by_symbol[t.symbol] = t.country
    for d in dividends:
        if d.symbol in tax_country_by_symbol:
            d.country = tax_country_by_symbol[d.symbol]

    # Group by country (CZK)
    div_by_country: dict[str, float] = defaultdict(float)
    tax_by_country: dict[str, float] = defaultdict(float)
    # Group by country and currency (original)
    div_by_country_cur: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    tax_by_country_cur: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))

    for d in dividends:
        div_by_country[d.country] += d.amount_czk
        div_by_country_cur[d.country][d.currency] += d.amount
    for t in withholding_taxes:
        tax_by_country[t.country] += t.amount_czk
        tax_by_country_cur[t.country][t.currency] += t.amount

    all_countries = sorted(set(list(div_by_country.keys()) + list(tax_by_country.keys())))

    print(f"  {'Country':<12} {'Dividends (CZK)':>18} {'Tax Paid (CZK)':>18} {'Net (CZK)':>18}   {'Dividends (orig)':}   {'Tax Paid (orig)':}")
    print(f"  {'-'*110}")

    total_div = 0.0
    total_tax = 0.0
    total_div_by_cur: dict[str, float] = defaultdict(float)
    total_tax_by_cur: dict[str, float] = defaultdict(float)
    for country in all_countries:
        div_czk = div_by_country.get(country, 0.0)
        tax_czk = tax_by_country.get(country, 0.0)
        total_div += div_czk
        total_tax += tax_czk

        div_orig = "  ".join(f"{amt:.2f} {cur}" for cur, amt in sorted(div_by_country_cur[country].items()) if amt != 0)
        tax_orig = "  ".join(f"{amt:.2f} {cur}" for cur, amt in sorted(tax_by_country_cur[country].items()) if amt != 0)
        for cur, amt in div_by_country_cur[country].items():
            total_div_by_cur[cur] += amt
        for cur, amt in tax_by_country_cur[country].items():
            total_tax_by_cur[cur] += amt

        print(f"  {country:<12} {div_czk:>18.2f} {tax_czk:>18.2f} {div_czk + tax_czk:>18.2f}   {div_orig:>16}  {tax_orig:>16}")

    print(f"  {'-'*110}")
    print(f"  {'TOTAL':<12} {total_div:>18.2f} {total_tax:>18.2f} {total_div + total_tax:>18.2f}")
    # Print original currency totals, one line per currency
    orig_prefix = " " * 74
    all_orig_currencies = sorted(set(list(total_div_by_cur.keys()) + list(total_tax_by_cur.keys())))
    for cur in all_orig_currencies:
        div_amt = total_div_by_cur.get(cur, 0.0)
        tax_amt = total_tax_by_cur.get(cur, 0.0)
        div_str = f"{div_amt:>12.2f} {cur}" if div_amt != 0 else " " * 15
        tax_str = f"{tax_amt:>12.2f} {cur}" if tax_amt != 0 else ""
        print(f"{orig_prefix}{div_str}  {tax_str}")
    print()

    # Detailed dividend list CSV
    csv_path = os.path.join(output_dir, f"{person_name}_dividends_{year}.csv")
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["date", "symbol", "amount", "currency", "country", "amount_czk"])
        for d in sorted(dividends, key=lambda x: x.date):
            writer.writerow([d.date.isoformat(), d.symbol, round(d.amount, 2),
                           d.currency, d.country, round(d.amount_czk, 2)])
    print(f"  -> CSV saved: {csv_path}")

    # Withholding tax detail CSV
    csv_path = os.path.join(output_dir, f"{person_name}_withholding_tax_{year}.csv")
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["date", "symbol", "amount", "currency", "country", "amount_czk"])
        for t in sorted(withholding_taxes, key=lambda x: x.date):
            writer.writerow([t.date.isoformat(), t.symbol, round(t.amount, 2),
                           t.currency, t.country, round(t.amount_czk, 2)])
    print(f"  -> CSV saved: {csv_path}")


def generate_options_report(records: list[OptionRecord], person_name: str, output_dir: str, year: int,
                            open_positions: list[OpenOptionRecord] | None = None):
    """Generate stock options profit/loss report including open short positions."""
    print(f"\n{'='*80}")
    print(f"  STOCK OPTIONS REPORT - {person_name} ({year})")
    print(f"{'='*80}\n")

    if not records and not open_positions:
        print(f"  No option positions in {year}.\n")
        return

    rows = []
    total_pl_czk = 0.0
    total_profit_czk = 0.0
    total_loss_czk = 0.0
    by_currency: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))

    # Closed positions
    for rec in sorted(records, key=lambda r: (r.close_date, r.symbol)):
        rate = get_rate(rec.currency, rec.close_date)
        pl_czk = rec.realized_pl * rate
        cur = rec.currency

        total_pl_czk += pl_czk
        if rec.realized_pl >= 0:
            total_profit_czk += pl_czk
            by_currency[cur]["profit"] += rec.realized_pl
        else:
            total_loss_czk += pl_czk
            by_currency[cur]["loss"] += rec.realized_pl
        by_currency[cur]["net"] += rec.realized_pl

        rows.append({
            "symbol": rec.symbol,
            "open_date": rec.open_date.isoformat(),
            "close_date": rec.close_date.isoformat(),
            "currency": rec.currency,
            "proceeds": round(rec.proceeds, 2),
            "commission": round(rec.commission, 2),
            "pl_orig": round(rec.realized_pl, 2),
            "cnb_rate": round(rate, 4),
            "pl_czk": round(pl_czk, 2),
            "account": rec.account,
            "status": "CLOSED",
        })

    header = f"  {'Symbol':<28} {'Open Date':<12} {'Close Date':<12} {'Curr':>4} {'Proceeds':>10} {'Comm':>8} {'P/L Orig':>10} {'CNB Rate':>9} {'P/L CZK':>12}"
    print(header)
    print("  " + "-" * (len(header) - 2))

    for r in rows:
        print(f"  {r['symbol']:<28} {r['open_date']:<12} {r['close_date']:<12} "
              f"{r['currency']:>4} {r['proceeds']:>10.2f} {r['commission']:>8.2f} "
              f"{r['pl_orig']:>10.2f} {r['cnb_rate']:>9.4f} {r['pl_czk']:>12.2f}")

    # Open short positions
    open_rows = []
    if open_positions:
        open_short = [p for p in open_positions if p.quantity < 0]

        if open_short:
            print(f"\n  OPEN SHORT POSITIONS (year end)")
            print("  " + "-" * (len(header) - 2))

        for rec in sorted(open_short, key=lambda r: (r.open_date, r.symbol)):
            rate = get_rate(rec.currency, rec.open_date)
            pl = rec.proceeds - rec.commission
            pl_czk = pl * rate
            cur = rec.currency
            label = "SHORT"

            total_pl_czk += pl_czk
            if pl >= 0:
                total_profit_czk += pl_czk
                by_currency[cur]["profit"] += pl
            else:
                total_loss_czk += pl_czk
                by_currency[cur]["loss"] += pl
            by_currency[cur]["net"] += pl

            open_rows.append({
                "symbol": rec.symbol,
                "open_date": rec.open_date.isoformat(),
                "close_date": "",
                "currency": rec.currency,
                "proceeds": round(rec.proceeds, 2),
                "commission": round(rec.commission, 2),
                "pl_orig": round(pl, 2),
                "cnb_rate": round(rate, 4),
                "pl_czk": round(pl_czk, 2),
                "account": rec.account,
                "status": f"OPEN {label}",
            })

            print(f"  {rec.symbol:<28} {rec.open_date.isoformat():<12} {'OPEN':>12} "
                  f"{rec.currency:>4} {rec.proceeds:>10.2f} {rec.commission:>8.2f} "
                  f"{pl:>10.2f} {rate:>9.4f} {pl_czk:>12.2f}")

    print(f"\n  SUMMARY")
    print(f"  {'-'*70}")
    print(f"  {'':18} {'CZK':>14}   {'Original currencies':}")
    print(f"  {'-'*70}")
    orig_profit = "  ".join(f"{v['profit']:11.2f} {c}" for c, v in sorted(by_currency.items()) if v["profit"] != 0)
    orig_loss = "  ".join(f"{v['loss']:11.2f} {c}" for c, v in sorted(by_currency.items()) if v["loss"] != 0)
    orig_net = "  ".join(f"{v['net']:11.2f} {c}" for c, v in sorted(by_currency.items()) if v["net"] != 0)
    print(f"  Option profit:     {total_profit_czk:>14.2f}   {orig_profit}")
    print(f"  Option loss:       {total_loss_czk:>14.2f}   {orig_loss}")
    print(f"  Net option P/L:    {total_pl_czk:>14.2f}   {orig_net}")
    print()

    # Write CSV
    all_rows = rows + open_rows
    csv_path = os.path.join(output_dir, f"{person_name}_options_{year}.csv")
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(all_rows[0].keys()) if all_rows else [])
        writer.writeheader()
        writer.writerows(all_rows)
    print(f"  -> CSV saved: {csv_path}")

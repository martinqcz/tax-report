"""Position tracker with FIFO lot matching and transfer handling."""

from dataclasses import dataclass, field
from datetime import date
from collections import defaultdict


@dataclass
class Lot:
    """A single purchase lot of shares."""
    symbol: str
    quantity: float
    price: float  # per share
    currency: str
    date: date
    account: str  # original account where purchased
    commission_per_share: float = 0.0


@dataclass
class SaleRecord:
    """Record of a matched sale against a purchase lot."""
    symbol: str
    sell_date: date
    sell_price: float
    sell_quantity: float
    sell_currency: str
    sell_commission: float
    buy_date: date
    buy_price: float
    buy_currency: str
    buy_commission_per_share: float
    holding_days: int
    tax_exempt: bool  # held > 3 years
    profit_loss: float  # in sell currency
    account: str


@dataclass
class OptionRecord:
    """Record of a closed option position."""
    symbol: str
    open_date: date
    close_date: date
    currency: str
    proceeds: float  # total proceeds from all legs
    commission: float  # total commission
    realized_pl: float
    account: str


class PositionTracker:
    """Tracks positions across multiple accounts using FIFO."""

    def __init__(self):
        # {(account, symbol): [Lot, ...]} - FIFO order
        self.lots: dict[tuple[str, str], list[Lot]] = defaultdict(list)
        self.sales: list[SaleRecord] = []
        self.option_records: list[OptionRecord] = []
        # Track option positions: {(account, symbol): [{"date": date, "quantity": int, "price": float, "commission": float}]}
        self.option_lots: dict[tuple[str, str], list[dict]] = defaultdict(list)

    def add_buy(self, account: str, symbol: str, quantity: float, price: float,
                currency: str, trade_date: date, commission: float = 0.0):
        """Add a purchase lot."""
        comm_per_share = abs(commission) / quantity if quantity != 0 else 0
        self.lots[(account, symbol)].append(Lot(
            symbol=symbol,
            quantity=abs(quantity),
            price=price,
            currency=currency,
            date=trade_date,
            account=account,
            commission_per_share=comm_per_share,
        ))

    def process_split(self, account: str, symbol: str, new_shares: float,
                      split_ratio: tuple[int, int] | None = None):
        """Process a stock split. New shares are distributed proportionally across existing lots.

        For a 20:1 split with 3 shares (pre-split), you get 57 new shares (60 total).
        Each lot's quantity is multiplied by the ratio, and price divided by the ratio.
        """
        key = (account, symbol)
        lots = self.lots.get(key, [])

        if not lots:
            return

        if split_ratio:
            new_for_old = split_ratio[0]
            old_for_new = split_ratio[1]
            ratio = new_for_old / old_for_new
        else:
            # Infer ratio from new shares and existing quantity
            total_existing = sum(lot.quantity for lot in lots)
            if total_existing <= 0:
                return
            ratio = (total_existing + new_shares) / total_existing

        for lot in lots:
            lot.price = lot.price / ratio
            lot.commission_per_share = lot.commission_per_share / ratio
            lot.quantity = lot.quantity * ratio

    def process_sell(self, account: str, symbol: str, sell_quantity: float,
                     sell_price: float, currency: str, sell_date: date,
                     commission: float = 0.0):
        """Process a sale using FIFO matching against available lots."""
        remaining = abs(sell_quantity)
        key = (account, symbol)
        lots = self.lots.get(key, [])

        sell_comm_per_share = abs(commission) / remaining if remaining != 0 else 0

        while remaining > 0.001 and lots:
            lot = lots[0]
            matched = min(remaining, lot.quantity)

            holding_days = (sell_date - lot.date).days
            tax_exempt = holding_days > 3 * 365  # > 3 years

            # P/L = (sell_price - buy_price) * quantity - commissions
            profit_loss = (sell_price - lot.price) * matched - (sell_comm_per_share + lot.commission_per_share) * matched

            self.sales.append(SaleRecord(
                symbol=symbol,
                sell_date=sell_date,
                sell_price=sell_price,
                sell_quantity=matched,
                sell_currency=currency,
                sell_commission=sell_comm_per_share * matched,
                buy_date=lot.date,
                buy_price=lot.price,
                buy_currency=lot.currency,
                buy_commission_per_share=lot.commission_per_share,
                holding_days=holding_days,
                tax_exempt=tax_exempt,
                profit_loss=profit_loss,
                account=account,
            ))

            lot.quantity -= matched
            remaining -= matched

            if lot.quantity < 0.001:
                lots.pop(0)

        if remaining > 0.001:
            print(f"  ⚠ MISSING BUY: {symbol} {remaining:.0f} shares sold {sell_date} "
                  f"@ {sell_price} {currency} in {account} — no matching purchase found")
            self.sales.append(SaleRecord(
                symbol=symbol,
                sell_date=sell_date,
                sell_price=sell_price,
                sell_quantity=remaining,
                sell_currency=currency,
                sell_commission=sell_comm_per_share * remaining,
                buy_date=date(2020, 1, 1),  # assumed pre-2021
                buy_price=0.0,
                buy_currency=currency,
                buy_commission_per_share=0.0,
                holding_days=(sell_date - date(2020, 1, 1)).days,
                tax_exempt=True,  # assumed > 3 years
                profit_loss=sell_price * remaining - sell_comm_per_share * remaining,
                account=account,
            ))

    def transfer(self, from_account: str, to_account: str, symbol: str,
                 quantity: float, transfer_date: date):
        """Transfer shares from one account to another, preserving lot history."""
        from_key = (from_account, symbol)
        to_key = (to_account, symbol)
        remaining = abs(quantity)

        from_lots = self.lots.get(from_key, [])

        while remaining > 0.001 and from_lots:
            lot = from_lots[0]
            transfer_qty = min(remaining, lot.quantity)

            # Create a new lot in the destination with preserved buy date and price
            self.lots[to_key].append(Lot(
                symbol=symbol,
                quantity=transfer_qty,
                price=lot.price,
                currency=lot.currency,
                date=lot.date,  # preserve original purchase date
                account=lot.account,  # preserve original account
                commission_per_share=lot.commission_per_share,
            ))

            lot.quantity -= transfer_qty
            remaining -= transfer_qty

            if lot.quantity < 0.001:
                from_lots.pop(0)

        if remaining > 0.001:
            # Remaining quantity not found in lots - create synthetic lot
            self.lots[to_key].append(Lot(
                symbol=symbol,
                quantity=remaining,
                price=0.0,
                currency="USD",
                date=date(2020, 1, 1),
                account=from_account,
            ))

    def process_option_trade(self, account: str, symbol: str, quantity: float,
                             price: float, currency: str, trade_date: date,
                             commission: float, code: str, realized_pl: float = 0.0,
                             proceeds: float = 0.0):
        """Process an option trade using IB's realized P/L directly."""
        key = (account, symbol)

        if "O" in code and "C" not in code:
            # Opening trade - track for date matching
            self.option_lots[key].append({
                "date": trade_date,
                "quantity": quantity,
                "commission": abs(commission),
                "proceeds": proceeds,
            })
        elif "C" in code:
            # Closing trade - use IB's realized P/L
            lots = self.option_lots.get(key, [])
            earliest_open_date = trade_date
            total_open_commission = 0.0
            total_open_proceeds = 0.0
            remaining = abs(quantity)

            while remaining > 0.001 and lots:
                lot = lots[0]
                matched = min(remaining, abs(lot["quantity"]))
                frac = matched / abs(lot["quantity"]) if lot["quantity"] != 0 else 1.0

                if lot["date"] < earliest_open_date:
                    earliest_open_date = lot["date"]
                total_open_commission += lot["commission"] * frac
                total_open_proceeds += lot["proceeds"] * frac

                if matched >= abs(lot["quantity"]) - 0.001:
                    lots.pop(0)
                else:
                    lot["quantity"] = (abs(lot["quantity"]) - matched) * (1 if lot["quantity"] > 0 else -1)
                    lot["commission"] *= (1 - frac)
                    lot["proceeds"] *= (1 - frac)

                remaining -= matched

            total_commission = abs(commission) + total_open_commission
            total_proceeds = proceeds + total_open_proceeds

            self.option_records.append(OptionRecord(
                symbol=symbol,
                open_date=earliest_open_date,
                close_date=trade_date,
                currency=currency,
                proceeds=total_proceeds,
                commission=total_commission,
                realized_pl=realized_pl,
                account=account,
            ))

    def transfer_options(self, from_account: str, to_account: str, symbol: str,
                         quantity: float, transfer_date: date):
        """Transfer option positions between accounts."""
        from_key = (from_account, symbol)
        to_key = (to_account, symbol)
        remaining = abs(quantity)

        from_lots = self.option_lots.get(from_key, [])

        transferred = []
        while remaining > 0.001 and from_lots:
            lot = from_lots[0]
            transfer_qty = min(remaining, abs(lot["quantity"]))
            frac = transfer_qty / abs(lot["quantity"]) if lot["quantity"] != 0 else 1

            transferred.append({
                "date": lot["date"],
                "quantity": int(transfer_qty) * (1 if lot["quantity"] > 0 else -1),
                "commission": lot["commission"] * frac,
                "proceeds": lot["proceeds"] * frac,
            })

            if transfer_qty >= abs(lot["quantity"]) - 0.001:
                from_lots.pop(0)
            else:
                lot["quantity"] = (abs(lot["quantity"]) - transfer_qty) * (1 if lot["quantity"] > 0 else -1)
                lot["proceeds"] *= (1 - frac)
                lot["commission"] *= (1 - frac)

            remaining -= transfer_qty

        for t in transferred:
            self.option_lots[to_key].append(t)

    def get_stock_sales(self, year: int) -> list[SaleRecord]:
        """Get all stock sales that occurred in the given year."""
        return [s for s in self.sales if s.sell_date.year == year]

    def get_option_records(self, year: int) -> list[OptionRecord]:
        """Get all closed option positions in the given year."""
        return [r for r in self.option_records if r.close_date.year == year]

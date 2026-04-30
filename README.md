# Czech Tax Report Generator

A Python tool that generates Czech tax reports from Interactive Brokers (including Lynx) and Fio e-Broker statements. Designed for Czech tax residents who trade stocks and stock options on foreign markets.

## What it does

The tool processes broker CSV statements and produces three reports per person:

**1. Stock Sales Report** - Lists all sold stock positions with purchase date, sale date, total sum paid (including commissions), total sum received, and profit/loss. Amounts are shown in original currency and converted to CZK. Applies the Czech **3-year holding exemption** (positions held longer than 3 years are tax-exempt). Uses FIFO lot matching.

**2. Dividend Report** - Summarizes dividends received and withholding tax already paid, grouped by country of origin. Useful for filling out the Czech tax return and claiming foreign tax credits.

**3. Stock Options Report** - Lists all closed option positions with total sum paid (including commissions), total sum received, and realized profit/loss in original currency and CZK.

All amounts are converted to CZK using the Czech National Bank (CNB) daily exchange rate for the transaction date.

## Features

- Parses **Interactive Brokers** activity statements (CSV export), including consolidated statements
- Parses **Fio e-Broker** trade statements (Czech CSV format)
- Handles **stock splits** - split shares inherit the original acquisition date
- Handles **position transfers** between accounts - preserves original acquisition date and cost basis
- **FIFO** (First In, First Out) lot matching for partial sales
- **3-year holding exemption** - automatically flags tax-exempt positions
- **CNB daily exchange rates** - fetched from the CNB API, cached locally
- Supports multiple currencies (USD, EUR, HKD, CZK, ...)
- Auto-discovers data files per person - just organize by folder
- Outputs formatted console report + CSV files for each section

## Prerequisites

- Python 3.10+
- `requests` library

```bash
pip install -r requirements.txt
```

## Data Setup

Organize your broker statements under the `data/` directory, one subdirectory per person:

```
data/
  PersonA/
    U1234567_2021_2021.csv    # IB activity statement for 2021
    U1234567_2022_2022.csv    # IB activity statement for 2022
    U1234567_2023_2023.csv
    U1234567_2024_2024.csv
    U1234567_2025_2025.csv
    U9876543_2025_2025.csv    # Another IB account (e.g. after migration)
    Fio Obchody 2022.csv      # Fio statement for 2022
    Fio Obchody 2023.csv
    Fio Obchody 2024.csv
    Fio Obchody 2025.csv
  PersonB/
    U5555555_2023_2023.csv
    U5555555_2024_2024.csv
    U5555555_2025_2025.csv
```

### Interactive Brokers statements

Export from IB Account Management:
1. Go to **Reports** > **Statements**
2. Select **Activity Statement**
3. Period: **Annual** for the desired year (January 1 - December 31)
4. Format: **CSV**

The filename must follow the pattern `U<account>_<year>_<year>.csv`.

**Important:** Load historical years (prior to the report year) so the tool can build accurate position cost basis and acquisition dates for the 3-year rule.

### Fio e-Broker statements

Export from Fio e-Broker:
1. Go to trade history
2. Select the date range for the desired year
3. Export as CSV

The filename must follow the pattern `Fio Obchody <year>.csv`.

## Usage

```bash
python3 main.py <year>
```

Example:
```bash
python3 main.py 2025
```

The tool will:
1. Auto-discover all person directories under `data/`
2. For each person, load all historical statements (years before the target) to build position lots
3. Process the target year's statements
4. Generate reports to console and save to `output/`

## Output

Reports are saved to the `output/` directory:

```
output/
  PersonA_report_2025.txt           # Full console output
  PersonA_stock_sales_2025.csv      # Stock sales detail
  PersonA_dividends_2025.csv        # Dividend detail
  PersonA_withholding_tax_2025.csv  # Withholding tax detail
  PersonA_options_2025.csv          # Options detail
```

## How It Works

### Position Tracking

The tool processes all historical statements chronologically to build a complete picture of position lots. Each lot tracks:
- Symbol, quantity, purchase price, purchase date, currency
- Commission per share

When shares are **sold**, FIFO matching is used to pair the sale with the oldest available lots. The holding period is calculated from the lot's original purchase date.

When shares are **transferred** between accounts, the original acquisition date and cost basis are preserved.

When a **stock split** occurs (detected from IB Corporate Actions or Fio split entries), existing lots are adjusted: quantity is multiplied and price is divided by the split ratio, preserving the original acquisition date.

If a position cannot be traced to a purchase in the available data, it is assumed to have been acquired before the earliest available data (and is marked as tax-exempt).

### Currency Conversion

All amounts are converted to CZK using the CNB (Czech National Bank) daily exchange rate for the transaction date. Rates are fetched from the CNB API and cached locally in `.cnb_cache/` to avoid repeated HTTP requests. For weekends and holidays, the last available business day rate is used.

### Country Classification

Dividend and withholding tax entries are classified by country:
- **IB statements**: Country is derived from the ISIN in the dividend description, and reconciled with the withholding tax country (e.g., TSM is a US-listed ADR but withholding tax is paid to Taiwan)
- **Fio statements**: Country is parsed from the transaction text (e.g., "Dividenda - USA")

## Limitations

- Only processes **stocks** and **equity options**. Does not handle futures, bonds, forex trading profits, or other instrument types.
- **Spinoffs** (e.g., IBM -> KD, T -> WBD) are detected but not fully tracked for cost basis allocation. Spun-off shares without traceable purchase history are assumed to be long-term holdings.
- The tool does not generate the actual Czech tax forms - it produces the data summaries needed to fill them in.

# TODO

- [ ] Support custom date range IB statements spanning two calendar years (e.g. `U20001633_20251201_20260130.csv`). Currently the year is extracted from the start date only, so the file would be assigned to 2025. Trades from January 2026 in that file would be incorrectly processed as 2025 data. The fix should split or filter trades by actual date relative to the target year.

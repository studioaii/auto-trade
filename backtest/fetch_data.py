"""Fetch & cache 3 months of NIFTY data from Zerodha for the futures backtest.

Writes three CSVs into backtest/data/:
  - nifty_spot_5min.csv   : NIFTY 50 index, 5-min, full ~3 months (Mar 2 – May 29 2026)
  - nifty_fut_5min.csv     : NIFTY front-month FUT, 5-min (Apr 1 – May 29 2026; intraday
                              futures only goes back this far because monthly contracts
                              expire and Kite's continuous feed is daily-only)
  - nifty_fut_daily.csv    : NIFTY continuous (stitched) FUT, daily, full ~3 months

Run once: `python backtest/fetch_data.py`. Idempotent.
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import sys
from pathlib import Path

from kiteconnect import KiteConnect

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import config  # noqa: E402
DATA = Path(__file__).resolve().parent / "data"
DATA.mkdir(parents=True, exist_ok=True)

SPOT_TOKEN = 256265           # NSE:NIFTY 50 index
FUT_FRONT_TOKEN = 15956226    # NIFTY26JUNFUT (front month; carries 5-min back to Apr 1)

FROM = dt.date(2026, 3, 1)
TO = dt.date(2026, 5, 29)


def _kite() -> KiteConnect:
    sess = json.load(open(REPO / ".kite_session.json"))
    k = KiteConnect(api_key=config.API_KEY)
    k.set_access_token(sess["access_token"])
    return k


def _write(rows: list[dict], path: Path) -> None:
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["datetime", "open", "high", "low", "close", "volume"])
        for r in rows:
            w.writerow([
                r["date"].strftime("%Y-%m-%d %H:%M:%S"),
                r["open"], r["high"], r["low"], r["close"], r["volume"],
            ])
    print(f"  wrote {len(rows):>5} rows -> {path.relative_to(REPO)}")


def main() -> None:
    k = _kite()
    print("Fetching NIFTY spot 5-min ...")
    _write(k.historical_data(SPOT_TOKEN, FROM, TO, "5minute"), DATA / "nifty_spot_5min.csv")

    print("Fetching NIFTY futures 5-min (front month) ...")
    _write(k.historical_data(FUT_FRONT_TOKEN, FROM, TO, "5minute"), DATA / "nifty_fut_5min.csv")

    print("Fetching NIFTY futures daily (continuous/stitched) ...")
    _write(k.historical_data(FUT_FRONT_TOKEN, FROM, TO, "day", continuous=True),
           DATA / "nifty_fut_daily.csv")

    print("Done.")


if __name__ == "__main__":
    main()

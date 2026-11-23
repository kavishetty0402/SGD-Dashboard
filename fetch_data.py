"""
fetch_data.py
Downloads daily closing prices from Yahoo Finance for everything the
dashboard shows, and saves them in data/markets.csv.

It re-downloads the last 3 years every time and overwrites the file.
That is simple and self-healing: if one day fails, the next run fills
the gap.

Run:  python3 fetch_data.py
"""
import os
import pandas as pd
import yfinance as yf

OUTPUT_FILE = "data/markets.csv"

# [our name, Yahoo ticker]
# Currencies: Yahoo's "XXX=X" means units of XXX per 1 USD.
TICKERS = [
    ["USD/SGD", "SGD=X"],
    ["USD/CNY", "CNY=X"],
    ["USD/MYR", "MYR=X"],
    ["USD/JPY", "JPY=X"],
    ["USD/EUR", "EUR=X"],
    ["USD/GBP", "GBP=X"],
    ["USD/AUD", "AUD=X"],
    ["USD/KRW", "KRW=X"],
    ["USD/TWD", "TWD=X"],
    ["USD/THB", "THB=X"],
    ["USD/IDR", "IDR=X"],
    ["USD/INR", "INR=X"],
    ["USD/PHP", "PHP=X"],
    ["USD/HKD", "HKD=X"],
    ["Dollar index", "DX-Y.NYB"],
    # Equities
    ["STI (Singapore)", "^STI"],
    ["Hang Seng", "^HSI"],
    ["Shanghai Composite", "000001.SS"],
    ["Nikkei 225", "^N225"],
    ["KOSPI", "^KS11"],
    ["Taiwan", "^TWII"],
    ["Malaysia KLCI", "^KLSE"],
    ["Indonesia JCI", "^JKSE"],
    ["S&P 500", "^GSPC"],
    # Rates (US Treasury yields, in %)
    ["US 3-month yield", "^IRX"],
    ["US 5-year yield", "^FVX"],
    ["US 10-year yield", "^TNX"],
    # Commodities
    ["Brent oil", "BZ=F"],
]


def main():
    yahoo_codes = []
    for name, code in TICKERS:
        yahoo_codes.append(code)

    print("Downloading", len(yahoo_codes), "series from Yahoo Finance...")
    raw = yf.download(yahoo_codes, period="3y", interval="1d",
                      auto_adjust=False, progress=False)
    closes = raw["Close"]

    # rename Yahoo codes to our names, one by one
    table = pd.DataFrame(index=closes.index)
    for name, code in TICKERS:
        if code in closes.columns:
            table[name] = closes[code]
            count = closes[code].notna().sum()
            print("  %-20s %5d days" % (name, count))
        else:
            print("  %-20s MISSING" % name)

    table.index = pd.to_datetime(table.index).date
    table.index.name = "date"
    table = table.dropna(how="all")

    os.makedirs("data", exist_ok=True)
    table.to_csv(OUTPUT_FILE)
    print("Saved", OUTPUT_FILE, "with", len(table), "days, last date", table.index[-1])


main()

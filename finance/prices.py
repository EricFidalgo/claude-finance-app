"""Daily prices for the holdings in data/manual.json and the brokerage's tickers: funds and ETFs from Yahoo
Finance, TSP funds from tsp.gov.

Neither needs a key. Yahoo gets a symbol and a date range; tsp.gov's download is the same for everyone.
Share counts, balances and account names stay on this machine.
"""
import csv
import functools
import io
import json
import logging
import time
import urllib.request
from datetime import date, datetime, timedelta, timezone

from . import db

log = logging.getLogger("finance")
YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?period1={start}&period2={end}&interval=1d"
TSP = "https://www.tsp.gov/data/fund-price-history.csv?Lfunds=1&InvFunds=1&download=1"  # the whole history, ~600 KB
# Each source turns the other's user agent away: Yahoo rate-limits a full browser string, tsp.gov refuses a short one.
YAHOO_AGENT = "Mozilla/5.0"
TSP_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"


def http_get(url, user_agent):
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": user_agent}), timeout=30) as r:
        return r.read()


def _tsp(fund, since, get):
    column = f"{fund} Fund" if len(fund) == 1 else fund  # TSP:C -> "C Fund", TSP:L 2050 -> "L 2050"
    rows = csv.DictReader(io.StringIO(get(TSP, TSP_AGENT).decode()))
    if column not in (rows.fieldnames or ()):
        raise ValueError(f"tsp.gov lists no {column!r} column")
    return sorted((r["Date"], float(r[column])) for r in rows if r["Date"] >= since and r[column])


def _yahoo(symbol, since, get):
    start = int(datetime.fromisoformat(since).replace(tzinfo=timezone.utc).timestamp())
    url = YAHOO.format(symbol=urllib.request.quote(symbol), start=start, end=int(time.time()))
    chart = json.loads(get(url, YAHOO_AGENT))["chart"]
    if not chart.get("result"):
        raise ValueError(f"Yahoo has no prices for {symbol}: {(chart.get('error') or {}).get('description')}")
    r = chart["result"][0]
    offset = timedelta(seconds=r["meta"].get("gmtoffset") or 0)  # bars are stamped at the exchange's open
    out = []
    for ts, close in zip(r.get("timestamp") or [], r["indicators"]["quote"][0]["close"]):
        day = (datetime.fromtimestamp(ts, timezone.utc) + offset).date().isoformat()
        if close is not None and day >= since:  # today's bar has no close until the fund posts its NAV
            out.append((day, round(close, 4)))  # Yahoo sends float32 noise: 17.879999 for 17.88
    return out


def fetch(symbol, since, get=http_get):
    """Daily prices [(ISO date, price)] on or after `since`, oldest first."""
    if symbol.startswith("TSP:"):
        return _tsp(symbol[4:], since, get)
    return _yahoo(symbol, since, get)


# How far back a refresh asks for, before a balance's as_of date (weekends and holidays have no price).
LOOKBACK_DAYS = 31
# A year of history for the Investing chart; a year of daily closes is a few KB per symbol.
HISTORY_DAYS = 366


def refresh(conn, doc, today, get=http_get, tickers=()):
    """Fetch and store a year of prices for every holding in a manual doc (see `manual`) and for `tickers`.
    One symbol failing leaves the others, and every price already stored, in place.

    Returns {symbol: {"date", "price"}} for the latest price fetched, or {symbol: {"error"}}.
    """
    year_ago = today - timedelta(days=HISTORY_DAYS)
    since = {t: year_ago for t in tickers}
    for a in doc.get("accounts") or []:
        for h in a["holdings"]:
            start = date.fromisoformat(h["as_of"]) - timedelta(days=LOOKBACK_DAYS) if h.get("as_of") else today
            since[h["symbol"]] = min(since.get(h["symbol"], year_ago), start)
    get = functools.cache(get)  # several TSP funds share one download
    out = {}
    for symbol, start in sorted(since.items()):
        try:
            rows = fetch(symbol, start.isoformat(), get)
            if not rows:
                raise ValueError("no prices in the last year")
        except Exception as e:  # network, HTTP status, a changed format: keep going with the rest
            log.warning("price %s: %s", symbol, e)
            out[symbol] = {"error": str(e)}
            continue
        with db.transaction(conn):
            conn.executemany("INSERT OR REPLACE INTO prices (symbol, date, price) VALUES (?, ?, ?)",
                             [(symbol, d, p) for d, p in rows])
        out[symbol] = {"date": rows[-1][0], "price": rows[-1][1]}
    return out

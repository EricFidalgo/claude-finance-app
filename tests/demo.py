"""Run the app on made-up data, for working on the UI without real transactions on screen.

    .venv/bin/python -m tests.demo [port]      # http://127.0.0.1:8790 by default

The data dir is a fresh temp folder; the fake bank goes through the real sync code. Nothing here touches Plaid.
"""
import contextlib
import os
import random
import sys
import tempfile
from datetime import date, timedelta

os.environ["FINANCE_DATA_DIR"] = tempfile.mkdtemp(prefix="finance-demo-")
os.environ["FINANCE_SCHEDULER"] = "off"

import uvicorn  # noqa: E402

from finance import db, manual, plan, prices, sync  # noqa: E402
from finance.plaid import save_item  # noqa: E402
from finance.web import create_app  # noqa: E402
from tests.fakes import BOA, MERRILL, FakeBank, FakeFeed, FakePlaid, acct, holding, page, security, txn  # noqa: E402

TODAY = date.today()


def _txns():
    rnd = random.Random(7)
    out, n = [], 0

    def add(account, amount, d, name, category, detailed=None, pending=False):
        nonlocal n
        n += 1
        out.append(txn(f"d{n}", account, round(amount, 2), d.isoformat(), name, name, category, detailed, pending))

    start = TODAY - timedelta(days=120)
    d = start
    while d <= TODAY:
        if d.day in (15,) or d == _month_end(d):
            add("chk", -2450.00, d, "Acme Corp Payroll", "INCOME", "INCOME_WAGES")
        if d.day == 1:
            add("chk", 1650.00, d, "Parkview Apartments", "RENT_AND_UTILITIES", "RENT_AND_UTILITIES_RENT")
        if d.day == 8:
            add("card", 15.49, d, "Netflix", "ENTERTAINMENT", "ENTERTAINMENT_TV_AND_MOVIES")
        if d.day == 12:
            add("card", 11.99, d, "Spotify", "ENTERTAINMENT", "ENTERTAINMENT_MUSIC_AND_AUDIO")
        if d.day == 18:
            add("chk", 64.00 + rnd.uniform(-6, 9), d, "City Power", "RENT_AND_UTILITIES", "RENT_AND_UTILITIES_GAS_AND_ELECTRICITY")
        if d.day == 21:
            add("card", 70.00, d, "Mobile Co", "RENT_AND_UTILITIES", "RENT_AND_UTILITIES_TELEPHONE")
        if d.day == 25:
            add("chk", 300.00, d, "Bank of America Card Payment", "LOAN_PAYMENTS", "LOAN_PAYMENTS_CREDIT_CARD_PAYMENT")
            add("chk", 500.00, d, "Transfer to Merrill", "TRANSFER_OUT", "TRANSFER_OUT_ACCOUNT_TRANSFER")
        if d.weekday() in (5, 2):
            add("card", rnd.uniform(45, 110), d, rnd.choice(["Aldi", "Trader Joe's"]), "FOOD_AND_DRINK",
                "FOOD_AND_DRINK_GROCERIES", pending=(TODAY - d).days < 2)
        if rnd.random() < 0.45:
            add("card", rnd.uniform(6, 38), d, rnd.choice(["Corner Cafe", "Chipotle", "Sushi Bar", "Pizza Place"]),
                "FOOD_AND_DRINK", "FOOD_AND_DRINK_RESTAURANT", pending=(TODAY - d).days < 2)
        if d.weekday() == 0 and rnd.random() < 0.6:
            add("card", rnd.uniform(35, 55), d, "Shell", "TRANSPORTATION", "TRANSPORTATION_GAS")
        if rnd.random() < 0.12:
            add("card", rnd.uniform(15, 140), d, rnd.choice(["Amazon", "Target", "Best Buy"]), "GENERAL_MERCHANDISE")
        if rnd.random() < 0.05:
            add("card", rnd.uniform(20, 60), d, "CVS Pharmacy", "MEDICAL", "MEDICAL_PHARMACIES_AND_SUPPLEMENTS")
        d += timedelta(days=1)
    add("card", 189.00, TODAY - timedelta(days=3), "Electronics Outlet", "GENERAL_MERCHANDISE")
    add("card", 23.40, TODAY, "Corner Cafe", "FOOD_AND_DRINK", "FOOD_AND_DRINK_RESTAURANT", pending=True)  # fills Today's gauge
    return out


def _month_end(d):
    return (d.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)


def bank():
    boa = FakePlaid(item_id="item-boa", institution_id=BOA, accounts=[
        acct("chk", "0001", current=2384.17, available=2310.55, name="Everyday Checking"),
        acct("card", "0002", "credit", "credit card", current=642.18, available=4357.82, limit=5000.00,
             name="Customized Cash Rewards"),
        acct("cma-b", "0003", "investment", "cash management", current=18450.00, name="CMA-Edge"),
    ], pages={"": page(_txns())}, consent_expiration_time=(TODAY + timedelta(days=200)).isoformat() + "T00:00:00Z")
    merrill = FakePlaid(item_id="item-ml", institution_id=MERRILL, products=("investments",), accounts=[
        acct("cma-m", "0003", "investment", "cash management", current=18450.00, name="CMA-Edge"),
    ], securities=[security("s-mm", "TTTXX", "mutual fund", "Treasury Money Market", cash_equivalent=True),
                   security("s-vti", "VTI", "etf", "Vanguard Total Stock Market ETF", price=290.0),
                   security("s-bnd", "BND", "etf", "Vanguard Total Bond Market ETF", price=72.5)],
        holdings=[holding("cma-m", "s-mm", 5200, 5200.00), holding("cma-m", "s-vti", 38, 11020.00, 9800.00),
                  holding("cma-m", "s-bnd", 30.76, 2230.00, 2200.00)])
    save_item({"item_id": "item-boa", "institution": "Bank of America", "env": "sandbox", "access_token": "demo-boa"})
    save_item({"item_id": "item-ml", "institution": "Merrill", "env": "sandbox", "access_token": "demo-ml"})
    return FakeBank(**{"demo-boa": boa, "demo-ml": merrill})


def demo_plan():
    nxt = TODAY.replace(day=15) if TODAY.day < 15 else (TODAY.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    return {"version": 1,
            "income": {"amount": 2450, "schedule": "semimonthly", "next_payday": nxt.isoformat(), "days": [15, 31]},
            "bills": [{"name": "Parkview Apartments", "amount": 1650, "due_day": 1, "account": "0001",
                       "category": "RENT_AND_UTILITIES", "autopay": True},
                      {"name": "City Power", "amount": 70, "due_day": 18, "fixed": False}],
            "monthly_spend": {"FOOD_AND_DRINK": 700, "GENERAL_MERCHANDISE": 250},
            "buffer": 200,
            "goals": [{"name": "Emergency fund", "target": 15000, "by": f"{TODAY.year + 1}-06-30"}]}


def demo_manual():
    return {"version": 1, "accounts": [
        {"name": "Vanguard IRA", "holdings": [{"symbol": "VFIAX", "name": "Vanguard 500 Index Admiral", "shares": 41.2}]},
        {"name": "TSP", "holdings": [{"symbol": "TSP:C", "name": "TSP C Fund", "balance": 8200,
                                      "as_of": (TODAY - timedelta(days=3)).isoformat(), "per_paycheck": 250}]}]}


def _walk(start, drift, wobble, seed):
    """A year of made-up weekday closes ending yesterday: a random walk with a slight drift."""
    rnd, price, out = random.Random(seed), start, []
    for n in range(prices.HISTORY_DAYS, 0, -1):
        d = TODAY - timedelta(days=n)
        if d.weekday() < 5:
            price *= 1 + drift + rnd.gauss(0, wobble)
            out.append((d.isoformat(), round(price, 2)))
    return out


def price_feed():
    return FakeFeed(closes={"VFIAX": _walk(470.0, 0.0006, 0.009, 1), "VTI": _walk(255.0, 0.0006, 0.01, 2),
                            "BND": _walk(71.0, 0.0001, 0.003, 3)},
                    tsp={"C Fund": _walk(105.0, 0.0006, 0.009, 4)})


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8790
    fake = bank()
    feed = price_feed()
    plan.save(demo_plan())
    manual.save(demo_manual())
    with contextlib.closing(db.connect()) as conn:
        print([r["status"] for r in sync.sync_all(conn, lambda env: fake)], flush=True)
        prices.refresh(conn, manual.load(), TODAY, feed, tickers=["BND", "VTI"])
    app = create_app(make_client=lambda env: fake, get_url=feed)
    print(f"Demo (made-up data) at http://127.0.0.1:{port}", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")

import json
from datetime import datetime, timezone

import pytest
from starlette.testclient import TestClient

from finance.plaid import save_item
from tests.fakes import BOA, MERRILL, FakeBank, FakePlaid, acct, holding, page, security, txn

NOW = datetime(2026, 10, 29, 16, 0, tzinfo=timezone.utc)  # a Thursday
BOA_TOKEN, MERRILL_TOKEN = "access-production-boa-SECRET1", "access-production-merrill-SECRET2"


def bank():
    boa = FakePlaid(item_id="item-boa", institution_id=BOA, accounts=[
        acct("chk", "1111", current=1500.00, available=1400.00),
        acct("card", "3333", "credit", "credit card", current=300.00, available=1650.00, limit=2000.00),
        acct("cma-b", "2222", "investment", "cash management", current=10000.00, name="CMA-Edge"),
    ], pages={"": page([
        txn("t1", "chk", 12.50, "2026-10-20", "Corner Cafe", "Corner Cafe", "FOOD_AND_DRINK"),
        txn("t2", "card", 60.25, "2026-10-22", "Aldi", "Aldi", "FOOD_AND_DRINK", "FOOD_AND_DRINK_GROCERIES"),
        txn("t3", "card", 30.00, "2026-10-28", "Shell", "Shell", "TRANSPORTATION"),
        txn("t4", "chk", -2400.00, "2026-10-23", "Employer", None, "INCOME"),
        txn("t5", "chk", 500.00, "2026-10-24", "To Merrill", None, "TRANSFER_OUT", "TRANSFER_OUT_ACCOUNT_TRANSFER"),
    ])})
    merrill = FakePlaid(item_id="item-ml", institution_id=MERRILL, products=("investments",), accounts=[
        acct("cma-m", "2222", "investment", "cash management", current=10000.00, name="CMA-Edge"),
    ], securities=[security("s-mm", "TTTXX", "mutual fund", "Treasury Money Market", cash_equivalent=True),
                   security("s-vti", "VTI", "etf", price=250.0)],
        holdings=[holding("cma-m", "s-mm", 4000, 4000.00), holding("cma-m", "s-vti", 24, 6000.00)])
    save_item({"item_id": "item-boa", "institution": "Bank of America", "env": "production", "access_token": BOA_TOKEN})
    save_item({"item_id": "item-ml", "institution": "Merrill", "env": "production", "access_token": MERRILL_TOKEN})
    return FakeBank(**{BOA_TOKEN: boa, MERRILL_TOKEN: merrill})


@pytest.fixture
def app(data_dir):
    from finance.web import create_app

    fake = bank()
    with TestClient(create_app(clock=lambda: NOW, make_client=lambda env: fake)) as c:
        assert [r["status"] for r in c.post("/api/sync", json={"force": True}).json()["results"]] == ["ok", "ok"]
        yield c


def test_health_reports_ok(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_data_survives_app_restart(data_dir):
    from finance.web import create_app

    plan = {"version": 1, "income": None, "bills": [], "monthly_spend": {}, "buffer": 150, "goals": []}
    with TestClient(create_app()) as c:
        assert c.put("/api/plan", json=plan).status_code == 200
    with TestClient(create_app()) as c:
        assert c.get("/api/plan").json()["buffer"] == 150


def test_food_last_week_from_the_spending_endpoint(app):
    s = app.get("/api/spending?period=last_week").json()
    assert (s["start"], s["end"]) == ("2026-10-19", "2026-10-25")
    food = next(c for c in s["by_category"] if c["category"] == "FOOD_AND_DRINK")
    assert food["total_cents"] == 1250 + 6025
    assert s["total_cents"] == 1250 + 6025  # income and the transfer to Merrill are not spending


def test_accounts_count_cma_edge_once_in_net_worth(app):
    m = app.get("/api/accounts").json()
    assert sorted(a["mask"] for a in m["accounts"]) == ["1111", "2222", "3333"]
    # 1500 checking + 10000 CMA-Edge - 350 card (incl. pending) = 11150
    assert m["net_worth"]["net_cents"] == 1115000


def test_holdings_show_allocation_and_idle_cash(app):
    inv = app.get("/api/holdings").json()
    assert inv["allocation"]["idle_cash_cents"] == 400000
    assert inv["allocation"]["invested_cents"] == 600000
    assert inv["left_to_invest"]["idle_cash_cents"] == 400000


def test_today_asks_for_a_plan_until_one_is_saved_then_shows_safe_to_spend(app):
    assert app.get("/api/today").json()["safe_to_spend"]["status"] == "needs_plan"
    plan = app.get("/api/plan").json()
    plan.update(income={"name": "Pay", "amount": 2400, "schedule": "biweekly", "next_payday": "2026-11-06"},
                bills=[{"name": "Phone", "amount": 50, "due_day": 1}], buffer=100)
    assert app.put("/api/plan", json=plan).status_code == 200

    s = app.get("/api/today").json()["safe_to_spend"]
    assert s["status"] == "ok"
    assert s["per_day_cents"] == (140000 - 5000 - 35000 - 10000) // 8


def test_invalid_plan_is_rejected_with_reasons_and_not_saved(app):
    r = app.put("/api/plan", json={"version": 1, "bills": [{"name": "", "amount": -5, "due_day": 40}]})
    assert r.status_code == 400
    assert any("due_day" in e for e in r.json()["errors"])
    assert app.get("/api/plan").json()["bills"] == []


def test_manual_sync_is_debounced_unless_forced(app):
    assert app.post("/api/sync", json={}).json()["status"] == "skipped"
    assert app.post("/api/sync", json={"force": True}).json()["status"] == "synced"


def test_snapshot_is_compact_text_with_this_screens_data_and_a_question_line(app):
    r = app.get("/api/snapshot?screen=spending")
    assert r.headers["content-type"].startswith("text/plain")
    text = r.text
    for heading in ("Accounts", "Spending this month", "Upcoming bills", "Holdings", "Plan", "This screen"):
        assert heading in text
    assert "Aldi" in text and text.rstrip().endswith("My question:")
    assert len(text) < 8000


def test_no_endpoint_ever_returns_a_token_or_key(app, monkeypatch):
    secrets = [BOA_TOKEN, MERRILL_TOKEN, "access-"]
    monkeypatch.setenv("PLAID_SECRET", "prod-secret-value-xyz")
    secrets.append("prod-secret-value-xyz")
    paths = ["/health", "/api/today", "/api/spending", "/api/spending?period=week", "/api/bills", "/api/accounts",
             "/api/holdings", "/api/transactions", "/api/alerts", "/api/status", "/api/plan", "/api/snapshot",
             "/api/snapshot?screen=today", "/"]
    bodies = [app.get(p).text for p in paths] + [app.post("/api/sync", json={"force": True}).text]
    for p, body in zip(paths + ["POST /api/sync"], bodies):
        for s in secrets:
            assert s not in body, f"{p} leaked a secret"


def test_transactions_endpoint_filters_by_date_and_search(app):
    rows = app.get("/api/transactions?start=2026-10-21&q=aldi").json()["transactions"]
    assert [r["transaction_id"] for r in rows] == ["t2"]

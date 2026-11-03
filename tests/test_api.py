import json
from datetime import datetime, timedelta, timezone

import pytest
from starlette.testclient import TestClient

from finance.plaid import save_item
from tests.fakes import BOA, MERRILL, FakeBank, FakeFeed, FakePlaid, acct, holding, page, security, txn

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
    feed = FakeFeed({"FSPSX": [("2026-10-27", 17.50), ("2026-10-28", 18.00)],
                     "VTI": [("2026-10-27", 240.00), ("2026-10-28", 250.00)]},
                    {"C Fund": [("2026-10-27", 120.0), ("2026-10-28", 125.0)]})
    with TestClient(create_app(clock=lambda: NOW, make_client=lambda env: fake, get_url=feed)) as c:
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


def with_pay(app, **income):
    plan = app.get("/api/plan").json()
    plan["income"] = {"name": "Pay", "amount": 2400, "schedule": "biweekly", "next_payday": "2026-11-06", **income}
    assert app.put("/api/plan", json=plan).status_code == 200


def test_spending_since_payday_needs_the_pay_schedule_then_starts_at_the_last_payday(app):
    r = app.get("/api/spending?period=pay_period")
    assert r.status_code == 400 and "pay schedule" in r.json()["error"]
    with_pay(app)
    s = app.get("/api/spending?period=pay_period").json()
    assert (s["start"], s["end"], s["payday"]) == ("2026-10-23", "2026-10-29", "2026-11-06")
    assert s["total_cents"] == 3000  # Shell; the paycheck and the transfer to Merrill are not spending


def test_today_shows_spent_today_this_week_and_since_payday_and_the_latest_transactions(app):
    t = app.get("/api/today").json()
    assert [(s["period"], s["total_cents"]) for s in t["spent"]] == [
        ("today", 0), ("week", 3000), ("month", 1250 + 6025 + 3000)]  # no pay schedule yet: this month instead
    assert [(r["transaction_id"], r["spending"]) for r in t["recent"]][:2] == [("t3", True), ("t5", False)]
    with_pay(app)
    since_payday = app.get("/api/today").json()["spent"][2]
    assert (since_payday["period"], since_payday["start"], since_payday["total_cents"]) == ("pay_period", "2026-10-23", 3000)


def test_transactions_for_a_period_with_what_they_add_up_to(app):
    with_pay(app)
    r = app.get("/api/transactions?period=pay_period").json()
    assert (r["start"], r["end"]) == ("2026-10-23", "2026-10-29")
    assert [t["transaction_id"] for t in r["transactions"]] == ["t3", "t5", "t4"]
    assert r["spent_cents"] == 3000
    # the $500 to Merrill and the paycheck are listed but not spending, so the rows say which ones add up to $30
    assert [t["spending"] for t in r["transactions"]] == [True, False, False]
    # the header's "Updated …" on this screen too
    assert r["last_synced_at"] and r["last_synced_at"] == app.get("/api/status").json()["last_synced_at"]
    assert app.get("/api/transactions?period=month&q=aldi").json()["spent_cents"] == 6025
    assert app.get("/api/transactions?period=fortnight").status_code == 400


def test_a_top_merchant_opens_exactly_its_own_charges_so_the_totals_match(app):
    boa = app.app.state.make_client("production").fakes[BOA_TOKEN]
    boa.pages["c1"] = page(added=[txn("t6", "card", 8.00, "2026-10-29", "Seashell Cafe", "Seashell Cafe", "FOOD_AND_DRINK")],
                           next_cursor="c2")
    app.post("/api/sync", json={"force": True})
    shell = next(m for m in app.get("/api/spending?period=month").json()["by_merchant"] if m["merchant"] == "Shell")
    r = app.get("/api/transactions?period=month&merchant=Shell").json()
    assert [t["transaction_id"] for t in r["transactions"]] == ["t3"]  # not Seashell Cafe, which a search would match
    assert r["spent_cents"] == shell["total_cents"] == 3000


def test_transactions_say_when_payday_is_so_the_screen_offers_the_pay_period_only_with_a_schedule(app):
    assert app.get("/api/transactions?period=week").json()["payday"] is None
    with_pay(app)
    assert app.get("/api/transactions?period=week").json()["payday"] == "2026-11-06"


def test_the_other_category_lists_the_charges_the_bank_left_uncategorized(app):
    boa = app.app.state.make_client("production").fakes[BOA_TOKEN]
    boa.pages["c1"] = page(added=[{**txn("t6", "card", 7.00, "2026-10-29", "SQ *KIOSK"), "personal_finance_category": None}],
                           next_cursor="c2")
    app.post("/api/sync", json={"force": True})
    other = next(c for c in app.get("/api/spending?period=week").json()["by_category"] if c["label"] == "Other")
    r = app.get(f"/api/transactions?period=week&category={other['category']}").json()
    assert [t["transaction_id"] for t in r["transactions"]] == ["t6"]


def test_spending_over_chosen_dates(app):
    s = app.get("/api/spending?start=2026-10-20&end=2026-10-22").json()
    assert (s["period"], s["total_cents"]) == ("custom", 1250 + 6025)
    for bad in ("start=2026-10-22&end=2026-10-20", "start=10/20/2026&end=2026-10-22", "start=2026-10-20"):
        assert app.get(f"/api/spending?{bad}").status_code == 400


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



def test_sync_fetches_prices_for_brokerage_tickers_and_charts_todays_holdings_at_past_prices(app):
    inv = app.get("/api/holdings").json()
    # 24 VTI at $240 then $250, plus $4,000 of money market held flat; today ends at the holdings' own $10,000
    assert inv["history"]["points"] == [{"date": "2026-10-27", "total_cents": 976000},
                                        {"date": "2026-10-28", "total_cents": 1000000},
                                        {"date": "2026-10-29", "total_cents": 1000000}]
    assert inv["history"]["unpriced"] == []


MANUAL = {"version": 1, "accounts": [
    {"name": "Fidelity", "holdings": [{"symbol": "FSPSX", "shares": 100}]},
    {"name": "TSP", "holdings": [{"symbol": "TSP:C", "name": "TSP C Fund", "balance": 1250, "as_of": "2026-10-28"}]}]}


def manual_values(app):
    return {h["security_id"]: h["value_cents"] for h in app.get("/api/holdings").json()["holdings"]
            if h["security_id"].startswith("manual:")}


def test_manual_investments_are_priced_on_save_and_count_in_holdings_and_net_worth(app):
    r = app.put("/api/manual", json=MANUAL)
    assert r.status_code == 200
    assert r.json()["prices"] == {"FSPSX": {"date": "2026-10-28", "price": 18.0},
                                  "TSP:C": {"date": "2026-10-28", "price": 125.0}}
    assert app.get("/api/manual").json() == MANUAL

    assert manual_values(app) == {"manual:FSPSX": 180000, "manual:TSP:C": 125000}  # 100 × $18; $1250 at $125
    assert app.get("/api/holdings").json()["allocation"]["invested_cents"] == 600000 + 180000 + 125000
    accounts = app.get("/api/accounts").json()
    assert accounts["net_worth"]["net_cents"] == 1115000 + 180000 + 125000
    assert [a["name"] for a in accounts["accounts"] if a["institution"] == "Manual"] == ["Fidelity", "TSP"]


def test_invalid_manual_investments_are_rejected_with_every_problem_and_not_saved(app):
    bad = {"version": 1, "accounts": [{"name": "", "holdings": [
        {"symbol": "FSPSX"}, {"symbol": "TSP:C", "balance": 100}, {"symbol": "VTSAX", "shares": -1},
        {"symbol": "TSP:Z", "balance": 5, "as_of": "2026-10-28"}]}]}
    r = app.put("/api/manual", json=bad)
    assert r.status_code == 400
    text = " | ".join(r.json()["errors"])
    for fragment in ("accounts[0].name", "holdings[0]", "holdings[1].as_of", "holdings[2].shares", "holdings[3].symbol"):
        assert fragment in text
    assert app.get("/api/manual").json() == {"version": 1, "accounts": []}


def test_manual_investments_reject_typos_and_numbers_that_cannot_be_valued(app):
    # Raw text, as a hand edit would have it: 1e400 parses as infinity.
    bad = """{"version": 1, "accounts": [{"name": "IRA", "kind": "roth", "holdings": [
        {"symbol": "FSPSX", "shares": 1e400},
        {"symbol": "TSP:C", "balance": 100, "as_of": "2026-10-28", "per_paychek": 50},
        {"symbol": "VTSAX", "shares": 5, "as_of": "yesterday"}]}]}"""
    r = app.put("/api/manual", content=bad, headers={"content-type": "application/json"})
    assert r.status_code == 400
    text = " | ".join(r.json()["errors"])
    for fragment in ("accounts[0].kind", "holdings[0].shares", "holdings[1].per_paychek", "holdings[2].as_of"):
        assert fragment in text
    assert app.get("/api/manual").json() == {"version": 1, "accounts": []}


def test_each_sync_refreshes_manual_prices_and_a_price_outage_never_fails_the_bank_sync(app):
    app.put("/api/manual", json=MANUAL)
    feed = app.app.state.get_url
    feed.closes["FSPSX"].append(("2026-10-29", 19.00))
    assert [r["status"] for r in app.post("/api/sync", json={"force": True}).json()["results"]] == ["ok", "ok"]
    assert manual_values(app)["manual:FSPSX"] == 190000

    feed.down = True
    assert [r["status"] for r in app.post("/api/sync", json={"force": True}).json()["results"]] == ["ok", "ok"]
    assert manual_values(app) == {"manual:FSPSX": 190000, "manual:TSP:C": 125000}  # the last prices fetched stand


def test_a_half_saved_hand_edit_of_manual_investments_never_fails_the_bank_sync(app, data_dir):
    (data_dir / "manual.json").write_text('{"version": 1, "accounts": [')
    assert [r["status"] for r in app.post("/api/sync", json={"force": True}).json()["results"]] == ["ok", "ok"]


def test_today_asks_for_a_plan_until_one_is_saved_then_shows_safe_to_spend(app):
    assert app.get("/api/today").json()["safe_to_spend"]["status"] == "needs_plan"
    plan = app.get("/api/plan").json()
    plan.update(income={"name": "Pay", "amount": 2400, "schedule": "biweekly", "next_payday": "2026-11-06"},
                bills=[{"name": "Phone", "amount": 50, "due_day": 1}], buffer=100)
    assert app.put("/api/plan", json=plan).status_code == 200

    s = app.get("/api/today").json()["safe_to_spend"]
    assert s["status"] == "ok"
    assert s["per_day_cents"] == (140000 - 5000 - 35000 - 10000) // 8


def test_a_dismissed_alert_stays_gone_across_screens_until_restored(app):
    # 20 hours after the sync, both banks show as stale: one alert each.
    late = datetime.now(timezone.utc) + timedelta(hours=20)
    app.app.state.clock = lambda: late
    before = app.get("/api/alerts").json()
    assert before["dismissed_count"] == 0 and len(before["alerts"]) == 2
    first, second = before["alerts"]

    r = app.put("/api/alerts/dismissed", json={"id": first["id"]})
    assert r.status_code == 200
    assert [a["id"] for a in r.json()["alerts"]] == [second["id"]] and r.json()["dismissed_count"] == 1

    today = app.get("/api/today").json()
    assert [a["id"] for a in today["alerts"]] == [second["id"]] and today["alerts_dismissed"] == 1
    snapshot = app.get("/api/snapshot").text
    assert first["title"] not in snapshot and "1 more dismissed by the owner" in snapshot

    # A dismissal counts only while its alert is live, so "bring back" never promises what isn't there.
    app.app.state.clock = lambda: NOW
    assert app.get("/api/alerts").json() == {"alerts": [], "dismissed_count": 0}
    app.app.state.clock = lambda: late

    r = app.delete("/api/alerts/dismissed")
    assert r.status_code == 200 and len(r.json()["alerts"]) == 2 and r.json()["dismissed_count"] == 0


def test_dismissing_ignores_ids_no_alert_has_and_rejects_a_malformed_body(app):
    r = app.put("/api/alerts/dismissed", json={"id": "nothing:like:this"})
    assert r.status_code == 200 and r.json() == {"alerts": [], "dismissed_count": 0}
    for bad in ({"id": 7}, {}, ["x"]):
        assert app.put("/api/alerts/dismissed", json=bad).status_code == 400
    assert app.put("/api/alerts/dismissed", content="not json").status_code == 400


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


def test_snapshot_of_the_pay_period_screen_falls_back_to_the_month_until_there_is_a_pay_schedule(app):
    assert "Spending transactions 2026-10-01 to" in app.get("/api/snapshot?screen=pay_period").text
    with_pay(app)
    text = app.get("/api/snapshot?screen=pay_period").text
    assert "Spending transactions 2026-10-23 to" in text
    assert "- Since payday (2026-10-23 to 2026-10-29): $30.00" in text


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


def test_requests_for_other_host_names_are_refused(client):
    # A web page that rebinds its own domain to 127.0.0.1 must not be able to read the API.
    assert client.get("/api/accounts", headers={"Host": "evil.example"}).status_code == 400
    assert client.get("/api/accounts", headers={"Host": "localhost:8787"}).status_code == 200
    assert client.get("/api/accounts", headers={"Host": "laptop.tail1234.ts.net"}).status_code == 200


def test_pages_forbid_outside_scripts_and_api_answers_are_never_cached(client):
    page = client.get("/")
    assert page.status_code == 200
    csp = page.headers["content-security-policy"]
    assert "script-src 'self'" in csp and "frame-ancestors 'none'" in csp
    assert page.headers["x-content-type-options"] == "nosniff"
    assert page.headers["referrer-policy"] == "no-referrer"
    api = client.get("/api/accounts")
    assert api.headers["cache-control"] == "no-store"
    assert api.headers["x-content-type-options"] == "nosniff"


def test_scheduler_retries_every_few_minutes_while_plaid_prepares_then_every_six_hours(data_dir, monkeypatch):
    import asyncio
    from datetime import timedelta

    from finance import web

    now = [NOW]
    app = web.create_app(clock=lambda: now[0])
    runs, statuses = [], iter(["not_ready", "not_ready", "error", "ok", "ok"])

    class Done(Exception):
        pass

    async def fake_sync(_app):
        runs.append(now[0])
        status = next(statuses, None)
        if status == "error":
            raise RuntimeError("Plaid unreachable")
        return [{"status": status}]

    async def fake_sleep(seconds):
        now[0] += timedelta(seconds=seconds)
        if now[0] > NOW + timedelta(hours=13):
            raise Done

    monkeypatch.setattr(web, "run_sync", fake_sync)
    monkeypatch.setattr(web.asyncio, "sleep", fake_sleep)
    with pytest.raises(Done):
        asyncio.run(web.scheduler(app))
    assert [(t - NOW) / timedelta(minutes=1) for t in runs] == [0, 5, 10, 370, 730]

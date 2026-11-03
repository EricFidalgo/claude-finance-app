"""Linking, fixing and removing banks from the Money screen, against a fake Plaid."""
import pytest
from starlette.testclient import TestClient

from tests.fakes import BOA, MERRILL, FakeBank, FakePlaid, acct, holding, page, security, txn
from tests.test_api import NOW, bank

JSON = {"Content-Type": "application/json"}


@pytest.fixture
def plaid(data_dir):
    return bank()


@pytest.fixture
def app(plaid):
    from finance.web import create_app

    with TestClient(create_app(clock=lambda: NOW, make_client=lambda env: plaid, get_url=lambda *a: b"{}")) as c:
        assert [r["status"] for r in c.post("/api/sync", json={"force": True}).json()["results"]] == ["ok", "ok"]
        yield c


def connections(app):
    return {c["institution"]: c for c in app.get("/api/status").json()["items"]}


def test_linking_a_new_bank_adds_its_accounts_and_transactions(app, plaid):
    r = app.post("/api/connections/link_token", json={"kind": "transactions"})
    assert r.status_code == 200 and r.json()["link_token"].startswith("link-")
    assert plaid.link_requests[-1]["products"] == ["transactions"]

    plaid.waiting["public-sandbox-chase"] = FakePlaid(item_id="item-chase", institution_id="ins_56", accounts=[
        acct("c-chk", "4444", current=800.00, available=800.00)],
        pages={"": page([txn("c1", "c-chk", 42.00, "2026-10-28", "Bookshop", "Bookshop", "GENERAL_MERCHANDISE")])})
    r = app.post("/api/connections", json={"public_token": "public-sandbox-chase", "institution": "Chase"})
    assert r.status_code == 200, r.text
    assert "Chase" in r.json()["message"]

    assert connections(app)["Chase"]["status"] == "ok"
    assert "4444" in {a["mask"] for a in app.get("/api/accounts").json()["accounts"]}
    assert "Bookshop" in {t["name"] for t in app.get("/api/transactions?period=month").json()["transactions"]}


def test_fixing_a_broken_login_uses_plaids_update_mode_and_syncs_again(app, plaid):
    boa = plaid.fakes["access-production-boa-SECRET1"]
    boa.fail("/item/get", "ITEM_LOGIN_REQUIRED")
    app.post("/api/sync", json={"force": True})
    broken = connections(app)["Bank of America"]
    assert broken["status"] == "login_required"

    r = app.post("/api/connections/link_token", json={"id": broken["id"]})
    assert r.status_code == 200 and "SECRET" not in r.text
    sent = plaid.link_requests[-1]
    assert sent["access_token"] == "access-production-boa-SECRET1"
    assert sent["update"] == {"account_selection_enabled": True} and "products" not in sent

    r = app.post(f"/api/connections/{broken['id']}/renewed", json={})
    assert r.json() == {"message": "Updated Bank of America.", "status": "ok"}
    assert connections(app)["Bank of America"]["status"] == "ok"


def remove(app, name):
    return app.request("DELETE", f"/api/connections/{connections(app)[name]['id']}")


def test_removing_a_bank_tells_plaid_and_forgets_its_accounts_and_transactions(app, plaid):
    r = remove(app, "Bank of America")
    assert r.status_code == 200, r.text
    assert r.json()["message"] == "Removed Bank of America and everything stored from it."
    assert plaid.removed == ["item-boa"]

    assert set(connections(app)) == {"Merrill"}
    # CMA-Edge stays, now under Merrill, which reports it too. The checking account and the card go with their bank.
    assert [(a["mask"], a["institution"]) for a in app.get("/api/accounts").json()["accounts"]] == [("2222", "Merrill")]
    assert app.get("/api/transactions?period=month").json()["transactions"] == []
    synced = app.post("/api/sync", json={"force": True}).json()["results"]
    assert [r["id"] for r in synced] == [connections(app)["Merrill"]["id"]]


def test_removing_the_brokerage_drops_its_holdings_but_keeps_the_account_the_bank_also_reports(app, plaid):
    assert app.get("/api/holdings").json()["holdings"]
    assert remove(app, "Merrill").status_code == 200
    assert app.get("/api/holdings").json()["holdings"] == []
    accounts = app.get("/api/accounts").json()
    assert sorted(a["mask"] for a in accounts["accounts"]) == ["1111", "2222", "3333"]
    assert app.post("/api/sync", json={"force": True}).json()["results"][0]["status"] == "ok"
    assert sorted(a["mask"] for a in app.get("/api/accounts").json()["accounts"]) == ["1111", "2222", "3333"]


def test_a_bank_plaid_already_dropped_is_still_removed_here(app, plaid):
    plaid.fakes.pop("access-production-boa-SECRET1")  # Plaid answers ITEM_NOT_FOUND
    assert remove(app, "Bank of America").status_code == 200
    assert set(connections(app)) == {"Merrill"}


def test_when_plaid_refuses_the_removal_nothing_is_forgotten(app, plaid):
    plaid.fakes["access-production-boa-SECRET1"].fail("/item/remove", "INTERNAL_SERVER_ERROR")
    r = remove(app, "Bank of America")
    assert r.status_code == 502 and r.json()["error"].startswith("INTERNAL_SERVER_ERROR")
    assert set(connections(app)) == {"Bank of America", "Merrill"}
    assert len(app.get("/api/transactions?period=month").json()["transactions"]) == 5


def test_an_unknown_connection_is_a_404(app):
    assert app.request("DELETE", "/api/connections/0123456789abcdef").status_code == 404
    assert app.post("/api/connections/0123456789abcdef/renewed", json={}).status_code == 404


def test_only_read_only_products_can_be_linked(app, plaid):
    asked = len(plaid.link_requests)
    for kind in ("auth", "transfer", None):
        r = app.post("/api/connections/link_token", json={"kind": kind})
        assert r.status_code == 400 and "transactions, investments" in r.json()["error"]
    assert len(plaid.link_requests) == asked
    assert app.post("/api/connections/link_token", json={"kind": "investments"}).status_code == 200
    assert all(set(req.get("products", [])) <= {"transactions", "investments"} for req in plaid.link_requests)


def test_writes_must_be_json_so_another_sites_form_cannot_send_one(app, plaid):
    plaid.waiting["public-x"] = FakePlaid(item_id="item-x")
    r = app.post("/api/connections", content='{"public_token": "public-x"}', headers={"Content-Type": "text/plain"})
    assert r.status_code == 400
    assert "public-x" in plaid.waiting
    assert app.post("/api/connections/link_token", content='{"kind": "transactions"}',
                    headers={"Content-Type": "text/plain"}).status_code == 400


def test_the_browser_never_sees_an_access_token_or_plaids_item_id(app, plaid):
    plaid.waiting["public-sandbox-chase"] = FakePlaid(item_id="item-chase", accounts=[acct("c", "4444")])
    bodies = [app.get("/api/status").text,
              app.post("/api/sync", json={"force": True}).text,
              app.post("/api/connections/link_token", json={"kind": "transactions"}).text,
              app.post("/api/connections/link_token", json={"id": connections(app)["Merrill"]["id"]}).text,
              app.post("/api/connections", json={"public_token": "public-sandbox-chase", "institution": "Chase"}).text,
              app.post(f"/api/connections/{connections(app)['Merrill']['id']}/renewed", json={}).text,
              remove(app, "Chase").text]
    for body in bodies:
        for secret in ("access-", "SECRET", "item-boa", "item-ml", "item-chase"):
            assert secret not in body


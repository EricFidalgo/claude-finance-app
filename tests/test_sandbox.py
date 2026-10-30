"""End to end against Plaid Sandbox (network). Skipped without sandbox keys."""
import os
import time

import pytest

from finance import db, store
from finance.plaid import PlaidClient
from finance.sync import sync_item

pytestmark = [
    pytest.mark.sandbox,
    pytest.mark.skipif(not (os.environ.get("PLAID_CLIENT_ID") and os.environ.get("PLAID_SANDBOX_SECRET")),
                       reason="needs PLAID_CLIENT_ID and PLAID_SANDBOX_SECRET"),
]

PLATYPUS = "ins_109508"  # First Platypus Bank, a Plaid Sandbox institution


def _sandbox_item(client, product):
    public = client.post("/sandbox/public_token/create", {"institution_id": PLATYPUS, "initial_products": [product]})
    res = client.post("/item/public_token/exchange", {"public_token": public["public_token"]})
    return {"item_id": res["item_id"], "institution": "First Platypus Bank", "env": "sandbox",
            "access_token": res["access_token"]}


def _sync_until(conn, client, item, ready, seconds=90):
    deadline = time.time() + seconds
    while True:
        result = sync_item(conn, client, item)
        if result["status"] == "ok" and ready():
            return result
        assert time.time() < deadline, f"sandbox not ready: {result.get('status')} {result.get('error_code')}"
        time.sleep(3)


def test_sandbox_transactions_item_syncs_end_to_end(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    client = PlaidClient("sandbox")
    item = _sandbox_item(client, "transactions")
    _sync_until(conn, client, item, lambda: len(store.transactions(conn)) > 0)

    assert store.accounts(conn)
    assert all(isinstance(t["amount_cents"], int) for t in store.transactions(conn))
    before = len(store.transactions(conn))
    assert sync_item(conn, client, item)["status"] == "ok"  # incremental sync from the saved cursor
    assert len(store.transactions(conn)) >= before
    assert store.items(conn)[0]["status"] == "ok"


def test_sandbox_investments_item_syncs_holdings(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    client = PlaidClient("sandbox")
    item = _sandbox_item(client, "investments")
    _sync_until(conn, client, item, lambda: len(store.holdings(conn)) > 0)

    assert any(h["ticker"] for h in store.holdings(conn))

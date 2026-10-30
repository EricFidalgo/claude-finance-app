import pytest

from finance import db, store
from finance.sync import sync_item
from tests.fakes import BOA, MERRILL, FakePlaid, acct, item, page, txn


@pytest.fixture
def conn(tmp_path):
    return db.connect(tmp_path / "t.db")


def test_sync_stores_accounts_and_transactions_in_cents_with_plaid_sign(conn):
    fake = FakePlaid(
        accounts=[acct("chk", "1111", current=1234.56, available=1200.10),
                  acct("cc", "3333", "credit", "credit card", current=300.25, available=1699.75, limit=2000)],
        pages={"": page(added=[txn("t1", "chk", 12.34, "2026-10-01", merchant="Cafe"),
                               txn("t2", "chk", -500.00, "2026-10-02", name="Payroll", category="INCOME")])},
    )
    result = sync_item(conn, fake, item(fake))

    assert result["status"] == "ok"
    accounts = {a["mask"]: a for a in store.accounts(conn)}
    assert accounts["1111"]["current_cents"] == 123456
    assert accounts["1111"]["available_cents"] == 120010
    assert accounts["3333"]["limit_cents"] == 200000
    amounts = {t["transaction_id"]: t["amount_cents"] for t in store.transactions(conn)}
    assert amounts == {"t1": 1234, "t2": -50000}


def test_sync_pages_through_has_more_and_resumes_from_saved_cursor(conn):
    fake = FakePlaid(
        accounts=[acct("chk", "1111")],
        pages={"": page(added=[txn("t1", "chk", 1, "2026-10-01")], next_cursor="c1", has_more=True),
               "c1": page(added=[txn("t2", "chk", 2, "2026-10-02")], next_cursor="c2")},
    )
    sync_item(conn, fake, item(fake))
    fake.pages["c2"] = page(added=[txn("t3", "chk", 3, "2026-10-03")], next_cursor="c3")
    sync_item(conn, fake, item(fake))

    assert sorted(t["transaction_id"] for t in store.transactions(conn)) == ["t1", "t2", "t3"]
    assert [c for p, c in fake.calls if p == "/transactions/sync"] == ["", "c1", "c2"]


def test_removed_and_modified_transactions_apply_and_posted_replaces_pending(conn):
    fake = FakePlaid(
        accounts=[acct("chk", "1111")],
        pages={"": page(added=[txn("p1", "chk", 20, "2026-10-01", pending=True),
                               txn("t2", "chk", 5, "2026-10-01"),
                               txn("t3", "chk", 7, "2026-10-01")], next_cursor="c1")},
    )
    sync_item(conn, fake, item(fake))
    # Plaid posts p1 as t1 (amount settled at 21.50); it may or may not also list p1 in removed.
    fake.pages["c1"] = page(added=[txn("t1", "chk", 21.50, "2026-10-02", pending_transaction_id="p1")],
                            modified=[txn("t2", "chk", 6, "2026-10-01")], removed=["t3"], next_cursor="c2")
    sync_item(conn, fake, item(fake))

    rows = {t["transaction_id"]: t for t in store.transactions(conn)}
    assert set(rows) == {"t1", "t2"}
    assert rows["t1"]["amount_cents"] == 2150 and rows["t1"]["pending"] is False
    assert rows["t2"]["amount_cents"] == 600


def _status(conn, item_id):
    return next(i for i in store.items(conn) if i["item_id"] == item_id)


def test_product_not_ready_keeps_cursor_and_balances_and_reports_not_ready(conn):
    fake = FakePlaid(accounts=[acct("chk", "1111", current=10)])
    fake.fail("/transactions/sync", "PRODUCT_NOT_READY")
    result = sync_item(conn, fake, item(fake))

    assert result["status"] == "not_ready"
    assert _status(conn, fake.item_id)["status"] == "not_ready"
    assert store.accounts(conn)[0]["current_cents"] == 1000
    sync_item(conn, fake, item(fake))  # ready now: starts from the empty cursor again
    assert [c for p, c in fake.calls if p == "/transactions/sync"] == ["", ""]
    assert _status(conn, fake.item_id)["status"] == "ok"


def test_login_required_marks_item_and_keeps_existing_data(conn):
    fake = FakePlaid(accounts=[acct("chk", "1111")], pages={"": page(added=[txn("t1", "chk", 1, "2026-10-01")])})
    sync_item(conn, fake, item(fake))
    synced_at = _status(conn, fake.item_id)["last_synced_at"]
    fake.fail("/item/get", "ITEM_LOGIN_REQUIRED")
    result = sync_item(conn, fake, item(fake))

    assert result["status"] == "login_required"
    status = _status(conn, fake.item_id)
    assert status["status"] == "login_required" and status["error_code"] == "ITEM_LOGIN_REQUIRED"
    assert status["last_synced_at"] == synced_at
    assert [t["transaction_id"] for t in store.transactions(conn)] == ["t1"]


def test_mutation_during_pagination_restarts_from_the_original_cursor(conn):
    fake = FakePlaid(
        accounts=[acct("chk", "1111")],
        pages={"": page(added=[txn("stale", "chk", 1, "2026-10-01")], next_cursor="c1", has_more=True),
               "c1": page(added=[txn("t2", "chk", 2, "2026-10-02")], next_cursor="c2")},
    )
    original_post = fake.post
    calls = {"n": 0}

    def flaky(path, body):
        if path == "/transactions/sync" and body["cursor"] == "c1" and calls["n"] == 0:
            calls["n"] += 1
            fake.pages[""] = page(added=[txn("t1", "chk", 1, "2026-10-01")], next_cursor="c1", has_more=True)
            from finance.plaid import PlaidError
            raise PlaidError("TRANSACTIONS_SYNC_MUTATION_DURING_PAGINATION")
        return original_post(path, body)

    fake.post = flaky
    assert sync_item(conn, fake, item(fake))["status"] == "ok"
    assert sorted(t["transaction_id"] for t in store.transactions(conn)) == ["t1", "t2"]


def test_error_message_never_contains_the_access_token(conn):
    fake = FakePlaid(accounts=[acct("chk", "1111")])
    fake.fail("/accounts/get", "INTERNAL_SERVER_ERROR")
    result = sync_item(conn, fake, item(fake))

    assert result["status"] == "error"
    assert "access-fake-token" not in repr(result) + repr(store.items(conn))


def _merrill():
    from tests.fakes import holding, security
    return FakePlaid(
        item_id="item-merrill", institution_id=MERRILL, products=("investments",),
        accounts=[acct("m-cma", "2222", "depository", "cash management", current=5000, name="CMA-Edge")],
        securities=[security("s-mm", "MMF", "mutual fund", "Money Market Fund", cash_equivalent=True),
                    security("s-voo", "VOO", "etf", price=500)],
        holdings=[holding("m-cma", "s-mm", 1000, 1000.00), holding("m-cma", "s-voo", 8, 4000.00, 3500.00)],
    )


def test_investments_item_stores_holdings_and_resync_does_not_duplicate(conn):
    fake = _merrill()
    sync_item(conn, fake, item(fake, "Merrill"))
    sync_item(conn, fake, item(fake, "Merrill"))

    rows = {h["ticker"]: h for h in store.holdings(conn)}
    assert set(rows) == {"MMF", "VOO"}
    assert rows["VOO"]["value_cents"] == 400000 and rows["VOO"]["cost_basis_cents"] == 350000
    assert rows["MMF"]["is_cash_equivalent"] is True
    assert "/transactions/sync" not in [p for p, _ in fake.calls]


def test_cma_edge_reported_by_boa_and_merrill_items_counts_once(conn):
    merrill = _merrill()
    boa = FakePlaid(accounts=[acct("b-chk", "1111"),
                              acct("b-cma", "2222", "depository", "cash management", current=5000)],
                    pages={"": page(added=[txn("bt1", "b-cma", 50, "2026-10-01")])})
    sync_item(conn, merrill, item(merrill, "Merrill"))
    sync_item(conn, boa, item(boa))

    assert sorted(a["mask"] for a in store.accounts(conn)) == ["1111", "2222"]
    assert [t["transaction_id"] for t in store.transactions(conn)] == ["bt1"]
    assert len(store.holdings(conn)) == 2


def test_relinked_bank_as_second_item_does_not_double_count_transactions(conn):
    first = FakePlaid(item_id="item-boa-1", accounts=[acct("a1", "1111")],
                      pages={"": page(added=[txn("x1", "a1", 30, "2026-10-01")])})
    second = FakePlaid(item_id="item-boa-2", accounts=[acct("a2", "1111")],
                       pages={"": page(added=[txn("y1", "a2", 30, "2026-10-01")])})
    sync_item(conn, first, item(first))
    sync_item(conn, second, item(second))

    assert len(store.accounts(conn)) == 1
    assert [t["transaction_id"] for t in store.transactions(conn)] == ["y1"]  # the newer Item keeps syncing
    first.pages["c1"] = page(next_cursor="c1")
    sync_item(conn, first, item(first))
    assert [t["transaction_id"] for t in store.transactions(conn)] == ["y1"]


def test_relinking_restarts_the_estimated_consent_year(conn):
    from datetime import date, datetime, timezone

    from finance import money
    from finance.sync import record_relink

    fake = FakePlaid(accounts=[acct("chk", "1111")])
    sync_item(conn, fake, item(fake))
    conn.execute("UPDATE items SET first_seen_at = '2025-11-01T12:00:00+00:00'")  # linked almost a year ago
    now = datetime.now(timezone.utc).isoformat()
    assert money.consent(store.items(conn)[0], now)["days_left"] < 30

    record_relink(conn, fake.item_id)

    renewed = money.consent(store.items(conn)[0], now)
    assert renewed["days_left"] >= 364 and date.fromisoformat(renewed["expires"]).year == date.today().year + 1


def test_relinking_an_unknown_item_is_refused(conn):
    from finance.sync import record_relink

    with pytest.raises(LookupError):
        record_relink(conn, "no-such-item")

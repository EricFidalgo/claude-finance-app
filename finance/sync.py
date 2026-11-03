"""Pull one Plaid Item into the local database.

sync_item(conn, client, item) is the whole job: Item status, balances, transactions (cursor-based
/transactions/sync) and holdings. Plaid errors land on the Item's status row; they never raise.
"""
import json

from . import config, db
from .money import to_cents
from .plaid import PlaidError, now_iso


def account_key(institution_id, account):
    """Institution family + mask, so one account reported by two Items counts once."""
    family = config.INSTITUTION_FAMILY.get(institution_id, institution_id or "unknown")
    return f"{family}:{account.get('mask') or account['account_id']}"


# Plaid errors that need the owner to re-link through Link update mode (Fix login on the Money screen).
LOGIN_CODES = {"ITEM_LOGIN_REQUIRED", "ACCESS_NOT_GRANTED", "NO_ACCOUNTS", "INVALID_ACCESS_TOKEN", "ITEM_NOT_FOUND"}
MUTATION = "TRANSACTIONS_SYNC_MUTATION_DURING_PAGINATION"


def sync_item(conn, client, item):
    item_id = item["item_id"]
    token = {"access_token": item["access_token"]}
    _ensure_item(conn, item_id, item.get("institution"))
    try:
        info = client.post("/item/get", token)["item"]
        if info.get("error") and info["error"].get("error_code"):
            raise PlaidError(info["error"]["error_code"], info["error"].get("error_message") or "")
        _update_item(conn, item_id, info)
        products = info.get("products") or []
        institution_id = info.get("institution_id")

        accounts = client.post("/accounts/get", token)["accounts"]
        with db.transaction(conn):
            keys = _save_accounts(conn, item_id, institution_id, accounts, "transactions" in products)

        if "investments" in products:
            inv = client.post("/investments/holdings/get", token)
            with db.transaction(conn):
                _save_holdings(conn, item_id, keys, inv)

        counts = {"added": 0, "modified": 0, "removed": 0}
        if "transactions" in products:
            added, modified, removed, cursor = _pull_transactions(client, token, _cursor(conn, item_id))
            with db.transaction(conn):
                for t in added + modified:
                    _save_transaction(conn, item_id, keys, t)
                conn.executemany("DELETE FROM transactions WHERE transaction_id = ?", [(r,) for r in removed])
                conn.execute("UPDATE items SET cursor = ? WHERE item_id = ?", (cursor, item_id))
            counts = {"added": len(added), "modified": len(modified), "removed": len(removed)}
    except PlaidError as e:
        status = "login_required" if e.code in LOGIN_CODES else "not_ready" if e.code == "PRODUCT_NOT_READY" else "error"
        conn.execute("UPDATE items SET status = ?, error_code = ?, error_message = ?, last_attempt_at = ?"
                     " WHERE item_id = ?", (status, e.code, e.message, now_iso(), item_id))
        _log(conn, item_id, False, e.code)
        return {"item_id": item_id, "status": status, "error_code": e.code}

    conn.execute("UPDATE items SET status = 'ok', error_code = NULL, error_message = NULL, last_synced_at = ?,"
                 " last_attempt_at = ? WHERE item_id = ?", (now_iso(), now_iso(), item_id))
    _log(conn, item_id, True, json.dumps(counts))
    return {"item_id": item_id, "status": "ok", **counts}


def _pull_transactions(client, token, start_cursor):
    """Page /transactions/sync to the end. Plaid asks to restart from the first cursor on a mutation error."""
    for _ in range(3):
        added, modified, removed, cursor = [], [], [], start_cursor
        try:
            while True:
                res = client.post("/transactions/sync", {**token, "cursor": cursor})
                added += res["added"]
                modified += res["modified"]
                removed += [r["transaction_id"] for r in res["removed"]]
                cursor = res["next_cursor"]
                if not res["has_more"]:
                    return added, modified, removed, cursor
        except PlaidError as e:
            if e.code != MUTATION:
                raise
    raise PlaidError(MUTATION, "data kept changing during sync; will retry next time")


def _log(conn, item_id, ok, detail):
    conn.execute("INSERT INTO sync_log (at, item_id, ok, detail) VALUES (?, ?, ?, ?)", (now_iso(), item_id, int(ok), detail))


def _cursor(conn, item_id):
    row = conn.execute("SELECT cursor FROM items WHERE item_id = ?", (item_id,)).fetchone()
    return row["cursor"] if row else ""


def _ensure_item(conn, item_id, institution):
    conn.execute("INSERT INTO items (item_id, institution, first_seen_at) VALUES (?, ?, ?)"
                 " ON CONFLICT(item_id) DO UPDATE SET institution = COALESCE(excluded.institution, items.institution)",
                 (item_id, institution, now_iso()))


def record_relink(conn, item_id):
    """The owner renewed an Item's login or consent in Plaid Link; an estimated consent year restarts now."""
    if not conn.execute("UPDATE items SET consent_since = ? WHERE item_id = ?", (now_iso(), item_id)).rowcount:
        raise LookupError(item_id)


def forget_item(conn, item_id):
    """Delete everything stored for an Item the owner removed. An account another Item also reports (CMA-Edge,
    through both Bank of America and Merrill) stays, now owned by that Item."""
    with db.transaction(conn):
        for table in ("transactions", "holdings", "account_ids", "sync_log"):
            conn.execute(f"DELETE FROM {table} WHERE item_id = ?", (item_id,))
        for (key,) in conn.execute("SELECT account_key FROM accounts WHERE item_id = :i OR txn_item_id = :i",
                                   {"i": item_id}).fetchall():
            others = [r["item_id"] for r in conn.execute(
                "SELECT a.item_id FROM account_ids a JOIN items i USING (item_id) WHERE a.account_key = ?"
                " GROUP BY a.item_id ORDER BY MAX(i.rowid) DESC", (key,))]
            if not others:
                conn.execute("DELETE FROM accounts WHERE account_key = ?", (key,))
                continue
            # Transactions come from the most recently linked remaining Item that has them, as in _save_accounts.
            txn_owner = next((o for o in others if conn.execute(
                "SELECT 1 FROM items WHERE item_id = ? AND products LIKE '%\"transactions\"%'", (o,)).fetchone()), None)
            conn.execute("UPDATE accounts SET institution = CASE WHEN item_id = :gone THEN"
                         " (SELECT institution FROM items WHERE item_id = :owner) ELSE institution END,"
                         " item_id = CASE WHEN item_id = :gone THEN :owner ELSE item_id END,"
                         " txn_item_id = CASE WHEN txn_item_id = :gone THEN :txn_owner ELSE txn_item_id END"
                         " WHERE account_key = :key", {"gone": item_id, "owner": others[0], "txn_owner": txn_owner, "key": key})
        conn.execute("DELETE FROM securities WHERE security_id NOT IN (SELECT security_id FROM holdings)")
        conn.execute("DELETE FROM items WHERE item_id = ?", (item_id,))


def _update_item(conn, item_id, info):
    conn.execute("UPDATE items SET institution_id = ?, products = ?, consent_expires_at = ? WHERE item_id = ?",
                 (info.get("institution_id"), json.dumps(info.get("products") or []),
                  info.get("consent_expiration_time"), item_id))


def _save_accounts(conn, item_id, institution_id, accounts, owns_transactions):
    keys = {}
    for a in accounts:
        key = account_key(institution_id, a)
        keys[a["account_id"]] = key
        bal = a.get("balances") or {}
        conn.execute(
            "INSERT INTO accounts (account_key, item_id, txn_item_id, institution, name, official_name, type, subtype,"
            " mask, current_cents, available_cents, limit_cents, currency, updated_at)"
            " VALUES (?, ?, ?, (SELECT institution FROM items WHERE item_id = ?), ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(account_key) DO UPDATE SET name = excluded.name, official_name = excluded.official_name,"
            " type = excluded.type, subtype = excluded.subtype, current_cents = excluded.current_cents,"
            " available_cents = excluded.available_cents, limit_cents = excluded.limit_cents,"
            " currency = excluded.currency, updated_at = excluded.updated_at,"
            # Transactions come from one Item per account: the most recently linked one that has them.
            " txn_item_id = CASE WHEN excluded.txn_item_id IS NULL THEN accounts.txn_item_id"
            "   WHEN accounts.txn_item_id IS NULL THEN excluded.txn_item_id"
            "   WHEN (SELECT rowid FROM items WHERE item_id = excluded.txn_item_id)"
            "      > (SELECT rowid FROM items WHERE item_id = accounts.txn_item_id) THEN excluded.txn_item_id"
            "   ELSE accounts.txn_item_id END",
            (key, item_id, item_id if owns_transactions else None, item_id, a.get("name"), a.get("official_name"),
             a.get("type"), a.get("subtype"), a.get("mask"), to_cents(bal.get("current")), to_cents(bal.get("available")),
             to_cents(bal.get("limit")), bal.get("iso_currency_code"), now_iso()),
        )
        conn.execute("INSERT OR REPLACE INTO account_ids (account_id, account_key, item_id) VALUES (?, ?, ?)",
                     (a["account_id"], key, item_id))
    return keys


def _save_holdings(conn, item_id, keys, inv):
    for s in inv["securities"]:
        conn.execute(
            "INSERT OR REPLACE INTO securities (security_id, ticker, name, type, is_cash_equivalent) VALUES (?, ?, ?, ?, ?)",
            (s["security_id"], s.get("ticker_symbol"), s.get("name"), s.get("type"), int(bool(s.get("is_cash_equivalent")))),
        )
    conn.execute("DELETE FROM holdings WHERE item_id = ?", (item_id,))
    for h in inv["holdings"]:
        conn.execute(
            "INSERT OR REPLACE INTO holdings (account_key, security_id, item_id, quantity, value_cents, cost_basis_cents,"
            " updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (keys.get(h["account_id"]) or _key_for(conn, h["account_id"]), h["security_id"], item_id, h.get("quantity"),
             to_cents(h.get("institution_value")), to_cents(h.get("cost_basis")), now_iso()),
        )


def _save_transaction(conn, item_id, keys, t):
    pfc = t.get("personal_finance_category") or {}
    key = keys.get(t["account_id"]) or _key_for(conn, t["account_id"])
    if t.get("pending_transaction_id"):
        conn.execute("DELETE FROM transactions WHERE transaction_id = ?", (t["pending_transaction_id"],))
    conn.execute(
        "INSERT OR REPLACE INTO transactions (transaction_id, item_id, account_id, account_key, amount_cents, date,"
        " authorized_date, name, merchant_name, category, category_detailed, pending, pending_transaction_id, currency)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (t["transaction_id"], item_id, t["account_id"], key, to_cents(t["amount"]), t["date"], t.get("authorized_date"),
         t.get("name"), t.get("merchant_name"), pfc.get("primary"), pfc.get("detailed"), int(bool(t.get("pending"))),
         t.get("pending_transaction_id"), t.get("iso_currency_code")),
    )


def _key_for(conn, account_id):
    row = conn.execute("SELECT account_key FROM account_ids WHERE account_id = ?", (account_id,)).fetchone()
    return row["account_key"] if row else f"unknown:{account_id}"


def sync_all(conn, make_client=None):
    """Sync every Item in items.json. Returns one result per Item (no tokens, no amounts)."""
    from .plaid import PlaidClient, load_items

    make_client = make_client or PlaidClient
    return [sync_item(conn, make_client(item.get("env", "production")), item) for item in load_items()]


if __name__ == "__main__":
    for r in sync_all(db.connect()):
        print(r)

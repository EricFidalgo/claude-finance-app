"""Reads from the local database, as plain dicts. Every page and API endpoint reads through here."""
import json


def accounts(conn):
    rows = conn.execute(
        "SELECT account_key, institution, name, official_name, type, subtype, mask, current_cents, available_cents,"
        " limit_cents, currency, updated_at FROM accounts ORDER BY type, mask"
    ).fetchall()
    return [dict(r) for r in rows]


def transactions(conn, start=None, end=None, account_key=None):
    """Transactions in [start, end] (ISO dates), newest first.

    Dedupe rule: an account reported by two Items takes its transactions from one Item only
    (accounts.txn_item_id), so nothing is counted twice.
    """
    sql = ("SELECT t.transaction_id, t.account_key, t.amount_cents, t.date, t.authorized_date, t.name,"
           " t.merchant_name, t.category, t.category_detailed, t.pending, a.type AS account_type,"
           " a.subtype AS account_subtype, a.mask AS account_mask"
           " FROM transactions t JOIN accounts a ON a.account_key = t.account_key AND a.txn_item_id = t.item_id"
           " WHERE 1 = 1")
    args = []
    if start:
        sql += " AND t.date >= ?"
        args.append(start)
    if end:
        sql += " AND t.date <= ?"
        args.append(end)
    if account_key:
        sql += " AND t.account_key = ?"
        args.append(account_key)
    sql += " ORDER BY t.date DESC, t.transaction_id"
    return [{**dict(r), "pending": bool(r["pending"])} for r in conn.execute(sql, args)]


def items(conn):
    """Linked Items and their sync status. Never includes access tokens (they are not in the database)."""
    rows = conn.execute(
        "SELECT item_id, institution, institution_id, products, status, error_code, error_message,"
        " consent_expires_at, consent_since, first_seen_at, last_synced_at, last_attempt_at FROM items ORDER BY institution"
    ).fetchall()
    return [{**dict(r), "products": json.loads(r["products"])} for r in rows]


def holdings(conn):
    rows = conn.execute(
        "SELECT h.account_key, a.name AS account_name, a.mask AS account_mask, h.security_id, s.ticker, s.name, s.type,"
        " s.is_cash_equivalent, h.quantity, h.value_cents, h.cost_basis_cents, h.updated_at"
        " FROM holdings h LEFT JOIN securities s USING (security_id) LEFT JOIN accounts a USING (account_key)"
        " ORDER BY h.value_cents DESC"
    ).fetchall()
    return [{**dict(r), "is_cash_equivalent": bool(r["is_cash_equivalent"])} for r in rows]

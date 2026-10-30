"""SQLite storage. Amounts are integer cents; positive means money out (Plaid's convention).

No access token is ever stored here: tokens live only in data/items.json.
"""
import sqlite3

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
  item_id TEXT PRIMARY KEY,
  institution TEXT,
  institution_id TEXT,
  products TEXT NOT NULL DEFAULT '[]',
  cursor TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'new',
  error_code TEXT,
  error_message TEXT,
  consent_expires_at TEXT,
  first_seen_at TEXT NOT NULL,
  consent_since TEXT,
  last_synced_at TEXT,
  last_attempt_at TEXT
);
CREATE TABLE IF NOT EXISTS accounts (
  account_key TEXT PRIMARY KEY,
  item_id TEXT NOT NULL,
  txn_item_id TEXT,
  institution TEXT,
  name TEXT,
  official_name TEXT,
  type TEXT,
  subtype TEXT,
  mask TEXT,
  current_cents INTEGER,
  available_cents INTEGER,
  limit_cents INTEGER,
  currency TEXT,
  updated_at TEXT
);
CREATE TABLE IF NOT EXISTS account_ids (
  account_id TEXT PRIMARY KEY,
  account_key TEXT NOT NULL,
  item_id TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS transactions (
  transaction_id TEXT PRIMARY KEY,
  item_id TEXT NOT NULL,
  account_id TEXT NOT NULL,
  account_key TEXT NOT NULL,
  amount_cents INTEGER NOT NULL,
  date TEXT NOT NULL,
  authorized_date TEXT,
  name TEXT,
  merchant_name TEXT,
  category TEXT,
  category_detailed TEXT,
  pending INTEGER NOT NULL DEFAULT 0,
  pending_transaction_id TEXT,
  currency TEXT
);
CREATE INDEX IF NOT EXISTS transactions_date ON transactions(date);
CREATE TABLE IF NOT EXISTS securities (
  security_id TEXT PRIMARY KEY,
  ticker TEXT,
  name TEXT,
  type TEXT,
  is_cash_equivalent INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS holdings (
  account_key TEXT NOT NULL,
  security_id TEXT NOT NULL,
  item_id TEXT NOT NULL,
  quantity REAL,
  value_cents INTEGER,
  cost_basis_cents INTEGER,
  updated_at TEXT,
  PRIMARY KEY (account_key, security_id)
);
CREATE TABLE IF NOT EXISTS sync_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  at TEXT NOT NULL,
  item_id TEXT NOT NULL,
  ok INTEGER NOT NULL,
  detail TEXT
);
"""


def connect(path=None) -> sqlite3.Connection:
    conn = sqlite3.connect(path or config.db_path(), check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    if "consent_since" not in {r["name"] for r in conn.execute("PRAGMA table_info(items)")}:
        conn.execute("ALTER TABLE items ADD COLUMN consent_since TEXT")  # databases from before re-link tracking
    return conn


class transaction:
    """`with transaction(conn):` runs the block as one atomic write."""

    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        self.conn.execute("BEGIN IMMEDIATE")
        return self.conn

    def __exit__(self, exc_type, *_):
        self.conn.execute("ROLLBACK" if exc_type else "COMMIT")

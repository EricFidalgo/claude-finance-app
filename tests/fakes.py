"""A fake Plaid that answers the endpoints sync uses, shaped like Plaid's real responses, and a fake price feed."""
import json
from datetime import datetime
from urllib.error import URLError

from finance.plaid import PlaidError

BOA = "ins_127989"
MERRILL = "ins_115610"


def acct(account_id, mask, type_="depository", subtype="checking", current=0.0, available=None, limit=None, name=None):
    return {
        "account_id": account_id,
        "mask": mask,
        "name": name or f"{subtype} {mask}",
        "official_name": None,
        "type": type_,
        "subtype": subtype,
        "balances": {"current": current, "available": available, "limit": limit, "iso_currency_code": "USD"},
    }


def txn(transaction_id, account_id, amount, date, name="Shop", merchant=None, category="GENERAL_MERCHANDISE",
        detailed=None, pending=False, pending_transaction_id=None):
    return {
        "transaction_id": transaction_id,
        "account_id": account_id,
        "amount": amount,
        "date": date,
        "authorized_date": date,
        "name": name,
        "merchant_name": merchant,
        "personal_finance_category": {"primary": category, "detailed": detailed or f"{category}_OTHER"},
        "pending": pending,
        "pending_transaction_id": pending_transaction_id,
        "iso_currency_code": "USD",
    }


def page(added=(), modified=(), removed=(), next_cursor="c1", has_more=False):
    return {
        "added": list(added),
        "modified": list(modified),
        "removed": [{"transaction_id": r} for r in removed],
        "next_cursor": next_cursor,
        "has_more": has_more,
    }


class FakePlaid:
    def __init__(self, item_id="item-boa", institution_id=BOA, products=("transactions",), accounts=(),
                 pages=None, holdings=(), securities=(), consent_expiration_time=None):
        self.item_id = item_id
        self.institution_id = institution_id
        self.products = list(products)
        self.accounts = list(accounts)
        self.pages = dict(pages or {"": page()})  # cursor -> response
        self.holdings = list(holdings)
        self.securities = list(securities)
        self.consent_expiration_time = consent_expiration_time
        self.errors = {}  # path -> list of error codes to raise, in order
        self.calls = []

    def fail(self, path, *codes):
        self.errors.setdefault(path, []).extend(codes)

    def post(self, path, body):
        self.calls.append((path, body.get("cursor")))
        if self.errors.get(path):
            code = self.errors[path].pop(0)
            raise PlaidError(code, f"fake {code}")
        if path == "/item/get":
            return {"item": {"item_id": self.item_id, "institution_id": self.institution_id,
                             "products": self.products, "consent_expiration_time": self.consent_expiration_time,
                             "error": None}}
        if path == "/accounts/get":
            return {"accounts": self.accounts}
        if path == "/transactions/sync":
            return self.pages.get(body["cursor"]) or page(next_cursor=body["cursor"])  # caught up: nothing new
        if path == "/investments/holdings/get":
            return {"accounts": self.accounts, "holdings": self.holdings, "securities": self.securities}
        raise AssertionError(f"unexpected Plaid call {path}")


def item(fake, institution="Bank of America"):
    return {"item_id": fake.item_id, "institution": institution, "env": "sandbox", "access_token": "access-fake-token"}


def security(security_id, ticker, type_, name=None, cash_equivalent=False, price=1.0):
    return {"security_id": security_id, "ticker_symbol": ticker, "name": name or ticker, "type": type_,
            "is_cash_equivalent": cash_equivalent, "close_price": price}


def holding(account_id, security_id, quantity, value, cost_basis=None):
    return {"account_id": account_id, "security_id": security_id, "quantity": quantity,
            "institution_value": value, "cost_basis": cost_basis}


class FakeBank:
    """Plaid as a whole: routes each call to the FakePlaid that owns the request's access token."""

    def __init__(self, **fakes_by_token):
        self.fakes = fakes_by_token

    def post(self, path, body):
        return self.fakes[body["access_token"]].post(path, body)


class FakeFeed:
    """The price sources as one `get(url, user_agent)`: Yahoo's chart JSON for each symbol in `closes`, tsp.gov's CSV
    for the columns in `tsp`. Both map a name to [(ISO date, price)]. Set `down` to make every request fail."""

    def __init__(self, closes=None, tsp=None):
        self.closes = dict(closes or {})
        self.tsp = dict(tsp or {})
        self.down = False

    def __call__(self, url, user_agent):
        if self.down:
            raise URLError("fake outage")
        if "tsp.gov" in url:
            dates = sorted({d for rows in self.tsp.values() for d, _ in rows}, reverse=True)
            by = {col: dict(rows) for col, rows in self.tsp.items()}
            lines = [",".join(["Date", *by])] + [",".join([d, *(str(by[c].get(d, "")) for c in by)]) for d in dates]
            return "\n".join(lines).encode()
        symbol = url.split("/chart/")[1].split("?")[0]
        rows = self.closes.get(symbol)
        if rows is None:
            return json.dumps({"chart": {"result": None, "error": {"description": "No data found"}}}).encode()
        stamps = [int(datetime.fromisoformat(f"{d}T13:30:00+00:00").timestamp()) for d, _ in rows]  # 9:30 New York
        return json.dumps({"chart": {"result": [{"meta": {"symbol": symbol, "gmtoffset": -14400}, "timestamp": stamps,
                                                 "indicators": {"quote": [{"close": [p for _, p in rows]}]}}],
                                     "error": None}}).encode()

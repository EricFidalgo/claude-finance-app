"""A fake Plaid that answers the endpoints sync uses, shaped like Plaid's real responses."""
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
            return self.pages[body["cursor"]]
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

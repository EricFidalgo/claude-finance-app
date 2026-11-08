"""Investments the owner types in: manual.json, for accounts Plaid can't link. Schema: docs/manual-schema.md.

Amounts in the file are dollars and quantities are shares. `prices` fetches what they're worth;
`money.manual_holdings` values them.
"""
import json
import os
import re

from . import config, money
from .plan import _is_amount, _is_date

EMPTY = {"version": 1, "accounts": []}
# A ticker for Yahoo (FSPSX, BRK-B) or a TSP fund (TSP:C, TSP:L 2050, TSP:L Income).
SYMBOL = re.compile(r"[A-Z0-9][A-Z0-9.^=-]{0,11}|TSP:([GFCSI]|L Income|L 20\d\d)")
ACCOUNT_FIELDS = {"name", "holdings"}
HOLDING_FIELDS = {"symbol", "name", "shares", "balance", "as_of", "per_paycheck", "cost_basis"}


class ManualError(ValueError):
    def __init__(self, errors):
        super().__init__("; ".join(errors))
        self.errors = errors


def load() -> dict:
    path = config.manual_path()
    if not path.exists():
        return json.loads(json.dumps(EMPTY))
    return json.loads(path.read_text())


def _unknown_fields(d, fields, at):
    """A misspelt optional field (per_paychek) would otherwise be dropped without a word."""
    return [f"{at}.{k} is not a field; see docs/manual-schema.md" for k in d if k not in fields]


def _holding_errors(h, at):
    if not isinstance(h, dict):
        return [f"{at} must be an object"]
    errors = _unknown_fields(h, HOLDING_FIELDS, at)
    if h.get("as_of") is not None and not _is_date(h["as_of"]):
        errors.append(f"{at}.as_of must be a date like 2026-10-30")
    if not isinstance(h.get("symbol"), str) or not SYMBOL.fullmatch(h["symbol"]):
        errors.append(f"{at}.symbol must be a ticker like FSPSX or a TSP fund like TSP:C")
    if h.get("name") is not None and not isinstance(h["name"], str):
        errors.append(f"{at}.name must be text")
    if (h.get("shares") is None) == (h.get("balance") is None):
        return errors + [f"{at} needs shares, or a balance and as_of"]
    if h.get("shares") is not None:
        if not _is_amount(h["shares"]) or not h["shares"]:
            errors.append(f"{at}.shares must be a number of shares above 0")
        if h.get("per_paycheck") is not None:
            errors.append(f"{at}.per_paycheck goes with a balance, not shares")
        if h.get("cost_basis") is not None and not _is_amount(h["cost_basis"]):
            errors.append(f"{at}.cost_basis must be what the shares cost in dollars, 0 or more")
        return errors
    if h.get("cost_basis") is not None:
        errors.append(f"{at}.cost_basis goes with shares, not a balance")
    if not _is_amount(h["balance"]):
        errors.append(f"{at}.balance must be a number of dollars, 0 or more")
    if h.get("as_of") is None:
        errors.append(f"{at}.as_of must be the balance's date, like 2026-10-30")
    if h.get("per_paycheck") is not None and not _is_amount(h["per_paycheck"]):
        errors.append(f"{at}.per_paycheck must be a number of dollars, 0 or more")
    return errors


def validate(doc) -> list:
    if not isinstance(doc, dict):
        return ["manual investments must be a JSON object"]
    if not isinstance(doc.get("accounts"), list):
        return ["accounts must be a list"]
    errors, keys = [], set()
    for i, a in enumerate(doc["accounts"]):
        at = f"accounts[{i}]"
        if not isinstance(a, dict):
            errors.append(f"{at} must be an object")
            continue
        errors += _unknown_fields(a, ACCOUNT_FIELDS, at)
        if not isinstance(a.get("name"), str) or not money.slug(a["name"]):
            errors.append(f"{at}.name is required")
        elif money.slug(a["name"]) in keys:
            errors.append(f"{at}.name is already used by another account")
        else:
            keys.add(money.slug(a["name"]))
        if not isinstance(a.get("holdings"), list) or not a["holdings"]:
            errors.append(f"{at}.holdings must list at least one holding")
            continue
        for j, h in enumerate(a["holdings"]):
            errors += _holding_errors(h, f"{at}.holdings[{j}]")
    return errors


def save(doc: dict) -> dict:
    errors = validate(doc)
    if errors:
        raise ManualError(errors)
    path = config.manual_path()
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, indent=2) + "\n")
    os.replace(tmp, path)
    return doc

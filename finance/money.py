"""Money logic: pure functions over the plain dicts that `store` returns.

Conventions (also in CLAUDE.md):
- Amounts are integer cents. Transaction amounts keep Plaid's sign: positive is money out, negative is money in.
- Spending = transactions in spending categories, refunds (negative) netted in. Transfers between own accounts,
  income and credit-card payments are not spending. Pending transactions count.
"""
import calendar
from collections import defaultdict
from datetime import date, datetime, timedelta

from finance import config

# Plaid personal_finance_category values that are not spending.
NOT_SPENDING_PRIMARY = {"INCOME", "TRANSFER_IN"}
NOT_SPENDING_DETAILED = {"TRANSFER_OUT_ACCOUNT_TRANSFER", "LOAN_PAYMENTS_CREDIT_CARD_PAYMENT"}


def is_spending(txn):
    if txn.get("category") in NOT_SPENDING_PRIMARY or txn.get("category_detailed") in NOT_SPENDING_DETAILED:
        return False
    if txn["amount_cents"] < 0 and txn.get("category") in (None, "OTHER"):
        return False  # money in with no spending category is not a refund
    return True


def spending_total(txns):
    return sum(t["amount_cents"] for t in txns if is_spending(t))


def merchant_of(txn):
    return txn.get("merchant_name") or txn.get("name") or "Unknown"


def _iso(d):
    return d.isoformat()


def _same_day_in(year, month, day):
    return date(year, month, min(day, calendar.monthrange(year, month)[1]))


def _prev_month(d):
    return (d.year - 1, 12) if d.month == 1 else (d.year, d.month - 1)


def period_range(period, today):
    """(start, end, previous_start, previous_end) as dates. To-date periods compare with the same elapsed days."""
    if period == "today":
        y = today - timedelta(days=1)
        return today, today, y, y
    if period == "week":
        start = today - timedelta(days=today.weekday())
        return start, today, start - timedelta(days=7), today - timedelta(days=7)
    if period == "last_week":
        start = today - timedelta(days=today.weekday() + 7)
        return start, start + timedelta(days=6), start - timedelta(days=7), start - timedelta(days=1)
    if period == "month":
        start = today.replace(day=1)
        py, pm = _prev_month(today)
        return start, today, date(py, pm, 1), _same_day_in(py, pm, today.day)
    if period == "last_month":
        end = today.replace(day=1) - timedelta(days=1)
        start = end.replace(day=1)
        prev_end = start - timedelta(days=1)
        return start, end, prev_end.replace(day=1), prev_end
    raise ValueError(f"unknown period {period!r}")


def _in(txn, start, end):
    return _iso(start) <= txn["date"] <= _iso(end)


def spend_summary(txns, period, today):
    start, end, pstart, pend = period_range(period, today)
    rows = [t for t in txns if _in(t, start, end) and is_spending(t)]
    previous = sum(t["amount_cents"] for t in txns if _in(t, pstart, pend) and is_spending(t))
    total = sum(t["amount_cents"] for t in rows)
    return {
        "period": period,
        "start": _iso(start),
        "end": _iso(end),
        "total_cents": total,
        "previous": {"start": _iso(pstart), "end": _iso(pend), "total_cents": previous},
        "change_cents": total - previous,
        "by_category": _group(rows, lambda t: t.get("category") or "OTHER", "category"),
        "by_merchant": _group(rows, merchant_of, "merchant"),
        "transactions": rows,
    }


def _group(rows, key, label):
    totals, counts = defaultdict(int), defaultdict(int)
    for t in rows:
        totals[key(t)] += t["amount_cents"]
        counts[key(t)] += 1
    ordered = sorted(totals, key=lambda k: (-totals[k], k))
    return [{label: k, "total_cents": totals[k], "count": counts[k]} for k in ordered]


# --- balances

ASSET_TYPES = {"depository", "investment", "other"}
LIABILITY_TYPES = {"credit", "loan"}


def is_checking(acct):
    return acct.get("type") == "depository" and acct.get("subtype") == "checking"


def spendable(acct):
    """What a deposit account can spend now: available (pending debits taken out) when the bank reports it."""
    return acct["available_cents"] if acct.get("available_cents") is not None else acct.get("current_cents") or 0


def card_owed(acct):
    """Owed on a card including pending charges, which banks show as used credit (limit - available)."""
    current = acct.get("current_cents") or 0
    if acct.get("limit_cents") is not None and acct.get("available_cents") is not None:
        return max(current, acct["limit_cents"] - acct["available_cents"])
    return current


def net_worth(accounts):
    assets = sum(x.get("current_cents") or 0 for x in accounts if x.get("type") in ASSET_TYPES)
    liabilities = sum(card_owed(x) for x in accounts if x.get("type") in LIABILITY_TYPES)
    return {"assets_cents": assets, "liabilities_cents": liabilities, "net_cents": assets - liabilities}


def checking_cents(accounts):
    return sum(spendable(x) for x in accounts if is_checking(x))


def cards_owed_cents(accounts):
    return sum(card_owed(x) for x in accounts if x.get("type") == "credit")


# --- investments

def bucket_of(holding):
    name = (holding.get("name") or "").lower()
    if holding.get("is_cash_equivalent") or holding.get("type") == "cash" or "money market" in name \
            or "deposit progr" in name:
        return "cash"
    return {"etf": "etf", "equity": "stock", "mutual fund": "fund"}.get(holding.get("type"), "other")


def _pct(part, whole):
    return round(100 * part / whole, 1) if whole else 0.0


def allocation(holdings):
    totals = defaultdict(int)
    for x in holdings:
        totals[bucket_of(x)] += x.get("value_cents") or 0
    total = sum(totals.values())
    invested = total - totals["cash"]
    positions = sorted((x for x in holdings if bucket_of(x) != "cash"), key=lambda x: -(x.get("value_cents") or 0))
    largest = positions[0] if positions else None
    return {
        "total_cents": total,
        "invested_cents": invested,
        "idle_cash_cents": totals["cash"],
        "buckets": [{"bucket": b, "value_cents": v, "pct": _pct(v, total)}
                    for b, v in sorted(totals.items(), key=lambda kv: -kv[1]) if v],
        "largest": largest and {"ticker": largest.get("ticker"), "name": largest.get("name"),
                                "value_cents": largest["value_cents"],
                                "pct_of_invested": _pct(largest["value_cents"], invested)},
        "single_stock_pct_of_invested": _pct(totals["stock"], invested),
    }


def cash_cents(accounts, holdings):
    """Cash on hand: checking (available) + cash-like holdings in brokerage/cash accounts - card balances owed.

    A deposit account with holdings (CMA-Edge) counts only its cash-like holdings; one without counts its balance.
    """
    with_holdings = {x["account_key"] for x in holdings}
    total = checking_cents(accounts)
    for x in accounts:
        if x.get("type") == "depository" and not is_checking(x) and x["account_key"] not in with_holdings:
            total += x.get("current_cents") or 0
    total += sum(x.get("value_cents") or 0 for x in holdings if bucket_of(x) == "cash")
    return total - cards_owed_cents(accounts)


def typical_monthly_spend(txns, today):
    """Average spending over the last (up to 3) complete calendar months in history, else None."""
    if not txns:
        return None
    first = min(t["date"] for t in txns)
    months = []
    y, m = _prev_month(today)
    while len(months) < 3:
        start = date(y, m, 1)
        if _iso(start) < first:
            break
        end = _same_day_in(y, m, 31)
        months.append(sum(t["amount_cents"] for t in txns if _in(t, start, end) and is_spending(t)))
        y, m = _prev_month(start)
    return round(sum(months) / len(months)) if months else None


def runway(accounts, holdings, typical_monthly_cents):
    cash = cash_cents(accounts, holdings)
    months = round(cash / typical_monthly_cents, 1) if typical_monthly_cents else None
    return {"cash_cents": cash, "typical_monthly_cents": typical_monthly_cents, "months": months}


# --- plan: payday, bills, safe to spend

def _add_months(d, n, day):
    y, m = divmod(d.month - 1 + n, 12)
    return _same_day_in(d.year + y, m + 1, day)


def next_payday(income, today):
    """First payday strictly after today, rolling the plan's anchor date forward by its schedule."""
    anchor = date.fromisoformat(income["next_payday"])
    schedule = income["schedule"]
    if schedule in ("weekly", "biweekly"):
        step = 7 if schedule == "weekly" else 14
        if anchor > today:
            return anchor
        return anchor + timedelta(days=step * ((today - anchor).days // step + 1))
    if schedule == "monthly":
        n = 0
        while (d := _add_months(anchor, n, anchor.day)) <= today:
            n += 1
        return d
    days = sorted(income.get("days") or [15, 31])
    month = today.replace(day=1)
    for n in range(3):
        for day in days:
            if (d := _add_months(month, n, day)) > today:
                return d
    raise AssertionError("unreachable")


def bill_dates(bill, start, end):
    """Due dates of a monthly bill in [start, end)."""
    out, n = [], 0
    while (d := _add_months(start.replace(day=1), n, bill["due_day"])) < end:
        if d >= start:
            out.append(d)
        n += 1
    return out


def _cents(dollars):
    return round(dollars * 100)


def _name_key(s):
    return (s or "").lower().split()[0] if (s or "").split() else ""


def bill_paid(bill, due, txns, window_days=7):
    """A matching outflow (name, amount within 10% unless variable) within `window_days` before the due date."""
    key, amount = _name_key(bill["name"]), _cents(bill["amount"])
    lo = _iso(due - timedelta(days=window_days))
    for x in txns:
        if not (lo <= x["date"] <= _iso(due)) or x["amount_cents"] <= 0:
            continue
        names = (x.get("merchant_name") or "").lower() + " " + (x.get("name") or "").lower()
        if key and key in names and (not bill.get("fixed", True) or abs(x["amount_cents"] - amount) <= amount / 10):
            return True
    return False


def safe_to_spend(accounts, plan, txns, today):
    """Daily amount that leaves bills due before payday, card balances and the buffer covered."""
    missing = [label for label, ok in (("pay schedule", plan.get("income")), ("bills", plan.get("bills"))) if not ok]
    if missing:
        return {"status": "needs_plan", "missing": missing}
    payday = next_payday(plan["income"], today)
    due = sorted(((d, b) for b in plan["bills"] for d in bill_dates(b, today, payday)
                  if not bill_paid(b, d, txns)), key=lambda db: db[0])
    checking = checking_cents(accounts)
    bills = sum(_cents(b["amount"]) for _, b in due)
    cards = cards_owed_cents(accounts)
    buffer = _cents(plan.get("buffer") or 0)
    available = checking - bills - cards - buffer
    days = (payday - today).days
    return {
        "status": "ok",
        "payday": _iso(payday),
        "days": days,
        "bills": [{"name": b["name"], "due": _iso(d), "amount_cents": _cents(b["amount"])} for d, b in due],
        "available_cents": available,
        "per_day_cents": round(available / days),
        "lines": [{"label": "Checking (available)", "amount_cents": checking},
                  {"label": "Bills before payday", "amount_cents": -bills},
                  {"label": "Credit card owed", "amount_cents": -cards},
                  {"label": "Buffer", "amount_cents": -buffer}],
    }


# --- recurring charges

# (name, lowest gap, highest gap) in days between charges.
CADENCES = (("weekly", 5, 9), ("biweekly", 12, 16), ("monthly", 26, 35))


def merchant_key(txn):
    """Groups name variants of one merchant ("Streamly", "Streamly Premium")."""
    return _name_key(merchant_of(txn)).strip(".,*#")


def _cadence(days):
    gaps = [(b - a).days for a, b in zip(days, days[1:])]
    for name, lo, hi in CADENCES:
        if gaps and all(lo <= g <= hi for g in gaps):
            return name
    return None


def next_after(last, cadence):
    if cadence == "monthly":
        return _add_months(last, 1, last.day)
    return last + timedelta(days=7 if cadence == "weekly" else 14)


def _median(xs):
    xs = sorted(xs)
    mid = len(xs) // 2
    return xs[mid] if len(xs) % 2 else round((xs[mid - 1] + xs[mid]) / 2)


def _series(rows, cadence, fixed):
    last = rows[-1]
    names = [merchant_of(x) for x in rows]
    last_date = date.fromisoformat(last["date"])
    return {
        "merchant": max(set(names), key=lambda n: (names.count(n), n == merchant_of(last))),
        "key": merchant_key(last),
        "amount_cents": last["amount_cents"],
        "median_cents": _median([x["amount_cents"] for x in rows]),
        "cadence": cadence,
        "count": len(rows),
        "last_date": last["date"],
        "next_due": _iso(next_after(last_date, cadence)),
        "fixed": fixed,
        "account_key": last.get("account_key"),
        "category": last.get("category"),
        "dates": [x["date"] for x in rows],
    }


def detect_recurring(txns, today):
    """Recurring outflows: same merchant charging a steady amount (fixed) or any amount (variable) on a cadence.

    Within a merchant, charges within 5% of each other form one fixed series, so several subscriptions billed under
    one name (Apple.com/bill) stay separate. A lone charge the bank labels RECURRING counts as monthly.
    """
    by_merchant = defaultdict(list)
    for x in sorted(txns, key=lambda x: x["date"]):
        if x["amount_cents"] > 0 and is_spending(x):
            by_merchant[merchant_key(x)].append(x)

    found = []
    for rows in by_merchant.values():
        clusters = []
        for x in rows:
            for c in clusters:
                if abs(x["amount_cents"] - c[0]["amount_cents"]) <= c[0]["amount_cents"] * 0.05:
                    c.append(x)
                    break
            else:
                clusters.append([x])
        fixed = []
        for c in clusters:
            cadence = len(c) >= 2 and _cadence([date.fromisoformat(x["date"]) for x in c])
            if cadence:
                fixed.append(_series(c, cadence, True))
        found += fixed
        if not fixed and len(rows) >= 2 and (cadence := _cadence([date.fromisoformat(x["date"]) for x in rows])):
            found.append(_series(rows, cadence, False))
            continue
        for c in clusters:
            if len(c) == 1 and "RECURRING" in (c[0].get("name") or "").upper():
                found.append(_series(c, "monthly", True))
    # A series is over once a whole extra cycle passes with no charge.
    grace = {name: hi for name, _, hi in CADENCES}
    active = [s for s in found if date.fromisoformat(s["next_due"]) + timedelta(days=grace[s["cadence"]]) >= today]
    return sorted(active, key=lambda s: (s["next_due"], s["merchant"]))


# --- unusual charges

def _days_apart(a, b):
    return abs((date.fromisoformat(a) - date.fromisoformat(b)).days)


def _flag(kind, x, detail, previous=None):
    return {"kind": kind, "transaction_id": x["transaction_id"], "merchant": merchant_of(x),
            "amount_cents": x["amount_cents"], "date": x["date"], "account_key": x.get("account_key"),
            "previous_cents": previous, "detail": detail}


def _dollars(c):
    return f"${c / 100:,.2f}"


def unusual_charges(txns, today, days=14):
    """Charges in the last `days` worth a look: possible duplicates, recurring charges that went up, big one-offs."""
    outflows = sorted((x for x in txns if x["amount_cents"] > 0 and is_spending(x)),
                      key=lambda x: (x["date"], x["transaction_id"]))
    typical = _median([x["amount_cents"] for x in outflows]) if outflows else 0
    since = _iso(today - timedelta(days=days))
    found = []
    for i, x in enumerate(outflows):
        if x["date"] < since:
            continue
        key = merchant_key(x)
        earlier = outflows[:i]
        if any(merchant_key(y) == key and y["amount_cents"] == x["amount_cents"]
               and y.get("account_key") == x.get("account_key") and _days_apart(y["date"], x["date"]) <= 2
               for y in earlier):
            found.append(_flag("duplicate", x, f"Same amount at {merchant_of(x)} within 2 days"))
            continue
        prior = [y for y in earlier if merchant_key(y) == key and y["date"] < x["date"]]
        due = [s for s in detect_recurring(prior, date.fromisoformat(x["date"]))
               if _days_apart(s["next_due"], x["date"]) <= 5]
        if due:
            s = min(due, key=lambda s: abs(s["amount_cents"] - x["amount_cents"]))
            if s["fixed"] and abs(x["amount_cents"] - s["amount_cents"]) >= s["amount_cents"] / 100:
                found.append(_flag("price_change", x, f"Was {_dollars(s['amount_cents'])}, now "
                                                      f"{_dollars(x['amount_cents'])}", s["amount_cents"]))
            elif not s["fixed"] and x["amount_cents"] > 1.5 * s["median_cents"]:
                found.append(_flag("higher_than_usual", x, f"Usually about {_dollars(s['median_cents'])}",
                                   s["median_cents"]))
        elif len(prior) <= 1 and x["amount_cents"] >= 10000 and x["amount_cents"] >= 3 * typical:
            found.append(_flag("large", x, "Large charge at a merchant you rarely use"))
    return sorted(found, key=lambda u: u["date"], reverse=True)


# --- upcoming bills

def _matches(bill, series):
    if _name_key(bill["name"]).strip(".,*#") != series["key"]:
        return False
    if not bill.get("fixed", True) or not series["fixed"]:
        return True
    return abs(series["amount_cents"] - _cents(bill["amount"])) <= _cents(bill["amount"]) / 10


def upcoming_bills(accounts, plan, recurring, txns, today, days=35):
    """Bills due in the next `days`: the plan's, plus detected recurring charges the plan doesn't list.

    Coverage walks checking forward in date order (paydays add income, bills paid from checking subtract) and marks
    each checking-paid bill covered or not. Bills charged to a card have coverage None.
    """
    end = today + timedelta(days=days)
    by_mask = {x["mask"]: x["account_key"] for x in accounts}
    checking = {x["account_key"] for x in accounts if is_checking(x)}
    default = next(iter(sorted(checking)), None)
    rows = []
    for b in plan.get("bills") or []:
        for d in bill_dates(b, today, end):
            rows.append({"name": b["name"], "due": _iso(d), "amount_cents": _cents(b["amount"]), "source": "plan",
                         "fixed": b.get("fixed", True), "autopay": b.get("autopay"), "category": b.get("category"),
                         "account_key": by_mask.get(b.get("account"), default) if b.get("account") else default,
                         "paid": bill_paid(b, d, txns)})
    declared = plan.get("bills") or []
    for s in recurring:
        if any(_matches(b, s) for b in declared):
            continue
        d = date.fromisoformat(s["next_due"])
        while d < today:
            d = next_after(d, s["cadence"])
        while d < end:
            rows.append({"name": s["merchant"], "due": _iso(d),
                         "amount_cents": s["amount_cents"] if s["fixed"] else s["median_cents"], "source": "detected",
                         "fixed": s["fixed"], "autopay": None, "category": s.get("category"),
                         "account_key": s.get("account_key"), "paid": False})
            d = next_after(d, s["cadence"])

    paydays = []
    if plan.get("income"):
        d = next_payday(plan["income"], today)
        while d < end:
            paydays.append(d)
            d = next_payday(plan["income"], d)
    pay = _cents(plan["income"]["amount"]) if plan.get("income") else 0

    rows.sort(key=lambda r: (r["due"], r["name"]))
    balance, pending_paydays = checking_cents(accounts), list(paydays)
    for r in rows:
        while pending_paydays and _iso(pending_paydays[0]) <= r["due"]:
            balance += pay
            pending_paydays.pop(0)
        r["estimated"] = not r["fixed"]
        if r["account_key"] not in checking:
            r["covered"] = None
        elif r["paid"]:
            r["covered"] = True
        else:
            balance -= r["amount_cents"]
            r["covered"] = balance >= 0
    return {"start": _iso(today), "end": _iso(end - timedelta(days=1)), "bills": rows,
            "paydays": [_iso(d) for d in paydays], "uncovered": sum(r["covered"] is False for r in rows)}


# --- investing: left to invest

RESERVE_MONTHS = (0, 1, 3)


def left_to_invest(accounts, holdings, plan, upcoming, typical_monthly_cents):
    """Idle brokerage cash plus whatever checking holds beyond card balances, upcoming bills and the buffer.

    Options show how much remains if some months of typical spending stay in cash. The owner decides.
    """
    checking = {x["account_key"] for x in accounts if is_checking(x)}
    bills = sum(b["amount_cents"] for b in upcoming["bills"] if b.get("account_key") in checking and not b["paid"])
    cards = cards_owed_cents(accounts)
    buffer = _cents(plan.get("buffer") or 0)
    checking_left = max(0, checking_cents(accounts) - cards - bills - buffer)
    idle = sum(x.get("value_cents") or 0 for x in holdings if bucket_of(x) == "cash")
    total = idle + checking_left
    months = RESERVE_MONTHS if typical_monthly_cents else (0,)
    return {
        "idle_cash_cents": idle,
        "checking_left_cents": checking_left,
        "total_cents": total,
        "lines": [{"label": "Checking (available)", "amount_cents": checking_cents(accounts)},
                  {"label": "Credit card owed", "amount_cents": -cards},
                  {"label": "Upcoming bills from checking", "amount_cents": -bills},
                  {"label": "Buffer", "amount_cents": -buffer},
                  {"label": "Idle cash in brokerage", "amount_cents": idle}],
        "options": [{"months": m, "amount_cents": max(0, total - m * (typical_monthly_cents or 0)),
                     "label": "Invest it all" if m == 0 else
                     f"Keep {m} month{'s' if m > 1 else ''} of spending in cash"} for m in months],
        "missing": [k for k in ("bills", "buffer") if not plan.get(k)],
    }


# --- attention list

LEVELS = {"urgent": 0, "warn": 1, "info": 2}


def _when(ts):
    return datetime.fromisoformat(ts)


def consent(item, now):
    """Consent expiry and days left: Plaid's date when it reports one, else an estimate for banks known to expire."""
    today = _when(now).date()
    if item.get("consent_expires_at"):
        expires, estimated = _when(item["consent_expires_at"]).date(), False
    elif item.get("institution_id") in config.CONSENT_MONTHS and item.get("first_seen_at"):
        seen = _when(item["first_seen_at"]).date()
        expires, estimated = _add_months(seen, config.CONSENT_MONTHS[item["institution_id"]], seen.day), True
    else:
        return None
    return {"expires": _iso(expires), "days_left": (expires - today).days, "estimated": estimated}


def _alert(level, kind, title, detail):
    return {"level": level, "kind": kind, "title": title, "detail": detail}


def alerts(items, upcoming, unusual, now):
    """The attention list at the top of Today, most urgent first."""
    found = []
    for it in items:
        name = it["institution"]
        if it["status"] == "login_required":
            found.append(_alert("urgent", "login_required", f"{name} needs you to sign in again",
                                "Run ./run.sh link on the computer and choose Fix login. Data stops updating until then."))
        elif it["status"] == "error":
            found.append(_alert("warn", "sync_error", f"{name} sync failed",
                                f"{it.get('error_code') or 'Unknown error'}. The next sync will retry."))
        elif it["status"] == "not_ready":
            found.append(_alert("info", "not_ready", f"{name} is still preparing data",
                                "Plaid is gathering history. The app retries every few minutes."))
        c = consent(it, now)
        if c and c["days_left"] <= 30:
            found.append(_alert("urgent" if c["days_left"] <= 7 else "warn", "consent_expiring",
                                f"{name} access ends in {c['days_left']} days",
                                f"{'About ' if c['estimated'] else ''}{c['expires']}. Re-link with ./run.sh link "
                                "before then to keep data flowing."))
    for b in upcoming["bills"]:
        if b.get("covered") is False:
            found.append(_alert("urgent", "uncovered_bill", f"Checking may not cover {b['name']}",
                                f"{_dollars(b['amount_cents'])} due {b['due']}."))
    for u in unusual:
        found.append(_alert("warn", "unusual_charge", f"{u['merchant']} {_dollars(u['amount_cents'])} on {u['date']}",
                            u["detail"]))
    stale_after = timedelta(hours=config.STALE_AFTER_HOURS)
    for it in items:
        last = it.get("last_synced_at")
        age = _when(now) - _when(last) if last else None
        if it["status"] == "ok" and (age is None or age > stale_after):
            ago = f"{int(age.total_seconds() // 3600)} hours ago" if age else "never"
            found.append(_alert("warn", "stale_sync", f"{it['institution']} last synced {ago}",
                                "Sync runs every 6 hours while the container is up. Tap Sync now to retry."))
    return sorted(found, key=lambda x: LEVELS[x["level"]])

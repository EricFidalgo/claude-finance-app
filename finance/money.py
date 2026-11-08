"""Money logic: pure functions over the plain dicts that `store` returns.

Conventions (also in CLAUDE.md):
- Amounts are integer cents. Transaction amounts keep Plaid's sign: positive is money out, negative is money in.
- Spending = transactions in spending categories, refunds (negative) netted in. Transfers between own accounts,
  income and credit-card payments are not spending. Pending transactions count.
"""
import calendar
import hashlib
import math
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from finance import config

# Plaid personal_finance_category values that are not spending.
NOT_SPENDING_PRIMARY = {"INCOME", "TRANSFER_IN"}
# Money moved to the owner's own accounts; transfers to people (TRANSFER_OUT_FROM_APPS) still count.
NOT_SPENDING_DETAILED = {"TRANSFER_OUT_ACCOUNT_TRANSFER", "TRANSFER_OUT_INVESTMENT_AND_RETIREMENT_FUNDS",
                         "TRANSFER_OUT_SAVINGS", "LOAN_PAYMENTS_CREDIT_CARD_PAYMENT"}


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


def period_range(period, today, income=None, start=None, end=None):
    """(start, end, previous_start, previous_end) as dates. To-date periods compare with the same elapsed days.
    The pay period runs from the last payday on or before today, so it needs the plan's income. A custom
    period is start..end, compared with as many days just before it."""
    if period == "custom":
        return start, end, start - (end - start) - timedelta(days=1), start - timedelta(days=1)
    if period == "pay_period":
        if not income:
            raise ValueError("the pay period needs a pay schedule in the plan")
        start = previous_payday(income, today)
        pstart = previous_payday(income, start - timedelta(days=1))
        return start, today, pstart, min(pstart + (today - start), start - timedelta(days=1))
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


def spend_summary(txns, period, today, income=None, start=None, end=None):
    start, end, pstart, pend = period_range(period, today, income, start, end)
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


def owed(acct):
    """What a card or loan account is owed, or None for an account that holds money."""
    return card_owed(acct) if acct.get("type") in LIABILITY_TYPES else None


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


def account_parts(account, holdings):
    """An account with holdings (CMA-Edge) as cash and invested. Its available balance is only the settled sweep
    cash, so it reads as tiny beside the total; the holdings say what the total is made of. Any balance beyond the
    holdings counts as cash. None for an account without holdings."""
    mine = [x for x in holdings if x["account_key"] == account["account_key"]]
    if not mine:
        return None
    cash = sum(x.get("value_cents") or 0 for x in mine if bucket_of(x) == "cash")
    invested = sum(x.get("value_cents") or 0 for x in mine if bucket_of(x) != "cash")
    extra = max(0, (account.get("current_cents") or 0) - cash - invested)
    return {"cash_cents": cash + extra, "invested_cents": invested}


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


def runway(accounts, holdings, typical_monthly_cents, plan=None):
    """Months cash on hand lasts. Measured against the plan's monthly costs (bills plus spending targets) once it
    has any, since a typical month from history can carry one-offs like moving in; the typical month stays beside it.
    """
    cash = cash_cents(accounts, holdings)
    planned = buffer_months(plan)["monthly_cents"] if plan else 0
    monthly = planned or typical_monthly_cents

    def months(per):
        return round(cash / per, 1) if per else None
    return {"cash_cents": cash, "basis": "plan" if planned else "typical", "monthly_cents": monthly,
            "months": months(monthly), "typical_monthly_cents": typical_monthly_cents,
            "typical_months": months(typical_monthly_cents)}


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


def previous_payday(income, today):
    """Last payday on or before today: where the current pay period starts. Every schedule pays within 62 days."""
    d = today - timedelta(days=62)
    anchored, last = _anchored_by(income, d), None
    while (d := next_payday(anchored, d)) <= today:
        last = d
    return last


def bill_dates(bill, start, end):
    """Due dates of a monthly bill in [start, end)."""
    out, n = [], 0
    while (d := _add_months(start.replace(day=1), n, bill["due_day"])) < end:
        if d >= start:
            out.append(d)
        n += 1
    return out


def to_cents(dollars):
    """Dollars (Plaid's floats, plan amounts) to integer cents, via Decimal so float noise never shifts a cent."""
    if dollars is None:
        return None
    return int((Decimal(str(dollars)) * 100).quantize(Decimal(1), ROUND_HALF_UP))


def buffer_months(plan):
    """How many months of planned costs (bills plus spending targets) the buffer covers; None with no costs planned."""
    buffer = to_cents(plan.get("buffer") or 0)
    bills = sum(to_cents(b.get("amount") or 0) for b in plan.get("bills") or [])
    targets = sum(to_cents(v or 0) for v in (plan.get("monthly_spend") or {}).values())
    monthly = bills + targets
    return {"buffer_cents": buffer, "bills_cents": bills, "targets_cents": targets, "monthly_cents": monthly,
            "months": round(buffer / monthly, 1) if monthly else None}


def _name_key(s):
    return (s or "").lower().split()[0] if (s or "").split() else ""


def pays_bill(bill, txn):
    """An outflow naming the bill, for its amount within 10% unless the bill varies."""
    key, amount = _name_key(bill["name"]), to_cents(bill["amount"])
    names = (txn.get("merchant_name") or "").lower() + " " + (txn.get("name") or "").lower()
    return txn["amount_cents"] > 0 and bool(key) and key in names and \
        (not bill.get("fixed", True) or abs(txn["amount_cents"] - amount) <= amount / 10)


PAID_EARLY_DAYS, PAID_LATE_DAYS = 7, 5


def bill_paid(bill, due, txns):
    """A payment of the bill from a week before the due date to a few days after it."""
    lo, hi = _iso(due - timedelta(days=PAID_EARLY_DAYS)), _iso(due + timedelta(days=PAID_LATE_DAYS))
    return any(lo <= x["date"] <= hi and pays_bill(bill, x) for x in txns)


def loan_payoff(bill, txns, today):
    """A plan bill with a `balance` (as of `balance_as_of`) counts down by each payment posted after that date.

    The rest is paid at the bill amount on its coming due dates (one already paid early is skipped, one a few days
    late and still unpaid counts as coming); `payoff` is the
    last payment's date, or the date of the payment that cleared it.
    """
    amount = to_cents(bill["amount"])
    payments = sorted((x for x in txns if x["date"] > bill["balance_as_of"] and pays_bill(bill, x)),
                      key=lambda x: x["date"])
    paid = sum(x["amount_cents"] for x in payments)
    remaining = max(0, to_cents(bill["balance"]) - paid)
    left = math.ceil(remaining / amount) if amount else None
    out = {"name": bill["name"], "balance_cents": to_cents(bill["balance"]), "balance_as_of": bill["balance_as_of"],
           "paid_cents": paid, "remaining_cents": remaining, "payments_left": left, "next_due": None,
           "payoff": payments[-1]["date"] if payments and not remaining else None,
           "last_payment_cents": remaining - amount * (left - 1) if left else None}
    if left:
        due, n = [], 0
        while len(due) < left:
            d = _add_months(today.replace(day=1), n, bill["due_day"])
            if d >= today - timedelta(days=PAID_LATE_DAYS) and not bill_paid(bill, d, txns):
                due.append(d)
            n += 1
        out.update(next_due=_iso(due[0]), payoff=_iso(due[-1]))
    return out


PAYCHECK_LOOKBACK_DAYS = 3  # a deposit can post a day or two ahead of payday


def expected_income_cents(income, txns, today):
    """The paycheck due today that hasn't shown up in the synced transactions yet, else 0.

    On payday the plan's next payday is already two weeks out, so without this a paycheck that is still syncing
    would leave the pool short. Any income, or an inflow at least half a paycheck, since a few days before payday
    counts as the paycheck having landed, so it is never added on top of a checking balance that holds it.
    """
    if previous_payday(income, today) != today:
        return 0
    pay = to_cents(income["amount"])
    since = _iso(today - timedelta(days=PAYCHECK_LOOKBACK_DAYS))
    landed = any(x["date"] >= since and x["amount_cents"] < 0 and
                 (x.get("category") == "INCOME" or -x["amount_cents"] * 2 >= pay) for x in txns)
    return 0 if landed else pay


def goal_progress(goal, income, today):
    """Where a savings goal stands. Its money stays in checking: `saved` dollars were already set aside on `start`,
    and each payday after `start`, up to (not on) `by`, sets aside an equal slice of the rest. Safe-to-spend keeps
    the set-aside part out of reach. On `by` it is freed to spend, so it shows as saved but holds nothing back.
    """
    target, base = to_cents(goal["target"]), to_cents(goal.get("saved") or 0)
    out = {"name": goal["name"], "target_cents": target, "by": goal.get("by"),
           "set_aside_cents": 0, "saved_cents": 0, "per_payday_cents": None, "paydays_left": None, "next_payday": None}
    if not goal.get("by") or not goal.get("start"):
        return {**out, "status": "no_date"}
    if not income:
        return {**out, "status": "needs_pay_schedule"}
    by = date.fromisoformat(goal["by"])
    if today >= by:
        return {**out, "status": "ready", "saved_cents": target}
    paydays, d = [], date.fromisoformat(goal["start"])
    income = _anchored_by(income, d)  # the plan's anchor payday may be after the goal started
    while (d := next_payday(income, d)) < by:
        paydays.append(d)
    done = sum(d <= today for d in paydays)
    left, rest = paydays[done:], max(0, target - base)
    # with no payday before the date, the whole target is set aside now
    set_aside = base + round(rest * done / len(paydays)) if paydays else target
    return {**out, "status": "saving", "set_aside_cents": set_aside, "saved_cents": set_aside,
            "per_payday_cents": round(rest / len(paydays)) if paydays else None,
            "paydays_left": len(left), "next_payday": _iso(left[0]) if left else None}


def goals_set_aside_cents(plan, today):
    return sum(goal_progress(g, plan.get("income"), today)["set_aside_cents"] for g in plan.get("goals") or [])


def _goals_line(goals_cents):
    return [{"label": "Saving for goals", "amount_cents": -goals_cents}] if goals_cents else []


def safe_to_spend(accounts, plan, txns, today):
    """Daily amount that leaves bills due before payday, card balances and the buffer covered."""
    missing = [label for label, ok in (("pay schedule", plan.get("income")), ("bills", plan.get("bills"))) if not ok]
    if missing:
        return {"status": "needs_plan", "missing": missing}
    payday = next_payday(plan["income"], today)
    # A bill that came due a few days ago and hasn't been paid yet still counts, at its plan amount (the estimate
    # for a variable bill) until the payment posts: bill_paid looks that far after the due date too.
    due = sorted(((d, b) for b in plan["bills"]
                  for d in bill_dates(b, today - timedelta(days=PAID_LATE_DAYS), payday)
                  if not bill_paid(b, d, txns)), key=lambda db: db[0])
    checking = checking_cents(accounts)
    bills = sum(to_cents(b["amount"]) for _, b in due)
    cards = cards_owed_cents(accounts)
    buffer = to_cents(plan.get("buffer") or 0)
    expected_income = expected_income_cents(plan["income"], txns, today)
    goals_cents = goals_set_aside_cents(plan, today)
    available = checking + expected_income - bills - cards - buffer - goals_cents
    days = (payday - today).days
    # Today's allowance is what the day started with, so it holds still while you spend. A plan bill paid today
    # is a bill, not spending, so it neither counts as spent nor comes back. Only a bill due inside bill_paid's window
    # counts as paid by today's payment, so a purchase that merely shares a bill's name is spending.
    due_soon = [b for b in plan["bills"] if bill_dates(b, today - timedelta(days=PAID_LATE_DAYS),
                                                       today + timedelta(days=PAID_EARLY_DAYS + 1))]
    today_spending = [x for x in txns if x["date"] == _iso(today) and is_spending(x)]
    bills_paid = sum(x["amount_cents"] for x in today_spending if any(pays_bill(b, x) for b in due_soon))
    spent = sum(x["amount_cents"] for x in today_spending) - bills_paid
    allowance = round((available + spent) / days)
    return {
        "status": "ok",
        "payday": _iso(payday),
        "days": days,
        "bills": [{"name": b["name"], "due": _iso(d), "amount_cents": to_cents(b["amount"])} for d, b in due],
        "expected_income_cents": expected_income,
        "available_cents": available,
        "per_day_cents": round(available / days),
        "spent_today_cents": spent,
        "bills_paid_today_cents": bills_paid,
        "allowance_cents": allowance,
        "left_today_cents": allowance - spent,
        "lines": [{"label": "Checking (available)", "amount_cents": checking},
                  *([{"label": "Payday income (not synced yet)", "amount_cents": expected_income}]
                    if expected_income else []),
                  {"label": "Bills before payday", "amount_cents": -bills},
                  {"label": "Credit card owed", "amount_cents": -cards},
                  {"label": "Buffer", "amount_cents": -buffer},
                  *_goals_line(goals_cents)],
    }


# --- manually entered holdings (manual.json)

def slug(name):
    return "-".join("".join(c if c.isalnum() else " " for c in name.lower()).split())


def price_on(series, d):
    """The last (ISO date, price) on or before date d in an oldest-first series, else None."""
    found = None
    for row in series:
        if row[0] > _iso(d):
            break
        found = row
    return found


def _price(row):
    return Decimal(str(row[1]))


def _anchored_by(income, d):
    """`income` with its anchor payday moved back whole periods to d or earlier. next_payday never counts back
    from the anchor (a new job's first payday has none before it), but a balance on d was being paid into."""
    anchor = date.fromisoformat(income["next_payday"])
    if anchor <= d or income["schedule"] == "semimonthly":  # semimonthly paydays come from its days, not the anchor
        return income
    if income["schedule"] == "monthly":
        anchor = _add_months(anchor, -12 * (anchor.year - d.year + 1), anchor.day)  # whole years keep the day
    else:
        step = 7 if income["schedule"] == "weekly" else 14
        anchor -= timedelta(days=step * -((d - anchor).days // step))
    return {**income, "next_payday": anchor.isoformat()}


def manual_shares(holding, series, income, today):
    """Shares held today. A holding entered as a balance (TSP) converts it at the price on its as_of date, then
    buys per_paycheck more at each plan payday since, at that day's price. None until a price covers as_of."""
    if holding.get("shares") is not None:
        return Decimal(str(holding["shares"]))
    as_of = date.fromisoformat(holding["as_of"])
    start = price_on(series, as_of)
    if start is None:
        return None
    shares = Decimal(str(holding["balance"])) / _price(start)
    d, income = as_of, income and _anchored_by(income, as_of)
    while income and holding.get("per_paycheck") and (d := next_payday(income, d)) <= today:
        shares += Decimal(str(holding["per_paycheck"])) / _price(price_on(series, d))
    return shares


def manual_holdings(doc, prices, income, today):
    """Accounts and holdings from manual.json, valued at the latest stored price.

    `prices` maps symbol -> [(ISO date, price)], oldest first. Rows are shaped like `store.accounts` and
    `store.holdings`, so net worth, allocation and the snapshot treat them like linked ones.
    """
    accounts, holdings = [], []
    for a in doc.get("accounts") or []:
        key = f"manual:{slug(a['name'])}"
        rows = []
        for h in a["holdings"]:
            series = prices.get(h["symbol"]) or []
            latest = series[-1] if series else None
            shares = manual_shares(h, series, income, today)
            priced = shares is not None and latest is not None
            rows.append({"account_key": key, "account_name": a["name"], "account_mask": None,
                         # TSP:C and the like name a fund at a source, not an exchange ticker
                         "security_id": f"manual:{h['symbol']}", "ticker": None if ":" in h["symbol"] else h["symbol"],
                         "name": h.get("name") or h["symbol"], "type": "mutual fund", "is_cash_equivalent": False,
                         "quantity": float(shares) if shares is not None else None,
                         "value_cents": to_cents(shares * _price(latest)) if priced else None,
                         "cost_basis_cents": to_cents(h.get("cost_basis")),
                         "updated_at": latest[0] if priced else None})
        holdings += rows
        accounts.append({"account_key": key, "institution": "Manual", "name": a["name"], "official_name": None,
                         "type": "investment", "subtype": None, "mask": None,
                         "current_cents": sum(r["value_cents"] or 0 for r in rows), "available_cents": None,
                         "limit_cents": None, "currency": "USD",
                         "updated_at": max((r["updated_at"] for r in rows if r["updated_at"]), default=None)})
    return accounts, holdings


def gains(holdings):
    """What the holdings with a known cost (cost basis: what was paid for them) are worth against that cost."""
    known = [x for x in holdings if x.get("cost_basis_cents") is not None and x.get("value_cents") is not None]
    value = sum(x["value_cents"] for x in known)
    basis = sum(x["cost_basis_cents"] for x in known)
    return {"value_cents": value, "cost_basis_cents": basis, "gain_cents": value - basis,
            "pct": _pct(value - basis, basis), "count": len(known)}


PAYDAYS_A_YEAR = {"weekly": 52, "biweekly": 26, "semimonthly": 24, "monthly": 12}


def spent_vs_pay(txns, income, today):
    """This calendar month's spending so far against an average month's take-home pay (pay × paydays a year ÷ 12)."""
    if not income:
        return None
    take_home = round(to_cents(income["amount"]) * PAYDAYS_A_YEAR[income["schedule"]] / 12)
    spent = spending_total([x for x in txns if _in(x, today.replace(day=1), today)])
    return {"take_home_cents": take_home, "spent_cents": spent, "left_cents": take_home - spent,
            "pct": _pct(spent, take_home)}


USUAL_PAYDAYS = {"weekly": 4, "biweekly": 2}  # a month with more has an extra paycheck


def extra_paycheck_months(income, today, months=12):
    """Months from this one on, over the next `months`, that hold an extra paycheck (3 biweekly or 5 weekly paydays)."""
    usual = USUAL_PAYDAYS.get(income["schedule"]) if income else None
    if not usual:
        return []
    first = today.replace(day=1)
    end = _add_months(first, months, 1)
    by_month, d = defaultdict(list), first - timedelta(days=1)
    anchored = _anchored_by(income, d)
    while (d := next_payday(anchored, d)) < end:
        by_month[f"{d:%Y-%m}"].append(_iso(d))
    return [{"month": m, "paydays": days, "extra_cents": to_cents(income["amount"]) * (len(days) - usual)}
            for m, days in sorted(by_month.items()) if len(days) > usual]


def yearly_contributions(doc, income):
    """What the hand-entered holdings buy in a year from each paycheck (TSP per_paycheck), in cents."""
    per_paycheck = sum(to_cents(h.get("per_paycheck") or 0) for a in doc.get("accounts") or [] for h in a["holdings"])
    return per_paycheck * PAYDAYS_A_YEAR[income["schedule"]] if income else 0


def price_symbol(holding):
    """The symbol its prices are stored under: a hand-entered holding's own symbol (TSP:C), else its ticker."""
    sid = holding.get("security_id") or ""
    return sid[len("manual:"):] if sid.startswith("manual:") else holding.get("ticker")


def portfolio_history(holdings, prices, start, end):
    """What the shares held now were worth on each price date in [start, end], ending on `end` (today) at today's value.

    Quantities stay as they are today, so this shows how the holdings moved, not deposits. Cash and holdings with
    no stored prices count at today's value on every day. The line starts once every priced holding has a price.
    """
    priced, flat, unpriced = [], 0, []
    for x in holdings:
        series = prices.get(price_symbol(x)) or []
        if bucket_of(x) != "cash" and series and x.get("quantity") is not None:
            priced.append((Decimal(str(x["quantity"])), series))
        else:
            flat += x.get("value_cents") or 0
            if bucket_of(x) != "cash":
                unpriced.append(x.get("name") or x.get("ticker"))
    first = max([_iso(start)] + [series[0][0] for _, series in priced])
    days = sorted({d for _, series in priced for d, _ in series if first <= d <= _iso(end)})
    points = [{"date": d, "total_cents": flat + sum(to_cents(q * _price(price_on(series, date.fromisoformat(d))))
                                                     for q, series in priced)} for d in days]
    if points and points[-1]["date"] < _iso(end):
        points.append({"date": _iso(end), "total_cents": sum(x.get("value_cents") or 0 for x in holdings)})
    return {"points": points, "flat_cents": flat, "unpriced": unpriced}


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
    one name (Apple.com/bill) stay separate. It takes repeat charges: the RECURRING a bank prints in a descriptor only
    means the card is on file with the merchant, and one-off Apple purchases carry it too. Two charges make a series
    only when they are all the merchant has: among a restaurant's many charges, two similar amounts a week apart are
    chance. A variable series needs three.
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
        series = []
        for c in clusters:
            enough = len(c) >= 3 or (len(c) == 2 and len(rows) == 2)
            if enough and (cadence := _cadence([date.fromisoformat(x["date"]) for x in c])):
                series.append((c, cadence))
        taken = {x["transaction_id"] for c, _ in series for x in c}
        fixed = []
        for c, cadence in series:
            # A charge on the next due date at a new price is the same bill after a price change.
            for x in rows:
                if x["transaction_id"] not in taken and x["date"] > c[-1]["date"] and \
                        _days_apart(_iso(next_after(date.fromisoformat(c[-1]["date"]), cadence)), x["date"]) <= 5:
                    c = [*c, x]
                    taken.add(x["transaction_id"])
            fixed.append(_series(c, cadence, True))
        found += fixed
        if not fixed and len(rows) >= 3 and (cadence := _cadence([date.fromisoformat(x["date"]) for x in rows])):
            found.append(_series(rows, cadence, False))
    # A series is over once a whole extra cycle passes with no charge.
    grace = {name: hi for name, _, hi in CADENCES}
    active = [s for s in found if date.fromisoformat(s["next_due"]) + timedelta(days=grace[s["cadence"]]) >= today]
    return sorted(active, key=lambda s: (s["next_due"], s["merchant"]))


# --- unusual charges

def _days_apart(a, b):
    return abs((date.fromisoformat(a) - date.fromisoformat(b)).days)


def _flag(kind, x, detail, previous=None):
    return {"kind": kind, "transaction_id": x["transaction_id"], "merchant": merchant_of(x),
            "amount_cents": x["amount_cents"], "date": x["date"], "authorized_date": x.get("authorized_date"),
            "account_key": x.get("account_key"),
            "previous_cents": previous, "detail": detail}


def dollars(c):
    return f"${c / 100:,.2f}"


def unusual_charges(txns, today, plan_bills=(), days=14):
    """Charges in the last `days` worth a look: possible duplicates, recurring charges that went up, big one-offs.

    A plan bill marked fixed: false is never a price change, only higher than usual.
    """
    varies = {_name_key(b["name"]).strip(".,*#") for b in plan_bills if b.get("fixed") is False}
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
            if s["fixed"] and key not in varies and abs(x["amount_cents"] - s["amount_cents"]) >= s["amount_cents"] / 100:
                found.append(_flag("price_change", x, f"Was {dollars(s['amount_cents'])}, now "
                                                      f"{dollars(x['amount_cents'])}", s["amount_cents"]))
            elif (not s["fixed"] or key in varies) and x["amount_cents"] > 1.5 * s["median_cents"]:
                found.append(_flag("higher_than_usual", x, f"Usually about {dollars(s['median_cents'])}",
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
    return abs(series["amount_cents"] - to_cents(bill["amount"])) <= to_cents(bill["amount"]) / 10


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
            rows.append({"name": b["name"], "due": _iso(d), "amount_cents": to_cents(b["amount"]), "source": "plan",
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
    pay = to_cents(plan["income"]["amount"]) if plan.get("income") else 0

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


def month_events(plan, recurring, txns, start, end, today):
    """Paydays and bills in [start, end), past and future, for a month view.

    Plan bills are paid when a matching payment shows up; detected charges are paid on the dates they were charged
    and projected forward from today by their cadence.
    """
    events = []
    income = plan.get("income")
    if income:
        anchored = _anchored_by(income, start)
        d = next_payday(anchored, start - timedelta(days=1))
        while d < end:
            events.append({"date": _iso(d), "kind": "payday", "name": "Payday",
                           "amount_cents": to_cents(income["amount"]), "paid": False})
            d = next_payday(anchored, d)
    declared = plan.get("bills") or []
    for b in declared:
        for d in bill_dates(b, start, end):
            events.append({"date": _iso(d), "kind": "bill", "name": b["name"], "amount_cents": to_cents(b["amount"]),
                           "paid": bill_paid(b, d, txns)})
    for s in recurring:
        if any(_matches(b, s) for b in declared):
            continue
        amount = s["amount_cents"] if s["fixed"] else s["median_cents"]
        events += [{"date": x, "kind": "bill", "name": s["merchant"], "amount_cents": amount, "paid": True}
                   for x in s["dates"] if _iso(start) <= x < _iso(end)]
        d = date.fromisoformat(s["next_due"])
        while d < end:
            if d >= max(today, start):
                events.append({"date": _iso(d), "kind": "bill", "name": s["merchant"], "amount_cents": amount,
                               "paid": False})
            d = next_after(d, s["cadence"])
    return sorted(events, key=lambda e: (e["date"], e["kind"], e["name"]))


# --- tips

PER_MONTH = {"weekly": 52 / 12, "biweekly": 26 / 12, "monthly": 1}
UTILIZATION_TIP_PCT = 30
RUNWAY_TIP_MONTHS = 3


def _whole(c):
    return f"${round(c / 100):,}"


def tips(accounts, holdings, plan, txns, recurring, typical_monthly_cents, today):
    """Short, specific nudges, most pressing first: budgets on pace to overrun, cards over 30% used, thin cash,
    and repeat charges the plan doesn't set aside."""
    out = []
    start, days_in = today.replace(day=1), calendar.monthrange(today.year, today.month)[1]
    for category, target in (plan.get("monthly_spend") or {}).items():
        spent = sum(x["amount_cents"] for x in txns if x["category"] == category and _in(x, start, today)
                    and is_spending(x))
        target, pace = to_cents(target), round(spent / today.day * days_in)
        # a week in, the month's pace means something; before that only an overrun does
        if spent > target or (pace > target and today.day > 7):
            left_days = days_in - today.day
            rest = (f"{dollars(round((target - spent) / left_days))} a day for the rest of {today:%B} keeps it on target."
                    if left_days and spent < target else "It's over already.")
            out.append({"kind": "pace", "title": f"{category.replace('_', ' ').capitalize()} is on pace for "
                                                 f"{_whole(pace)} this month",
                        "detail": f"Your target is {_whole(target)} and {_whole(spent)} is spent. {rest}"})
    for x in accounts:
        if x.get("type") == "credit" and x.get("limit_cents"):
            pct = round(100 * card_owed(x) / x["limit_cents"])
            if pct > UTILIZATION_TIP_PCT:
                out.append({"kind": "card", "title": f"Card …{x['mask']} is {pct}% used",
                            "detail": f"Paying it under {UTILIZATION_TIP_PCT}% "
                                      f"({_whole(x['limit_cents'] * UTILIZATION_TIP_PCT / 100)}) before the "
                                      "statement closes helps your credit score."})
    r = runway(accounts, holdings, typical_monthly_cents, plan)
    if r["months"] is not None and r["months"] < RUNWAY_TIP_MONTHS:
        against = f"your planned {_whole(r['monthly_cents'])} month of bills and spending targets" \
            if r["basis"] == "plan" else f"a typical {_whole(r['monthly_cents'])} month"
        out.append({"kind": "runway", "title": f"Cash covers {r['months']:g} months of spending",
                    "detail": f"Checking and brokerage cash, less the card, against {against}. "
                              "Three months is a common cushion."})
    declared = plan.get("bills") or []
    per_month = {x["key"]: (x["amount_cents"] if x["fixed"] else x["median_cents"]) * PER_MONTH[x["cadence"]]
                 for x in recurring if not any(_matches(b, x) for b in declared)}
    extra = sorted((x for x in recurring if x["key"] in per_month), key=lambda x: -per_month[x["key"]])
    if extra:
        monthly = sum(per_month.values())
        out.append({"kind": "subscriptions",
                    "title": f"{len(extra)} repeat charge{'s' * (len(extra) > 1)} outside your plan: "
                             f"{dollars(round(monthly))} a month",
                    "detail": f"{', '.join(x['merchant'] for x in extra)}. Add the ones you keep to the plan so "
                              "safe-to-spend sets them aside."})
    return out


# --- investing: left to invest

RESERVE_MONTHS = (0, 1, 3)


def left_to_invest(accounts, holdings, plan, upcoming, typical_monthly_cents, today):
    """Idle brokerage cash plus whatever checking holds beyond card balances, upcoming bills, the buffer and goal savings.

    Options show how much remains if some months of typical spending stay in cash. The owner decides.
    """
    checking = {x["account_key"] for x in accounts if is_checking(x)}
    bills = sum(b["amount_cents"] for b in upcoming["bills"] if b.get("account_key") in checking and not b["paid"])
    cards = cards_owed_cents(accounts)
    buffer = to_cents(plan.get("buffer") or 0)
    goals_cents = goals_set_aside_cents(plan, today)
    checking_left = max(0, checking_cents(accounts) - cards - bills - buffer - goals_cents)
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
                  *_goals_line(goals_cents),
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
        seen = _when(item.get("consent_since") or item["first_seen_at"]).date()
        expires, estimated = _add_months(seen, config.CONSENT_MONTHS[item["institution_id"]], seen.day), True
    else:
        return None
    return {"expires": _iso(expires), "days_left": (expires - today).days, "estimated": estimated}


def month_name(iso):
    d = date.fromisoformat(iso[:10])
    return f"{d:%b %Y}"


def _day(iso):
    d = date.fromisoformat(iso[:10])
    return f"{d:%a, %b} {d.day}"


def _alert(level, kind, occurrence, title, detail):
    """`occurrence` names this happening of the problem, so dismissing it never hides the next one.

    It's hashed because the id goes to the browser and an occurrence can hold a Plaid item id.
    """
    digest = hashlib.sha256(":".join(str(x) for x in occurrence).encode()).hexdigest()[:16]
    return {"id": f"{kind}:{digest}", "level": level, "kind": kind, "title": title, "detail": detail}


REMINDERS_AHEAD = 3


def reminders_ahead(reminders, today):
    """The next few plan reminders after today, soonest first; from their date they move to the attention list."""
    return sorted((r for r in reminders if r["date"] > today.isoformat()), key=lambda r: r["date"])[:REMINDERS_AHEAD]


def alerts(items, upcoming, unusual, now, reminders=()):
    """The attention list at the top of Today, most urgent first.

    A bank problem is one occurrence until a sync succeeds; a warning that turns urgent is a new one. A flagged charge
    goes by its authorized date, because posting gives a pending charge a new transaction id and often a new date; if
    posting changes its amount or merchant name, it shows once more. Identical charges are numbered, so each is
    dismissed on its own. A plan reminder shows from its date until dismissed, oldest first.
    """
    found = []
    today = _iso(_when(now).date())
    for r in sorted((r for r in reminders if r["date"] <= today), key=lambda r: r["date"]):
        found.append(_alert("warn", "reminder", (r["date"], r["title"]), r["title"],
                            r.get("detail") or f"From your plan, set for {_day(r['date'])}."))
    for it in items:
        name = it["institution"]
        since = (it["item_id"], it.get("last_synced_at"))
        if it["status"] == "login_required":
            found.append(_alert("urgent", "login_required", since, f"{name} needs you to sign in again",
                                "On the Money screen, under Connections, tap Fix login. Data stops updating until then."))
        elif it["status"] == "error":
            found.append(_alert("warn", "sync_error", since, f"{name} sync failed",
                                f"{it.get('error_code') or 'Unknown error'}. The next sync will retry."))
        elif it["status"] == "not_ready":
            found.append(_alert("info", "not_ready", since, f"{name} is still preparing data",
                                "Plaid is gathering history. The app retries every few minutes."))
        c = consent(it, now)
        if c and c["days_left"] <= 30:
            level = "urgent" if c["days_left"] <= 7 else "warn"
            found.append(_alert(level, "consent_expiring", (it["item_id"], c["expires"], level),
                                f"{name} access ends in {c['days_left']} days",
                                f"{'About ' if c['estimated'] else ''}{_day(c['expires'])}. Tap Fix login under "
                                "Connections on the Money screen before then to keep data flowing."))
    for b in upcoming["bills"]:
        if b.get("covered") is False:
            found.append(_alert("urgent", "uncovered_bill", (b["name"], b["due"], b["amount_cents"]),
                                f"Checking may not cover {b['name']}",
                                f"{dollars(b['amount_cents'])} due {_day(b['due'])}."))
    seen = Counter()
    for u in unusual:
        charge = (u["kind"], u["merchant"], u["amount_cents"], u.get("authorized_date") or u["date"])
        seen[charge] += 1
        found.append(_alert("warn", "unusual_charge", (*charge, seen[charge]),
                            f"{u['merchant']} {dollars(u['amount_cents'])} on {_day(u['date'])}", u["detail"]))
    stale_after = timedelta(hours=config.STALE_AFTER_HOURS)
    for it in items:
        last = it.get("last_synced_at")
        age = _when(now) - _when(last) if last else None
        if it["status"] == "ok" and (age is None or age > stale_after):
            ago = f"{int(age.total_seconds() // 3600)} hours ago" if age else "never"
            found.append(_alert("warn", "stale_sync", (it["item_id"], last), f"{it['institution']} last synced {ago}",
                                "Sync runs every 6 hours while the container is up. Tap Sync now to retry."))
    return sorted(found, key=lambda x: LEVELS[x["level"]])

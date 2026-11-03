"""What each screen shows, and the Ask Claude snapshot. The API, the UI and the snapshot all read through here.

`gather` loads everything once per request; each screen function picks from it. All amounts are integer cents.
"""
from datetime import datetime, timedelta

from . import money, prices, store

PERIODS = ("today", "week", "pay_period", "last_week", "month", "last_month")
ACCOUNT_ORDER = {"depository": 0, "credit": 1, "loan": 2, "investment": 3}


def gather(conn, plan, now, manual):
    today = now.astimezone().date()
    price_series = store.prices(conn)
    entered_accounts, entered_holdings = money.manual_holdings(manual, price_series, plan.get("income"), today)
    accounts = sorted(store.accounts(conn) + entered_accounts,
                      key=lambda a: (ACCOUNT_ORDER.get(a["type"], 9), not money.is_checking(a)))
    txns = store.transactions(conn)
    holdings = sorted(store.holdings(conn) + entered_holdings, key=lambda h: -(h["value_cents"] or 0))
    recurring = money.detect_recurring(txns, today)
    upcoming = money.upcoming_bills(accounts, plan, recurring, txns, today)
    unusual = money.unusual_charges(txns, today, plan.get("bills") or [])
    return {
        "now": now.astimezone(),
        "today": today,
        "plan": plan,
        "accounts": accounts,
        "txns": txns,
        "holdings": holdings,
        "items": store.items(conn),
        "dismissed_alerts": store.dismissed_alerts(conn),
        "recurring": recurring,
        "upcoming": upcoming,
        "unusual": unusual,
        "typical_monthly_cents": money.typical_monthly_spend(txns, today),
        "prices": price_series,
        "manual": manual,
    }


def category_label(category):
    return (category or "OTHER").replace("_", " ").capitalize()


def _last_synced(ctx):
    times = [it["last_synced_at"] for it in ctx["items"] if it.get("last_synced_at")]
    return min(times) if times else None


def status(ctx):
    return {
        "now": ctx["now"].isoformat(timespec="seconds"),
        "last_synced_at": _last_synced(ctx),
        "items": [{"institution": it["institution"], "products": it["products"], "status": it["status"],
                   "error_code": it["error_code"], "last_synced_at": it["last_synced_at"],
                   "last_attempt_at": it["last_attempt_at"], "consent": money.consent(it, ctx["now"].isoformat())}
                  for it in ctx["items"]],
    }


def alerts(ctx):
    """The attention list: what's live and not dismissed, plus how many live ones are hidden."""
    live = money.alerts(ctx["items"], ctx["upcoming"], ctx["unusual"], ctx["now"].isoformat())
    shown = [a for a in live if a["id"] not in ctx["dismissed_alerts"]]
    return {"alerts": shown, "dismissed_count": len(live) - len(shown)}


RECENT = 6


def today(ctx):
    income = ctx["plan"].get("income")
    spent = [{k: s[k] for k in ("period", "start", "end", "total_cents")}
             for s in (money.spend_summary(ctx["txns"], p, ctx["today"], income)
                       for p in ("today", "week", "pay_period" if income else "month"))]
    attention = alerts(ctx)
    return {
        "date": ctx["today"].isoformat(),
        "safe_to_spend": money.safe_to_spend(ctx["accounts"], ctx["plan"], ctx["txns"], ctx["today"]),
        "alerts": attention["alerts"],
        "alerts_dismissed": attention["dismissed_count"],
        "spent_today_cents": spent[0]["total_cents"],
        "spent": spent,
        "recent": _mark_spending(ctx["txns"][:RECENT]),
        "next_bills": [b for b in ctx["upcoming"]["bills"] if not b["paid"]][:3],
        "tips": money.tips(ctx["accounts"], ctx["holdings"], ctx["plan"], ctx["txns"], ctx["recurring"],
                           ctx["typical_monthly_cents"], ctx["today"]),
        "last_synced_at": _last_synced(ctx),
    }


def _cumulative(txns, start, end):
    """Running spending total for each day in [start, end]."""
    by_day = {}
    for t in txns:
        if money.is_spending(t):
            by_day[t["date"]] = by_day.get(t["date"], 0) + t["amount_cents"]
    out, total, d = [], 0, start
    while d <= end:
        total += by_day.get(d.isoformat(), 0)
        out.append({"date": d.isoformat(), "total_cents": total})
        d += timedelta(days=1)
    return out


def spending(ctx, period="month", start=None, end=None):
    income = ctx["plan"].get("income")
    s = money.spend_summary(ctx["txns"], period, ctx["today"], income, start, end)
    start, end, pstart, pend = money.period_range(period, ctx["today"], income, start, end)
    for c in s["by_category"]:
        c["label"] = category_label(c["category"])
    targets = ctx["plan"].get("monthly_spend") or {}
    return {
        **s,
        "payday": _payday(ctx),
        "trend": {"current": _cumulative(s["transactions"], start, end),
                  "previous": _cumulative(ctx["txns"], pstart, pend)},
        "targets_cents": {k: money.to_cents(v) for k, v in targets.items()},
        "unusual": ctx["unusual"],
        "last_synced_at": _last_synced(ctx),
    }


def bills(ctx):
    first = ctx["today"].replace(day=1)
    return {
        **ctx["upcoming"],
        # this month and next, for the calendar
        "calendar": money.month_events(ctx["plan"], ctx["recurring"], ctx["txns"], first,
                                       (first + timedelta(days=62)).replace(day=1), ctx["today"]),
        "recurring": [{k: v for k, v in r.items() if k != "dates"} for r in ctx["recurring"]],
        "price_flags": [u for u in ctx["unusual"] if u["kind"] in ("price_change", "higher_than_usual")],
        "declared": ctx["plan"].get("bills") or [],
        "last_synced_at": _last_synced(ctx),
    }


def accounts(ctx):
    accts = [{**a, "owed_cents": money.card_owed(a) if a["type"] == "credit" else None} for a in ctx["accounts"]]
    return {
        "accounts": accts,
        "net_worth": money.net_worth(ctx["accounts"]),
        "cards_owed_cents": money.cards_owed_cents(ctx["accounts"]),
        "runway": money.runway(ctx["accounts"], ctx["holdings"], ctx["typical_monthly_cents"]),
        "last_synced_at": _last_synced(ctx),
    }


def holdings(ctx):
    return {
        "holdings": [{**h, "bucket": money.bucket_of(h)} for h in ctx["holdings"]],
        "allocation": money.allocation(ctx["holdings"]),
        "left_to_invest": money.left_to_invest(ctx["accounts"], ctx["holdings"], ctx["plan"], ctx["upcoming"],
                                               ctx["typical_monthly_cents"]),
        "history": money.portfolio_history(ctx["holdings"], ctx["prices"],
                                           ctx["today"] - timedelta(days=prices.HISTORY_DAYS), ctx["today"]),
        "contributions_per_year_cents": money.yearly_contributions(ctx["manual"], ctx["plan"].get("income")),
        "last_synced_at": _last_synced(ctx),
    }


def transactions(ctx, start=None, end=None, account=None, q=None, category=None, limit=200, period=None,
                 merchant=None):
    """Transactions matching every filter, newest first, and the spending among them. A named period sets
    start and end. merchant matches exactly, as Spending's top merchants group; q matches any part of a name."""
    income = ctx["plan"].get("income")
    if period:
        first, last, _, _ = money.period_range(period, ctx["today"], income)
        start, end = first.isoformat(), last.isoformat()
    rows = ctx["txns"]
    if start:
        rows = [t for t in rows if t["date"] >= start]
    if end:
        rows = [t for t in rows if t["date"] <= end]
    if account:
        rows = [t for t in rows if account in (t["account_key"], t["account_mask"])]
    if category:
        rows = [t for t in rows if (t["category"] or "OTHER") == category]  # the same key as by_category
    if merchant:
        rows = [t for t in rows if money.merchant_of(t) == merchant]
    if q:
        q = q.lower()
        rows = [t for t in rows if q in (t["name"] or "").lower() or q in (t["merchant_name"] or "").lower()]
    return {"start": start, "end": end, "transactions": _mark_spending(rows[:limit]), "count": len(rows),
            "spent_cents": money.spending_total(rows), "last_synced_at": _last_synced(ctx), "payday": _payday(ctx)}


def _payday(ctx):
    """The next payday, or None without a pay schedule: a screen offers the pay period only when there is one."""
    income = ctx["plan"].get("income")
    return money.next_payday(income, ctx["today"]).isoformat() if income else None


def _mark_spending(rows):
    """Rows marked with whether they count as spending, so a list shows which ones make up its total."""
    return [{**t, "spending": money.is_spending(t)} for t in rows]


# --- Ask Claude snapshot

def _d(cents):
    return money.dollars(cents) if cents is not None else "n/a"


def _local(ts):
    return datetime.fromisoformat(ts).astimezone().strftime("%Y-%m-%d %H:%M") if ts else "never"


def _account_line(a):
    mask = f" …{a['mask']}" if a["mask"] else ""
    label = f"{a['name']}{mask} ({a['subtype'] or a['type']})"
    if a["type"] == "credit":
        return f"- {label}: owed {_d(money.card_owed(a))} incl. pending" + \
            (f", limit {_d(a['limit_cents'])}" if a.get("limit_cents") else "")
    if a.get("available_cents") is not None and a["available_cents"] != a["current_cents"]:
        return f"- {label}: {_d(a['available_cents'])} available, {_d(a['current_cents'])} current"
    return f"- {label}: {_d(a['current_cents'])}"


def _period_line(ctx, period, title):
    s = money.spend_summary(ctx["txns"], period, ctx["today"], ctx["plan"].get("income"))
    cats = ", ".join(f"{category_label(c['category'])} {_d(c['total_cents'])}" for c in s["by_category"][:6])
    return f"- {title} ({s['start']} to {s['end']}): {_d(s['total_cents'])}" + (f". {cats}" if cats else "")


def _txn_line(t):
    return (f"{t['date']} {money.merchant_of(t)} {_d(t['amount_cents'])} {category_label(t['category'])}"
            f" …{t['account_mask']}{' (pending)' if t['pending'] else ''}")


def _screen_lines(ctx, screen):
    if screen == "today":
        s = money.safe_to_spend(ctx["accounts"], ctx["plan"], ctx["txns"], ctx["today"])
        if s["status"] != "ok":
            return [f"Safe to spend: needs {' and '.join(s['missing'])} in the plan."]
        return [f"Safe to spend: {_d(s['per_day_cents'])}/day until payday {s['payday']} ({s['days']} days)"] + \
            [f"  {line['label']}: {_d(line['amount_cents'])}" for line in s["lines"]] + \
            [f"  = {_d(s['available_cents'])} ÷ {s['days']} days",
             f"Today's allowance {_d(s['allowance_cents'])}: spent {_d(s['spent_today_cents'])}, "
             f"{_d(s['left_today_cents'])} left"]
    if screen in ("spending",) + PERIODS:
        income = ctx["plan"].get("income")
        period = screen if screen in PERIODS and (income or screen != "pay_period") else "month"
        s = money.spend_summary(ctx["txns"], period, ctx["today"], income)
        return [f"Spending transactions {s['start']} to {s['end']} (amount, + is money out):"] + \
            [_txn_line(t) for t in s["transactions"][:80]]
    if screen == "bills":
        return ["Bills, next 35 days:"] + [
            f"{b['due']} {b['name']} {_d(b['amount_cents'])}{' (estimated)' if b['estimated'] else ''}"
            f" [{b['source']}]{' paid' if b['paid'] else ''}"
            f"{'' if b['covered'] is None else ' covered' if b['covered'] else ' NOT covered by checking'}"
            for b in ctx["upcoming"]["bills"]]
    if screen == "money":
        r = money.runway(ctx["accounts"], ctx["holdings"], ctx["typical_monthly_cents"])
        return [f"Cash on hand {_d(r['cash_cents'])}, typical monthly spend {_d(r['typical_monthly_cents'])}, "
                f"runway {r['months']} months"]
    if screen == "investing":
        li = money.left_to_invest(ctx["accounts"], ctx["holdings"], ctx["plan"], ctx["upcoming"],
                                  ctx["typical_monthly_cents"])
        return ["Left to invest:"] + [f"  {line['label']}: {_d(line['amount_cents'])}" for line in li["lines"]] + \
            [f"  Option: {o['label']}: {_d(o['amount_cents'])}" for o in li["options"]]
    return []


def _plan_lines(plan):
    inc = plan.get("income")
    out = [f"- Income: {_d(money.to_cents(inc['amount']))} {inc['schedule']}, next payday {inc['next_payday']}"
           if inc else "- Income: not set"]
    out.append("- Bills: " + ("; ".join(f"{b['name']} {_d(money.to_cents(b['amount']))} on day {b['due_day']}"
                                        for b in plan.get("bills") or []) or "none listed"))
    out.append(f"- Buffer: {_d(money.to_cents(plan['buffer'])) if plan.get('buffer') is not None else 'not set'}")
    if plan.get("monthly_spend"):
        out.append("- Monthly spend targets: " + ", ".join(f"{k} {_d(money.to_cents(v))}"
                                                           for k, v in plan["monthly_spend"].items()))
    for g in plan.get("goals") or []:
        out.append(f"- Goal: {g['name']} {_d(money.to_cents(g['target']))}" + (f" by {g['by']}" if g.get("by") else ""))
    return out


def snapshot(ctx, screen=None):
    """Compact plain-text snapshot for pasting into Claude. One function for the app button and /api/snapshot."""
    nw = money.net_worth(ctx["accounts"])
    alloc = money.allocation(ctx["holdings"])
    lines = [
        f"My finances as of {ctx['now']:%Y-%m-%d %H:%M} (last synced {_local(_last_synced(ctx))}). USD.",
        "Conventions: amounts positive = money out. Spending excludes transfers between my accounts, income and "
        "credit-card payments; refunds net in; pending counts.",
        "",
        "Accounts",
        *[_account_line(a) for a in ctx["accounts"]],
        f"- Net worth {_d(nw['net_cents'])} (assets {_d(nw['assets_cents'])} − card owed {_d(nw['liabilities_cents'])})",
        "",
        "Spending this month and recent periods",
        _period_line(ctx, "month", "Spending this month"),
        _period_line(ctx, "last_month", "Last month"),
        _period_line(ctx, "week", "This week"),
        _period_line(ctx, "last_week", "Last week"),
        *([_period_line(ctx, "pay_period", "Since payday")] if ctx["plan"].get("income") else []),
        f"- Typical month (last complete months): {_d(ctx['typical_monthly_cents'])}",
        "",
        "Upcoming bills (35 days)",
        *([f"- {b['due']} {b['name']} {_d(b['amount_cents'])}{' est.' if b['estimated'] else ''}"
           f"{' paid' if b['paid'] else ''}{' NOT covered' if b['covered'] is False else ''}"
           for b in ctx["upcoming"]["bills"][:12]] or ["- none found"]),
        "Recurring charges detected: " + (", ".join(
            f"{r['merchant']} {_d(r['amount_cents'])} {r['cadence']}" for r in ctx["recurring"][:12]) or "none"),
    ]
    if ctx["unusual"]:
        lines.append("Unusual charges: " + "; ".join(f"{u['date']} {u['merchant']} {_d(u['amount_cents'])} "
                                                     f"({u['detail']})" for u in ctx["unusual"][:6]))
    lines += [
        "",
        "Holdings",
        f"- Total {_d(alloc['total_cents'])}: " + ", ".join(f"{b['bucket']} {b['pct']}%" for b in alloc["buckets"]),
        *[f"- {h.get('ticker') or h.get('name')}: {_d(h['value_cents'])}" for h in ctx["holdings"][:12]],
        "",
        "Plan",
        *_plan_lines(ctx["plan"]),
    ]
    attention = alerts(ctx)
    if attention["alerts"] or attention["dismissed_count"]:
        lines += ["", "Needs attention"] + [f"- {a['title']}: {a['detail']}" for a in attention["alerts"]]
        if attention["dismissed_count"]:
            lines.append(f"- {attention['dismissed_count']} more dismissed by the owner")
    extra = _screen_lines(ctx, screen)
    if extra:
        lines += ["", f"This screen ({screen})", *extra]
    lines += ["", "My question:", ""]
    return "\n".join(lines)

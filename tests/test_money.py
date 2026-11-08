from datetime import date

import pytest

from finance import money


def t(amount_cents, day, category="FOOD_AND_DRINK", merchant="Cafe", detailed=None, pending=False,
      account_key="bofa:1111", name=None, tid=None, authorized=None):
    return {"transaction_id": tid or f"{merchant}-{day}-{amount_cents}", "amount_cents": amount_cents, "date": day,
            "authorized_date": authorized,
            "merchant_name": merchant, "name": name or merchant, "category": category,
            "category_detailed": detailed or f"{category}_OTHER", "pending": pending, "account_key": account_key}


# --- spending: sign convention and what counts


def test_spending_counts_outflows_nets_refunds_and_skips_transfers_income_and_card_payments():
    txns = [
        t(1000, "2026-10-28"),                                          # coffee, money out
        t(-300, "2026-10-28", "GENERAL_MERCHANDISE", "Amazon"),          # refund nets against spend
        t(2500, "2026-10-28", "GENERAL_MERCHANDISE", "Amazon"),
        t(800, "2026-10-28", pending=True),                             # pending still counts
        t(-200000, "2026-10-28", "INCOME", "Employer"),
        t(50000, "2026-10-28", "TRANSFER_OUT", "Merrill", "TRANSFER_OUT_ACCOUNT_TRANSFER"),
        t(30000, "2026-10-28", "LOAN_PAYMENTS", "BoA card", "LOAN_PAYMENTS_CREDIT_CARD_PAYMENT"),
        t(-30000, "2026-10-28", "TRANSFER_IN", "BoA card", account_key="bofa:3333"),
        t(41000, "2026-10-28", "LOAN_PAYMENTS", "AutoFinance", "LOAN_PAYMENTS_CAR_PAYMENT"),  # a real outflow
        t(-5000, "2026-10-28", "OTHER", "Unknown deposit"),              # unknown inflow is not a refund
    ]
    assert money.spending_total(txns) == 1000 - 300 + 2500 + 800 + 41000


def test_moving_money_to_own_savings_or_investments_is_not_spending_but_paying_people_is():
    txns = [
        t(20000, "2026-10-28", "TRANSFER_OUT", "Merrill", "TRANSFER_OUT_INVESTMENT_AND_RETIREMENT_FUNDS"),
        t(10000, "2026-10-28", "TRANSFER_OUT", "BoA", "TRANSFER_OUT_SAVINGS"),
        t(4500, "2026-10-28", "TRANSFER_OUT", "Venmo", "TRANSFER_OUT_TRANSFER_OUT_FROM_APPS"),  # paying a friend
    ]
    assert money.spending_total(txns) == 4500


def test_spend_summary_month_to_date_compares_with_same_days_of_last_month():
    txns = [t(1000, "2026-10-01"), t(2000, "2026-10-15", "GROCERIES", "Aldi"),
            t(500, "2026-09-10"), t(9999, "2026-09-20"),  # Sep 20 is past "same day last month"
            t(700, "2026-10-16")]  # after today
    s = money.spend_summary(txns, "month", date(2026, 10, 15))

    assert (s["start"], s["end"]) == ("2026-10-01", "2026-10-15")
    assert s["total_cents"] == 3000
    assert (s["previous"]["start"], s["previous"]["end"], s["previous"]["total_cents"]) == ("2026-09-01", "2026-09-15", 500)
    assert s["change_cents"] == 2500
    assert s["by_category"][0] == {"category": "GROCERIES", "total_cents": 2000, "count": 1}
    assert [m["merchant"] for m in s["by_merchant"]] == ["Aldi", "Cafe"]


def test_spend_summary_week_runs_monday_to_today_and_last_week_is_the_full_previous_week():
    txns = [t(100, "2026-10-26"), t(200, "2026-10-29"), t(400, "2026-10-19"), t(800, "2026-10-25")]
    week = money.spend_summary(txns, "week", date(2026, 10, 29))  # a Thursday
    last = money.spend_summary(txns, "last_week", date(2026, 10, 29))

    assert (week["start"], week["end"], week["total_cents"]) == ("2026-10-26", "2026-10-29", 300)
    assert (week["previous"]["start"], week["previous"]["end"]) == ("2026-10-19", "2026-10-22")
    assert (last["start"], last["end"], last["total_cents"]) == ("2026-10-19", "2026-10-25", 1200)


# --- balances


def a(mask, type_="depository", subtype="checking", current=0, available=None, limit=None, name=None):
    return {"account_key": f"bofa:{mask}", "mask": mask, "type": type_, "subtype": subtype, "name": name or mask,
            "current_cents": current, "available_cents": available, "limit_cents": limit}


CHECKING = a("1111", current=150000, available=140000)          # $14 pending debit not yet in current
CARD = a("3333", "credit", "credit card", current=30000, available=165000, limit=200000)  # $50 pending charge
CMA = a("2222", "depository", "cash management", current=1000000, name="CMA-Edge")


def test_owed_is_set_for_cards_and_loans_and_empty_for_everything_else():
    assert money.owed(CARD) == 35000
    assert money.owed(a("2", "loan", "loan", current=383262)) == 383262
    assert money.owed(a("3", "depository", "checking", current=150000)) is None


def test_card_owed_includes_pending_charges_seen_through_available_credit():
    assert money.card_owed(CARD) == 35000
    assert money.card_owed(a("1", "credit", "credit card", current=30000)) == 30000  # no limit data: current only


def test_net_worth_is_assets_minus_card_owed():
    nw = money.net_worth([CHECKING, CARD, CMA])
    assert nw["assets_cents"] == 150000 + 1000000
    assert nw["liabilities_cents"] == 35000
    assert nw["net_cents"] == 150000 + 1000000 - 35000


def h(ticker, type_, value, cash=False, name=None, account_key="bofa:2222"):
    return {"ticker": ticker, "type": type_, "value_cents": value, "is_cash_equivalent": cash,
            "name": name or ticker, "account_key": account_key}


HOLDINGS = [h("TTTXX", "mutual fund", 200000, cash=True), h("Deposit", "other", 100000, name="Ml Direct Deposit Progrm"),
            h("VTI", "etf", 400000), h("VXUS", "etf", 100000), h("NVDA", "equity", 150000), h("AAPL", "equity", 50000)]


def test_allocation_buckets_cash_like_etfs_and_single_stocks_with_concentration():
    al = money.allocation(HOLDINGS)
    buckets = {b["bucket"]: b["value_cents"] for b in al["buckets"]}
    assert buckets == {"cash": 300000, "etf": 500000, "stock": 200000}
    assert al["total_cents"] == 1000000
    assert al["idle_cash_cents"] == 300000
    assert al["largest"]["ticker"] == "VTI" and al["largest"]["pct_of_invested"] == 57.1  # 4000 / 7000
    assert al["single_stock_pct_of_invested"] == 28.6


def test_runway_uses_checking_plus_brokerage_cash_minus_card_over_typical_month():
    r = money.runway([CHECKING, CARD, CMA], HOLDINGS, typical_monthly_cents=100000)
    # 1400 available checking + 3000 cash-like in CMA - 350 owed on the card = 4050 over 1000 a month
    assert r["cash_cents"] == 405000
    assert r["months"] == 4.0


def test_runway_measures_cash_against_the_plans_monthly_costs_and_keeps_the_typical_month_beside_it():
    plan = {**EMPTY_PLAN, "bills": [bill("Rent", 1200, 1), bill("Car", 300, 9)], "monthly_spend": {"FOOD_AND_DRINK": 500}}
    r = money.runway([CHECKING, CARD, CMA], HOLDINGS, typical_monthly_cents=300000, plan=plan)
    # $4,050 cash over $1,200 + $300 + $500 = $2,000 planned a month; a $3,000 typical month (move-in costs) gives 1.4
    assert (r["basis"], r["monthly_cents"], r["months"]) == ("plan", 200000, 2.0)
    assert (r["typical_monthly_cents"], r["typical_months"]) == (300000, 1.4)

    r = money.runway([CHECKING, CARD, CMA], HOLDINGS, typical_monthly_cents=300000, plan=EMPTY_PLAN)
    assert (r["basis"], r["monthly_cents"], r["months"]) == ("typical", 300000, 1.4)


def test_typical_monthly_spend_averages_complete_months_in_history():
    txns = [t(50000, "2026-07-31"), t(100000, "2026-08-10"), t(200000, "2026-09-10"), t(999999, "2026-10-05")]
    # history starts Jul 31: August and September are the complete months; October is partial
    assert money.typical_monthly_spend(txns, date(2026, 10, 29)) == 150000
    assert money.typical_monthly_spend([], date(2026, 10, 29)) is None


# --- plan-driven: payday, bills, safe to spend

from finance.plan import EMPTY_PLAN  # noqa: E402


def income(schedule="biweekly", next_payday="2026-11-06", **kw):
    return {"amount": 2400, "schedule": schedule, "next_payday": next_payday, **kw}


def bill(name, amount, due_day, account="1111", **kw):
    return {"name": name, "amount": amount, "due_day": due_day, "account": account, "category": None,
            "autopay": True, "fixed": True, **kw}


def test_next_payday_rolls_the_anchor_forward_by_schedule():
    today = date(2026, 10, 29)
    assert money.next_payday(income("biweekly", "2026-11-06"), today) == date(2026, 11, 6)
    assert money.next_payday(income("biweekly", "2026-10-09"), today) == date(2026, 11, 6)
    assert money.next_payday(income("weekly", "2026-10-29"), today) == date(2026, 11, 5)  # paid today: next one
    assert money.next_payday(income("monthly", "2026-09-30"), today) == date(2026, 10, 30)
    assert money.next_payday(income("monthly", "2026-01-31"), date(2026, 11, 1)) == date(2026, 11, 30)
    assert money.next_payday(income("semimonthly", "2026-10-15"), today) == date(2026, 10, 31)
    assert money.next_payday(income("semimonthly", "2026-10-15", days=[1, 15]), today) == date(2026, 11, 1)


def test_pay_period_runs_from_the_last_payday_to_today_against_the_same_days_of_the_one_before():
    today = date(2026, 10, 29)
    assert money.period_range("pay_period", today, income("biweekly", "2026-11-06")) == (
        date(2026, 10, 23), today, date(2026, 10, 9), date(2026, 10, 15))
    assert money.period_range("pay_period", today, income("semimonthly", "2026-10-15")) == (
        date(2026, 10, 15), today, date(2026, 9, 30), date(2026, 10, 14))
    assert money.period_range("pay_period", date(2026, 11, 1), income("monthly", "2026-01-31")) == (
        date(2026, 10, 31), date(2026, 11, 1), date(2026, 9, 30), date(2026, 10, 1))
    assert money.period_range("pay_period", today, income("weekly", "2026-10-29"))[:2] == (today, today)  # paid today
    with pytest.raises(ValueError):
        money.period_range("pay_period", today)  # no pay schedule in the plan


def test_a_chosen_date_range_compares_with_as_many_days_just_before_it():
    s = money.spend_summary([t(1000, "2026-10-12"), t(400, "2026-10-05"), t(9900, "2026-10-20")], "custom",
                            date(2026, 10, 29), start=date(2026, 10, 10), end=date(2026, 10, 19))
    assert (s["start"], s["end"], s["total_cents"]) == ("2026-10-10", "2026-10-19", 1000)
    assert s["previous"] == {"start": "2026-09-30", "end": "2026-10-09", "total_cents": 400}


def test_safe_to_spend_with_an_empty_plan_asks_for_pay_schedule_and_bills():
    s = money.safe_to_spend([CHECKING, CARD], EMPTY_PLAN, [], date(2026, 10, 29))
    assert s["status"] == "needs_plan"
    assert s["missing"] == ["pay schedule", "bills"]
    assert "per_day_cents" not in s


def test_safe_to_spend_worked_example():
    plan = {**EMPTY_PLAN, "income": income("biweekly", "2026-11-06"), "buffer": 100,
            "bills": [bill("Phone", 50, 1), bill("Car", 410.00, 9), bill("Rent", 1200, 6)]}
    s = money.safe_to_spend([CHECKING, CARD], plan, [], date(2026, 10, 29))

    # checking available 1400.00 (pending debit already out), card owed 350.00 incl. pending,
    # bills due before Nov 6: Phone (Nov 1). Rent is due ON payday, Car after it. Buffer 100.
    # (1400 - 50 - 350 - 100) / 8 days = 112.50 a day
    assert s["status"] == "ok"
    assert s["payday"] == "2026-11-06" and s["days"] == 8
    assert [b["name"] for b in s["bills"]] == ["Phone"]
    assert s["available_cents"] == 90000
    assert s["per_day_cents"] == 11250
    assert [line["label"] for line in s["lines"]] == ["Checking (available)", "Bills before payday",
                                                      "Credit card owed", "Buffer"]


def test_safe_to_spend_skips_a_bill_already_paid_this_cycle_and_can_go_negative():
    plan = {**EMPTY_PLAN, "income": income("biweekly", "2026-11-06"), "buffer": 0,
            "bills": [bill("Acme Insurance", 11.99, 29), bill("Rent", 1200, 1)]}
    paid = [t(1199, "2026-10-29", "GENERAL_SERVICES", "Acme Insurance")]
    s = money.safe_to_spend([CHECKING, CARD], plan, paid, date(2026, 10, 29))

    assert [b["name"] for b in s["bills"]] == ["Rent"]
    assert s["available_cents"] == 140000 - 120000 - 35000
    assert s["per_day_cents"] == -1875


def test_safe_to_spend_keeps_a_just_missed_unpaid_bill_at_its_estimate_until_the_payment_posts():
    plan = {**EMPTY_PLAN, "income": income("biweekly", "2026-11-06"), "buffer": 0,
            "bills": [bill("Flint Energies", 100, 27, fixed=False, autopay=False),   # due 2 days ago, no payment yet
                      bill("Old Gym", 30, 20)]}                                   # 9 days ago: past the grace window
    s = money.safe_to_spend([CHECKING, CARD], plan, [], date(2026, 10, 29))

    assert [(b["name"], b["due"], b["amount_cents"]) for b in s["bills"]] == [("Flint Energies", "2026-10-27", 10000)]
    assert s["available_cents"] == 140000 - 10000 - 35000

    # the real bill posts for less than the estimate: it drops out, and the actual amount is already in checking
    posted = [t(8900, "2026-10-29", "RENT_AND_UTILITIES", "Flint Energies")]
    s = money.safe_to_spend([CHECKING, CARD], plan, posted, date(2026, 10, 29))
    assert s["bills"] == []
    assert s["available_cents"] == 140000 - 35000


def test_on_payday_the_paycheck_counts_as_coming_until_it_shows_up_in_checking():
    plan = {**EMPTY_PLAN, "income": income("biweekly", "2026-11-06"), "buffer": 100, "bills": [bill("Phone", 50, 10)]}
    today = date(2026, 11, 6)  # payday, and the 2,400.00 deposit hasn't synced

    s = money.safe_to_spend([CHECKING, CARD], plan, [], today)
    # checking 1400 + paycheck 2400 - Phone 50 - card 350 - buffer 100 = 3300 over the 14 days to Nov 20
    assert s["payday"] == "2026-11-20" and s["days"] == 14
    assert s["expected_income_cents"] == 240000
    assert s["available_cents"] == 330000
    assert s["lines"][1] == {"label": "Payday income (not synced yet)", "amount_cents": 240000}

    # the deposit arrives (a day early or on the day): checking already holds it, so it is not added twice
    landed = [t(-240000, "2026-11-05", "INCOME", "Employer")]
    s = money.safe_to_spend([CHECKING, CARD], plan, landed, today)
    assert s["expected_income_cents"] == 0
    assert s["available_cents"] == 140000 - 5000 - 35000 - 10000
    assert "Payday income (not synced yet)" not in [line["label"] for line in s["lines"]]


def test_a_deposit_that_is_not_labelled_income_still_counts_as_the_paycheck_if_it_is_paycheck_sized():
    plan = {**EMPTY_PLAN, "income": income("biweekly", "2026-11-06"), "buffer": 0, "bills": [bill("Phone", 50, 10)]}
    deposit = [t(-231000, "2026-11-06", "OTHER", "ACH Deposit")]
    s = money.safe_to_spend([CHECKING, CARD], plan, deposit, date(2026, 11, 6))
    assert s["expected_income_cents"] == 0


def test_the_day_after_payday_nothing_is_expected_even_if_no_deposit_was_seen():
    plan = {**EMPTY_PLAN, "income": income("biweekly", "2026-11-06"), "buffer": 0, "bills": [bill("Phone", 50, 10)]}
    s = money.safe_to_spend([CHECKING, CARD], plan, [], date(2026, 11, 7))
    assert s["expected_income_cents"] == 0 and s["days"] == 13


def test_todays_allowance_holds_steady_through_the_day_and_counts_down_what_is_left():
    plan = {**EMPTY_PLAN, "income": income("biweekly", "2026-11-06"), "buffer": 100, "bills": [bill("Phone", 50, 1)]}
    txns = [t(3000, "2026-10-29", merchant="Cafe"),                                  # spent today, on the card
            t(5000, "2026-10-29", "GENERAL_SERVICES", "Phone"),                       # the Phone bill, already set aside
            t(20000, "2026-10-29", "LOAN_PAYMENTS", "BoA card", "LOAN_PAYMENTS_CREDIT_CARD_PAYMENT"),
            t(9900, "2026-10-28")]                                                    # yesterday's spending
    s = money.safe_to_spend([CHECKING, CARD], plan, txns, date(2026, 10, 29))

    # now: 1400 checking - 0 bills (Phone paid) - 350 card - 100 buffer = 950 over 8 days
    # the day started with today's 30 of spending still in hand: (950 + 30) / 8 = 122.50 a day; 92.50 left today
    assert s["available_cents"] == 95000
    assert s["spent_today_cents"] == 3000
    assert s["allowance_cents"] == 12250
    assert s["left_today_cents"] == 9250
    # Today's "spent today" tile counts the Phone bill as spending; this says why the allowance's 30 is less
    assert s["bills_paid_today_cents"] == 5000


def test_spent_today_counts_a_purchase_that_only_looks_like_a_plan_bill_not_due_this_week():
    plan = {**EMPTY_PLAN, "income": income("biweekly", "2026-11-06"), "buffer": 0,
            "bills": [bill("Apple iCloud", 2.99, 20, fixed=False)]}    # varies, so any amount matches; due Nov 20
    txns = [t(4500, "2026-10-29", "GENERAL_MERCHANDISE", "Apple")]      # an Apple purchase, three weeks early
    s = money.safe_to_spend([CHECKING, CARD], plan, txns, date(2026, 10, 29))

    assert s["spent_today_cents"] == 4500


def test_pace_tips_wait_for_a_week_of_the_month_unless_the_target_is_already_gone():
    plan = {**EMPTY_PLAN, "monthly_spend": {"FOOD_AND_DRINK": 300, "TRANSPORTATION": 50}}
    txns = [t(6000, "2026-10-01"), t(6000, "2026-10-01", "TRANSPORTATION", "Shell")]
    tips = money.tips([CHECKING], [], plan, txns, [], None, date(2026, 10, 1))

    # food: one $60 grocery run on the 1st says nothing about the month (it would "pace" to $1,860)
    # transport: $60 against $50 is over no matter the day
    assert [x["title"] for x in tips if x["kind"] == "pace"] == ["Transportation is on pace for $1,860 this month"]


def test_month_events_mark_a_plan_bill_paid_a_few_days_late():
    plan = {**EMPTY_PLAN, "bills": [bill("Rent", 1200, 1)]}
    txns = [t(120000, "2026-10-03", "RENT_AND_UTILITIES", "Rent")]
    events = money.month_events(plan, [], txns, date(2026, 10, 1), date(2026, 11, 1), date(2026, 10, 29))

    assert [(e["date"], e["paid"]) for e in events] == [("2026-10-01", True)]


def test_month_events_mark_paydays_plan_bills_and_detected_charges_across_the_month():
    plan = {**EMPTY_PLAN, "income": income("biweekly", "2026-11-06", amount=2000),
            "bills": [bill("Rent", 1200, 1), bill("Phone", 50, 20)]}
    netflix = monthly([1599] * 4, "Netflix")                    # charged the 12th, July through October
    txns = netflix + [t(5000, "2026-10-19", "GENERAL_SERVICES", "Phone")]
    recurring = money.detect_recurring(txns, date(2026, 10, 29))
    events = money.month_events(plan, recurring, txns, date(2026, 10, 1), date(2026, 12, 1), date(2026, 10, 29))

    assert [(e["date"], e["kind"], e["name"], e["amount_cents"], e["paid"]) for e in events] == [
        ("2026-10-01", "bill", "Rent", 120000, False),       # no payment seen: it stays unpaid, not guessed
        ("2026-10-09", "payday", "Payday", 200000, False),      # paydays run back from the anchor too
        ("2026-10-12", "bill", "Netflix", 1599, True),        # a detected charge that already happened
        ("2026-10-20", "bill", "Phone", 5000, True),          # paid the day before it was due
        ("2026-10-23", "payday", "Payday", 200000, False),
        ("2026-11-01", "bill", "Rent", 120000, False),
        ("2026-11-06", "payday", "Payday", 200000, False),
        ("2026-11-12", "bill", "Netflix", 1599, False),       # projected from its cadence
        ("2026-11-20", "bill", "Phone", 5000, False),
        ("2026-11-20", "payday", "Payday", 200000, False),
    ]


def test_tips_flag_a_budget_on_pace_to_overrun_a_card_over_30_percent_thin_cash_and_unplanned_subscriptions():
    plan = {**EMPTY_PLAN, "monthly_spend": {"FOOD_AND_DRINK": 300, "TRANSPORTATION": 200},
            "bills": [bill("Rent", 1200, 1)]}
    maxed = a("9999", "credit", "credit card", current=90000, available=100000, limit=200000)
    txns = [t(14000, "2026-10-03"), t(10000, "2026-10-14"), t(3000, "2026-10-14", "TRANSPORTATION", "Shell"),
            t(99000, "2026-09-20")]                                              # last month's food doesn't count
    subs = monthly([1599] * 4, "Netflix") + monthly([1099] * 4, "Spotify", start=(2026, 6, 20)) + \
        monthly([120000] * 4, "Rent", "RENT_AND_UTILITIES", start=(2026, 7, 1))  # rent is in the plan already
    recurring = money.detect_recurring(subs, date(2026, 10, 15))
    tips = money.tips([CHECKING, maxed], [], plan, txns, recurring, 200000, date(2026, 10, 15))

    # food: 240 by the 15th of 31 days is on pace for 496; 60 left over the 16 days to go is 3.75 a day
    # card: 1000 of 2000 used; 30% is 600. cash: 1400 checking - 1000 card = 400, 0.2 of the planned 1700 month
    assert [(x["kind"], x["title"], x["detail"]) for x in tips] == [
        ("pace", "Food and drink is on pace for $496 this month",
         "Your target is $300 and $240 is spent. $3.75 a day for the rest of October keeps it on target."),
        ("card", "Card …9999 is 50% used",
         "Paying it under 30% ($600) before the statement closes helps your credit score."),
        ("runway", "Cash covers 0.2 months of spending",
         "Checking and brokerage cash, less the card, against your planned $1,700 month of bills and spending targets. "
         "Three months is a common cushion."),
        ("subscriptions", "2 repeat charges outside your plan: $26.98 a month",
         "Netflix, Spotify. Add the ones you keep to the plan so safe-to-spend sets them aside."),
    ]


def test_portfolio_history_values_todays_shares_at_past_prices_and_holds_cash_and_unpriced_funds_flat():
    holdings = [{**h("VTI", "etf", 220000), "security_id": "s-vti", "quantity": 10},
                {**h("BND", "etf", 7200), "security_id": "s-bnd", "quantity": 1},
                {**h(None, "mutual fund", 21000, name="TSP C Fund"), "security_id": "manual:TSP:C", "quantity": 2.0},
                {**h("TTTXX", "mutual fund", 100000, cash=True), "security_id": "s-mm", "quantity": 1000},
                {**h("MYST", "mutual fund", 50000, name="Mystery Fund"), "security_id": "s-myst", "quantity": 5}]
    prices = {"VTI": [("2026-09-30", 190.0), ("2026-10-01", 200.0), ("2026-10-02", 210.0), ("2026-10-05", 220.0)],
              "BND": [("2026-10-02", 70.0), ("2026-10-05", 72.0)],      # no price on the 1st, so the chart starts later
              "TSP:C": [("2026-09-30", 100.0), ("2026-10-02", 110.0), ("2026-10-05", 105.0)]}
    hist = money.portfolio_history(holdings, prices, date(2026, 10, 1), date(2026, 10, 5))

    # flat: 1,000 cash + 500 Mystery Fund. 10/2: 10×210 + 1×70 + 2×110 = 2,390. 10/5: 10×220 + 72 + 2×105 = 2,482
    assert hist["points"] == [{"date": "2026-10-02", "total_cents": 389000},
                              {"date": "2026-10-05", "total_cents": 398200}]
    assert hist["flat_cents"] == 150000
    assert hist["unpriced"] == ["Mystery Fund"]


def test_portfolio_history_ends_on_today_at_the_value_shown_as_the_total():
    holdings = [{**h("VTI", "etf", 230000), "security_id": "s-vti", "quantity": 10},     # the bank's live value
                {**h("TTTXX", "mutual fund", 100000, cash=True), "security_id": "s-mm", "quantity": 1000}]
    prices = {"VTI": [("2026-10-01", 200.0), ("2026-10-02", 210.0)]}
    hist = money.portfolio_history(holdings, prices, date(2026, 10, 1), date(2026, 10, 3))

    # yesterday's close is the last price; today's point is the 3,300 the Investing total shows
    assert hist["points"] == [{"date": "2026-10-01", "total_cents": 300000},
                              {"date": "2026-10-02", "total_cents": 310000},
                              {"date": "2026-10-03", "total_cents": 330000}]


def test_yearly_contributions_are_each_paychecks_auto_investment_times_paydays_a_year():
    doc = {"version": 1, "accounts": [
        {"name": "TSP", "holdings": [{"symbol": "TSP:C", "balance": 8200, "as_of": "2026-10-27", "per_paycheck": 250},
                                     {"symbol": "TSP:S", "balance": 100, "as_of": "2026-10-27", "per_paycheck": 50}]},
        {"name": "IRA", "holdings": [{"symbol": "VFIAX", "shares": 4}]}]}
    assert money.yearly_contributions(doc, income("biweekly")) == 26 * 30000
    assert money.yearly_contributions(doc, income("semimonthly")) == 24 * 30000
    assert money.yearly_contributions(doc, None) == 0       # no pay schedule, no paydays to count


# --- recurring charges


def monthly(amounts, merchant, category="ENTERTAINMENT", start=(2026, 7, 12), detailed=None, names=None, **kw):
    y, m, d = start
    out = []
    for i, amt in enumerate(amounts):
        mm = m + i
        day = f"{y + (mm - 1) // 12}-{(mm - 1) % 12 + 1:02d}-{d:02d}"
        out.append(t(amt, day, category, (names or [merchant] * len(amounts))[i], detailed, **kw))
    return out


def test_detect_recurring_finds_fixed_and_variable_monthly_series_and_skips_one_offs_and_transfers():
    txns = (
        monthly([1599, 1599, 1599, 1599], "Streamly", names=["Streamly", "Streamly", "Streamly Premium", "Streamly"])
        + monthly([299, 299, 299], "Apple.com/bill", start=(2026, 8, 3))
        + monthly([1099, 1099, 1099], "Apple.com/bill", start=(2026, 8, 20))
        + monthly([11800, 14650, 9920], "City Power", "RENT_AND_UTILITIES", start=(2026, 8, 7))
        + monthly([42000, 42000, 42000], "Car Lender", "LOAN_PAYMENTS", start=(2026, 8, 15),
                  detailed="LOAN_PAYMENTS_CAR_PAYMENT")
        + monthly([50000, 50000, 50000], "Brokerage", "TRANSFER_OUT", start=(2026, 8, 1),
                  detailed="TRANSFER_OUT_ACCOUNT_TRANSFER")
        + monthly([-240000, -240000, -240000], "Employer", "INCOME", start=(2026, 8, 1))
        + [t(8999, "2026-09-02", "GENERAL_MERCHANDISE", "Gadget Shop")]
    )
    found = {(r["merchant"], r["amount_cents"]): r for r in money.detect_recurring(txns, date(2026, 10, 29))}

    assert set(found) == {("Streamly", 1599), ("Apple.com/bill", 299), ("Apple.com/bill", 1099),
                          ("City Power", 9920), ("Car Lender", 42000)}
    s = found[("Streamly", 1599)]
    assert (s["cadence"], s["count"], s["last_date"], s["next_due"], s["fixed"]) == \
        ("monthly", 4, "2026-10-12", "2026-11-12", True)
    power = found[("City Power", 9920)]
    assert power["fixed"] is False and power["median_cents"] == 11800
    assert found[("Car Lender", 42000)]["next_due"] == "2026-11-15"


def test_detect_recurring_ignores_chance_matches_among_everyday_charges():
    # Pizza: many charges at random amounts; two happen to be within 5% and a week apart.
    pizza = [t(c, d, merchant="Pizza Place") for c, d in [(1200, "2026-10-15"), (1230, "2026-10-22"), (2890, "2026-10-03"),
                                                          (740, "2026-10-11"), (3310, "2026-10-19"), (1960, "2026-10-24")]]
    pharmacy = [t(2400, "2026-09-12", "MEDICAL", "CVS"), t(5100, "2026-10-13", "MEDICAL", "CVS")]  # two visits a month apart
    # Power bills vary; two of four land within 5% of each other. One varying series, due after the latest bill.
    power = monthly([7820, 6407, 6390, 7190], "City Power", "RENT_AND_UTILITIES", start=(2026, 7, 18))
    found = money.detect_recurring(pizza + pharmacy + power, date(2026, 10, 29))

    assert [(r["merchant"], r["fixed"], r["next_due"]) for r in found] == [("City Power", False, "2026-11-18")]


def test_detect_recurring_follows_a_subscription_through_a_price_rise():
    txns = monthly([1599, 1599, 1599, 1799], "Streamly")  # Oct 12 charge is the new price
    [s] = money.detect_recurring(txns, date(2026, 10, 29))
    assert (s["amount_cents"], s["count"], s["last_date"], s["next_due"], s["fixed"]) == \
        (1799, 4, "2026-10-12", "2026-11-12", True)


def test_detect_recurring_needs_repeat_charges_because_the_banks_recurring_label_only_means_card_on_file():
    label = "CHECKCARD STREAMLY.COM RECURRING"
    one_offs = [t(150, "2026-08-04", "ENTERTAINMENT", "Streamly", name=label),
                t(650, "2026-10-13", "ENTERTAINMENT", "Streamly", name=label),
                t(2300, "2026-10-28", "ENTERTAINMENT", "Streamly", name=label),
                t(9500, "2026-08-27", "RENT_AND_UTILITIES", "Lakeside Water", name="Lakeside Wat RECURRING"),
                t(3, "2026-10-02", "GENERAL_SERVICES", "Hosting Co", name="HOSTING 12AB RECURRING")]
    assert money.detect_recurring(one_offs, date(2026, 11, 1)) == []

    repeats = monthly([1599, 1599], "Streamly", start=(2026, 9, 12))
    [s] = money.detect_recurring(repeats, date(2026, 11, 1))
    assert (s["merchant"], s["count"], s["next_due"]) == ("Streamly", 2, "2026-11-12")


def test_detect_recurring_drops_a_series_that_stopped_charging():
    txns = monthly([999, 999, 999], "OldGym", "PERSONAL_CARE", start=(2026, 6, 10))  # last charge Aug 10
    assert money.detect_recurring(txns, date(2026, 10, 29)) == []
    assert len(money.detect_recurring(txns, date(2026, 9, 12))) == 1


# --- unusual charges


def test_unusual_charges_flags_duplicates_large_one_offs_and_recurring_price_jumps():
    coffee = [t(650 + i, f"2026-{m:02d}-{d:02d}", tid=f"c{m}{d}") for i, (m, d) in
              enumerate([(8, 3), (8, 17), (9, 1), (9, 14), (9, 29), (10, 6), (10, 13)])]
    txns = coffee + (
        [t(4250, "2026-10-14", merchant="Diner", tid="d1"), t(4250, "2026-10-15", merchant="Diner", tid="d2"),
         t(4250, "2026-09-20", merchant="Diner", tid="d0")]                                      # weeks apart: fine
        + monthly([1599, 1599, 1599, 1799], "Streamly")                                          # Oct 12 raise
        + monthly([11800, 14650, 9920, 25000], "City Power", "RENT_AND_UTILITIES", start=(2026, 7, 7))
        + [t(45000, "2026-10-10", "GENERAL_MERCHANDISE", "Gadget Shop", tid="g1"),
           t(60000, "2026-09-01", "GENERAL_MERCHANDISE", "Sofa Store", tid="s1")]                 # outside 14 days
    )
    flagged = {(u["kind"], u["merchant"]): u for u in money.unusual_charges(txns, date(2026, 10, 20))}

    assert set(flagged) == {("duplicate", "Diner"), ("price_change", "Streamly"),
                            ("higher_than_usual", "City Power"), ("large", "Gadget Shop")}
    assert flagged[("duplicate", "Diner")]["transaction_id"] == "d2"
    assert flagged[("price_change", "Streamly")]["previous_cents"] == 1599
    assert flagged[("higher_than_usual", "City Power")]["previous_cents"] == 11800
    assert all(u["detail"] for u in flagged.values())


def test_unusual_charges_take_the_plans_word_that_a_bill_varies():
    power = monthly([6407, 6400, 6390, 7190], "City Power", "RENT_AND_UTILITIES", start=(2026, 7, 18))
    assert [u["kind"] for u in money.unusual_charges(power, date(2026, 10, 20))] == ["price_change"]

    varies = [{"name": "City Power", "amount": 70, "due_day": 18, "fixed": False}]
    assert money.unusual_charges(power, date(2026, 10, 20), varies) == []
    spike = power[:3] + [t(9900, "2026-10-18", "RENT_AND_UTILITIES", "City Power")]  # 1.5x the usual still shows
    assert [u["kind"] for u in money.unusual_charges(spike, date(2026, 10, 20), varies)] == ["higher_than_usual"]


# --- upcoming bills


def test_upcoming_bills_merges_plan_and_detected_and_checks_checking_covers_each_one():
    plan = {**EMPTY_PLAN, "income": income("biweekly", "2026-11-06"),
            "bills": [bill("Rent", 1500, 1), bill("Phone", 50, 5, account="3333"), bill("Car Lender", 420, 15)]}
    recurring = [
        {"merchant": "City Power", "key": "city", "amount_cents": 9920, "median_cents": 11800, "cadence": "monthly",
         "next_due": "2026-11-07", "fixed": False, "account_key": "bofa:1111"},
        {"merchant": "Streamly", "key": "streamly", "amount_cents": 1599, "median_cents": 1599, "cadence": "monthly",
         "next_due": "2026-11-12", "fixed": True, "account_key": "bofa:3333"},
        {"merchant": "Car Lender", "key": "car", "amount_cents": 42000, "median_cents": 42000, "cadence": "monthly",
         "next_due": "2026-11-15", "fixed": True, "account_key": "bofa:1111"},
    ]
    cal = money.upcoming_bills([CHECKING, CARD], plan, recurring, [], date(2026, 10, 29))

    rows = [(b["name"], b["due"], b["amount_cents"], b["source"], b["covered"]) for b in cal["bills"]]
    # checking 1400 - Rent 1500 = -100 (short); payday +2400 -> 2300; City Power ~118 -> 2182; Car 420 -> 1762;
    # payday +2400 -> 4162; Rent 1500 -> 2662. Card-paid bills land on the card: coverage n/a.
    assert rows == [
        ("Rent", "2026-11-01", 150000, "plan", False),
        ("Phone", "2026-11-05", 5000, "plan", None),
        ("City Power", "2026-11-07", 11800, "detected", True),
        ("Streamly", "2026-11-12", 1599, "detected", None),
        ("Car Lender", "2026-11-15", 42000, "plan", True),
        ("Rent", "2026-12-01", 150000, "plan", True),
    ]
    assert cal["paydays"] == ["2026-11-06", "2026-11-20"]
    assert cal["uncovered"] == 1
    assert cal["bills"][2]["estimated"] is True


def test_upcoming_bills_marks_a_plan_bill_already_paid():
    plan = {**EMPTY_PLAN, "bills": [bill("Rent", 1500, 1)]}
    cal = money.upcoming_bills([CHECKING], plan, [], [t(150000, "2026-10-28", "RENT_AND_UTILITIES", "Rent")],
                               date(2026, 10, 29), days=20)
    [rent] = cal["bills"]
    assert rent["paid"] is True and rent["covered"] is True and cal["uncovered"] == 0


# --- investing: left to invest


def test_left_to_invest_adds_idle_brokerage_cash_to_checking_left_after_card_bills_and_buffer():
    holdings = [{"account_key": "bofa:2222", "name": "Money Market Fund", "type": "mutual fund",
                 "is_cash_equivalent": True, "value_cents": 500000},
                {"account_key": "bofa:2222", "ticker": "VTI", "type": "etf", "value_cents": 900000}]
    upcoming = {"bills": [
        {"name": "Car", "amount_cents": 42000, "account_key": "bofa:1111", "paid": False},
        {"name": "Streamly", "amount_cents": 1599, "account_key": "bofa:3333", "paid": False},  # on the card
        {"name": "Rent", "amount_cents": 150000, "account_key": "bofa:1111", "paid": True},      # already out
    ]}
    plan = {**EMPTY_PLAN, "buffer": 100, "bills": [bill("Car", 420, 9)]}
    li = money.left_to_invest([CHECKING, CARD], holdings, plan, upcoming, typical_monthly_cents=200000,
                             today=date(2026, 10, 29))

    # checking 1400 - card 350 - Car 420 - buffer 100 = 530; + idle 5000 = 5530
    assert li["checking_left_cents"] == 53000
    assert li["idle_cash_cents"] == 500000
    assert li["total_cents"] == 553000
    assert [(o["months"], o["amount_cents"]) for o in li["options"]] == [(0, 553000), (1, 353000), (3, 0)]
    assert li["missing"] == []


def test_left_to_invest_never_counts_a_checking_shortfall_and_flags_an_empty_plan():
    li = money.left_to_invest([CHECKING, a("9", "credit", "credit card", current=500000)], [], EMPTY_PLAN,
                              {"bills": []}, typical_monthly_cents=None, today=date(2026, 10, 29))
    assert li["checking_left_cents"] == 0 and li["total_cents"] == 0
    assert li["missing"] == ["bills", "buffer"]
    assert li["options"] == [{"months": 0, "amount_cents": 0, "label": "Invest it all"}]


# --- attention list

NOW = "2026-10-29T16:00:00+00:00"


def item_row(**kw):
    base = {"item_id": "i1", "institution": "Bank of America", "institution_id": "ins_127989", "status": "ok",
            "error_code": None, "error_message": None, "consent_expires_at": None,
            "first_seen_at": "2026-01-10T12:00:00+00:00", "last_synced_at": "2026-10-29T12:00:00+00:00"}
    return {**base, **kw}


def test_consent_uses_plaid_expiry_else_estimates_twelve_months_for_bank_of_america():
    assert money.consent(item_row(consent_expires_at="2026-12-01T00:00:00Z"), NOW) == \
        {"expires": "2026-12-01", "days_left": 33, "estimated": False}
    assert money.consent(item_row(), NOW) == {"expires": "2027-01-10", "days_left": 73, "estimated": True}
    assert money.consent(item_row(institution_id="ins_115610"), NOW) is None


def test_alerts_rank_login_and_uncovered_bills_above_unusual_charges_and_stale_syncs():
    items = [item_row(status="login_required", error_code="ITEM_LOGIN_REQUIRED"),
             item_row(item_id="i2", institution="Merrill", institution_id="ins_115610",
                      last_synced_at="2026-10-28T20:00:00+00:00")]  # 20 hours ago
    upcoming = {"bills": [{"name": "Rent", "due": "2026-11-01", "amount_cents": 150000, "covered": False}]}
    unusual = [{"kind": "duplicate", "merchant": "Diner", "amount_cents": 4250, "date": "2026-10-26",
                "detail": "Same amount at Diner within 2 days", "transaction_id": "d2"}]
    found = money.alerts(items, upcoming, unusual, NOW)

    assert [(x["level"], x["kind"]) for x in found] == [
        ("urgent", "login_required"), ("urgent", "uncovered_bill"), ("warn", "unusual_charge"), ("warn", "stale_sync")]
    assert "Bank of America" in found[0]["title"] and "Fix login" in found[0]["detail"]
    assert "Merrill" in found[3]["title"]
    assert found[1]["detail"] == "$1,500.00 due Sun, Nov 1."  # dates read as words: these show on the phone as-is
    assert found[2]["title"] == "Diner $42.50 on Mon, Oct 26"


def test_alerts_warn_when_consent_has_thirty_days_or_fewer():
    soon = item_row(consent_expires_at="2026-11-20T00:00:00Z")
    [x] = money.alerts([soon], {"bills": []}, [], NOW)
    assert (x["level"], x["kind"]) == ("warn", "consent_expiring") and "22 days" in x["title"]
    assert money.alerts([item_row()], {"bills": []}, [], NOW) == []


def alert_id(items=(), bills=(), unusual=(), now=NOW):
    [x] = money.alerts(list(items), {"bills": list(bills)}, list(unusual), now)
    return x["id"]


def test_an_alert_id_stays_the_same_while_the_problem_lasts_and_changes_when_it_happens_again():
    # A login that stays broken keeps its id; one that recovers and breaks again is a new alert.
    broken = item_row(status="login_required")
    assert alert_id([broken]) == alert_id([{**broken, "last_attempt_at": "2026-10-29T15:00:00+00:00"}])
    assert alert_id([broken]) != alert_id([{**broken, "last_synced_at": "2026-10-29T15:00:00+00:00"}])

    # Each month's bill is its own alert, and a warning that turns urgent shows again.
    rent = {"name": "Rent", "due": "2026-11-01", "amount_cents": 150000, "covered": False}
    assert alert_id(bills=[rent]) != alert_id(bills=[{**rent, "due": "2026-12-01"}])
    soon = item_row(consent_expires_at="2026-11-20T00:00:00Z")
    later = "2026-11-15T16:00:00+00:00"
    assert alert_id([soon]) != alert_id([{**soon, "last_synced_at": later}], now=later)


def test_a_flagged_charge_keeps_its_alert_id_when_it_posts_under_a_new_transaction_id_and_date():
    pending = {"kind": "large", "merchant": "Diner", "amount_cents": 4250, "date": "2026-10-26",
               "authorized_date": "2026-10-25", "detail": "x", "transaction_id": "pending-1"}
    posted = {**pending, "date": "2026-10-27", "transaction_id": "posted-1"}
    assert alert_id(unusual=[pending]) == alert_id(unusual=[posted])
    assert alert_id(unusual=[pending]) != alert_id(unusual=[{**pending, "authorized_date": "2026-11-25"}])
    assert money.unusual_charges([t(250000, "2026-10-26", authorized="2026-10-25", merchant="Rare Shop"),
                                  t(100, "2026-10-01"), t(100, "2026-10-02")],
                                 date(2026, 10, 29))[0]["authorized_date"] == "2026-10-25"


def test_identical_flagged_charges_are_dismissed_one_at_a_time():
    twin = {"kind": "duplicate", "merchant": "Diner", "amount_cents": 4250, "date": "2026-10-26",
            "authorized_date": None, "detail": "x", "transaction_id": "a"}
    ids = [a["id"] for a in money.alerts([], {"bills": []}, [twin, {**twin, "transaction_id": "b"}], NOW)]
    assert len(set(ids)) == 2

    # The same charge flagged for a different reason is a different alert.
    assert alert_id(unusual=[twin]) != alert_id(unusual=[{**twin, "kind": "large"}])


def test_an_uncovered_bill_shows_again_when_its_amount_goes_up():
    rent = {"name": "Rent", "due": "2026-11-01", "amount_cents": 150000, "covered": False}
    assert alert_id(bills=[rent]) != alert_id(bills=[{**rent, "amount_cents": 160000}])


def test_an_alert_id_does_not_carry_plaid_identifiers_to_the_browser():
    assert "secretish" not in alert_id([item_row(status="login_required", item_id="item-secretish")])


# --- manually entered holdings


def test_manual_fund_is_shares_times_the_latest_price_and_its_account_counts_in_net_worth():
    doc = {"version": 1, "accounts": [{"name": "Fidelity", "holdings": [{"symbol": "FSPSX", "shares": 123.45}]}]}
    prices = {"FSPSX": [("2026-10-28", 17.93), ("2026-10-29", 18.02)]}
    accounts, holdings = money.manual_holdings(doc, prices, None, date(2026, 10, 30))

    # 123.45 shares × $18.02 = $2,224.569
    assert [(h["ticker"], h["quantity"], h["value_cents"], h["updated_at"]) for h in holdings] == \
        [("FSPSX", 123.45, 222457, "2026-10-29")]
    assert [(a["account_key"], a["name"], a["type"], a["mask"], a["current_cents"]) for a in accounts] == \
        [("manual:fidelity", "Fidelity", "investment", None, 222457)]
    assert money.net_worth(accounts)["net_cents"] == 222457
    assert money.bucket_of(holdings[0]) == "fund"


def test_manual_tsp_balance_buys_shares_each_payday_since_it_was_entered():
    tsp = {"symbol": "TSP:C", "name": "TSP C Fund", "balance": 1250.00, "as_of": "2026-10-30", "per_paycheck": 300.00}
    doc = {"version": 1, "accounts": [{"name": "TSP", "holdings": [tsp]}]}
    prices = {"TSP:C": [("2026-10-29", 125.0), ("2026-11-06", 120.0), ("2026-11-19", 150.0), ("2026-11-21", 152.0)]}
    pay = {"amount": 2000, "schedule": "biweekly", "next_payday": "2026-11-06"}

    # $1250 ÷ $125 (10-29, the last price on or before as_of) = 10 shares; payday 11-06 buys $300 ÷ $120 = 2.5;
    # payday 11-20 has no price yet, so $300 ÷ $150 (11-19) = 2. 14.5 shares × $152 (latest) = $2,204.
    _, [h] = money.manual_holdings(doc, prices, pay, date(2026, 11, 21))
    assert (h["ticker"], h["name"], h["quantity"], h["value_cents"]) == (None, "TSP C Fund", 14.5, 220400)

    # No income in the plan means no paydays: the balance alone, 10 shares × $152.
    _, [h] = money.manual_holdings(doc, prices, None, date(2026, 11, 21))
    assert h["value_cents"] == 152000


def test_manual_tsp_counts_the_paydays_before_the_plans_next_payday_too():
    tsp = {"symbol": "TSP:C", "balance": 1250.00, "as_of": "2026-10-30", "per_paycheck": 300.00}
    doc = {"version": 1, "accounts": [{"name": "TSP", "holdings": [tsp]}]}
    prices = {"TSP:C": [("2026-10-29", 125.0), ("2026-11-06", 120.0), ("2026-11-20", 150.0), ("2026-12-04", 100.0),
                        ("2026-12-09", 160.0)]}
    pay = {"amount": 2000, "schedule": "biweekly", "next_payday": "2026-12-04"}  # moved on after 11-06 and 11-20

    # 10 shares on 10-30, then paydays 11-06 (+2.5 at $120), 11-20 (+2 at $150), 12-04 (+3 at $100) = 17.5 × $160
    _, [h] = money.manual_holdings(doc, prices, pay, date(2026, 12, 10))
    assert h["value_cents"] == 280000


def test_manual_holding_without_a_price_yet_has_no_value_and_adds_nothing():
    doc = {"version": 1, "accounts": [
        {"name": "Funds", "holdings": [{"symbol": "FSPSX", "shares": 100}, {"symbol": "VTSAX", "shares": 50}]},
        {"name": "TSP", "holdings": [{"symbol": "TSP:C", "balance": 1000, "as_of": "2026-10-30"}]}]}
    accounts, holdings = money.manual_holdings(doc, {"FSPSX": [("2026-10-29", 18.02)]}, None, date(2026, 10, 30))

    assert [(h["security_id"], h["quantity"], h["value_cents"], h["updated_at"]) for h in holdings] == [
        ("manual:FSPSX", 100.0, 180200, "2026-10-29"), ("manual:VTSAX", 50.0, None, None),
        ("manual:TSP:C", None, None, None)]
    assert [(a["current_cents"], a["updated_at"]) for a in accounts] == [(180200, "2026-10-29"), (0, None)]
    assert money.allocation(holdings[1:])["total_cents"] == 0


# --- goals: money set aside in checking, a slice each payday


def test_a_goal_sets_aside_an_equal_slice_each_payday_until_its_date_then_frees_it():
    pay = income("biweekly", "2026-11-06")
    trip = {"name": "Vacation", "target": 1000, "start": "2026-11-08", "by": "2027-01-15"}
    # Paydays from Nov 8 up to (not on) Jan 15: Nov 20, Dec 4, Dec 18, Jan 1. $1,000 / 4 = $250 each.
    g = money.goal_progress(trip, pay, date(2026, 11, 8))
    assert (g["status"], g["set_aside_cents"], g["per_payday_cents"]) == ("saving", 0, 25000)
    assert (g["next_payday"], g["paydays_left"]) == ("2026-11-20", 4)

    assert money.goal_progress(trip, pay, date(2026, 12, 5))["set_aside_cents"] == 50000
    on_last = money.goal_progress(trip, pay, date(2027, 1, 1))
    assert (on_last["set_aside_cents"], on_last["paydays_left"], on_last["next_payday"]) == (100000, 0, None)

    done = money.goal_progress(trip, pay, date(2027, 1, 15))
    assert (done["status"], done["set_aside_cents"], done["target_cents"]) == ("ready", 0, 100000)


def test_safe_to_spend_keeps_what_goals_have_set_aside_out_of_reach():
    plan = {**EMPTY_PLAN, "income": income("biweekly", "2026-11-06"), "buffer": 100,
            "bills": [bill("Phone", 50, 1)],
            "goals": [{"name": "Trip", "target": 400, "start": "2026-10-01", "by": "2026-12-01"},
                      {"name": "Someday", "target": 5000}]}  # no date: nothing set aside
    s = money.safe_to_spend([CHECKING, CARD], plan, [], date(2026, 10, 29))

    # Trip: paydays Oct 9, Oct 23, Nov 6, Nov 20 -> $100 each; Oct 9 and Oct 23 have passed -> $200 set aside.
    # (1400 - 50 - 350 - 100 - 200) / 8 days = 87.50 a day
    assert s["available_cents"] == 70000
    assert s["per_day_cents"] == 8750
    assert s["lines"][-1] == {"label": "Saving for goals", "amount_cents": -20000}


def test_left_to_invest_leaves_goal_savings_in_checking():
    plan = {**EMPTY_PLAN, "buffer": 100, "bills": [bill("Car", 420, 9)], "income": income("biweekly", "2026-11-06"),
            "goals": [{"name": "Trip", "target": 400, "start": "2026-10-01", "by": "2026-12-01"}]}
    li = money.left_to_invest([CHECKING, CARD], [], plan, {"bills": []}, typical_monthly_cents=None,
                              today=date(2026, 10, 29))
    # checking 1400 - card 350 - buffer 100 - Trip's $200 set aside so far = 750
    assert li["checking_left_cents"] == 75000
    assert {"label": "Saving for goals", "amount_cents": -20000} in li["lines"]


def test_a_goal_rebased_midway_spreads_only_what_is_left_over_the_paydays_left():
    pay = income("biweekly", "2026-11-06")
    # Raised to $1,500 on Dec 5 with $500 already set aside: Dec 18 and Jan 1 carry $500 each.
    trip = {"name": "Vacation", "target": 1500, "saved": 500, "start": "2026-12-05", "by": "2027-01-15"}
    g = money.goal_progress(trip, pay, date(2026, 12, 5))
    assert (g["set_aside_cents"], g["per_payday_cents"], g["paydays_left"]) == (50000, 50000, 2)
    assert money.goal_progress(trip, pay, date(2027, 1, 1))["set_aside_cents"] == 150000


def test_a_goal_started_on_payday_takes_its_first_slice_next_payday():
    g = money.goal_progress({"name": "Trip", "target": 400, "start": "2026-11-06", "by": "2026-12-20"},
                            income("biweekly", "2026-11-06"), date(2026, 11, 6))
    # Nov 20 and Dec 4 and Dec 18 after Nov 6
    assert (g["set_aside_cents"], g["next_payday"], g["paydays_left"]) == (0, "2026-11-20", 3)


def test_a_freed_goal_shows_as_fully_saved_but_holds_nothing_back():
    g = money.goal_progress({"name": "Trip", "target": 400, "start": "2026-10-01", "by": "2026-12-01"},
                            income("biweekly", "2026-11-06"), date(2026, 12, 1))
    assert (g["status"], g["set_aside_cents"], g["saved_cents"]) == ("ready", 0, 40000)


# --- buffer: how many months of planned costs it covers


def test_buffer_is_measured_in_months_of_planned_bills_plus_spending_targets():
    plan = {"bills": [{"name": "Rent", "amount": 1500, "due_day": 1}, {"name": "Phone", "amount": 45.50, "due_day": 9}],
            "monthly_spend": {"FOOD_AND_DRINK": 400, "TRANSPORTATION": 54.50},
            "buffer": 6000}
    b = money.buffer_months(plan)
    assert b == {"buffer_cents": 600000, "bills_cents": 154550, "targets_cents": 45450,
                 "monthly_cents": 200000, "months": 3.0}


def test_buffer_months_is_unknown_without_any_planned_costs():
    assert money.buffer_months({"bills": [], "monthly_spend": {}, "buffer": 500})["months"] is None
    assert money.buffer_months({"bills": [{"name": "Rent", "amount": 900, "due_day": 1}], "buffer": None}) == \
        {"buffer_cents": 0, "bills_cents": 90000, "targets_cents": 0, "monthly_cents": 90000, "months": 0.0}


def test_a_loan_counts_down_from_its_balance_with_each_payment_to_a_payoff_month():
    loan = bill("Synchrony", 176.99, 13, category="LOAN_PAYMENTS", balance=3832.62, balance_as_of="2026-11-08")
    paid = t(17699, "2026-11-13", "LOAN_PAYMENTS", "Synchrony Bank")
    before = t(17699, "2026-10-13", "LOAN_PAYMENTS", "Synchrony Bank")  # already in the balance

    p = money.loan_payoff(loan, [before], date(2026, 11, 8))
    # $3,832.62 / $176.99 = 21.65 -> 22 payments, Nov 13 2026 through Aug 13 2028
    assert (p["remaining_cents"], p["payments_left"], p["payoff"]) == (383262, 22, "2028-08-13")

    p = money.loan_payoff(loan, [before, paid], date(2026, 11, 20))
    assert (p["paid_cents"], p["remaining_cents"], p["payments_left"]) == (17699, 365563, 21)
    # 20 full payments from Dec 2026 leave $3,655.63 - $3,539.80 = $115.83 for the last one
    assert (p["next_due"], p["payoff"], p["last_payment_cents"]) == ("2026-12-13", "2028-08-13", 11583)


def test_a_loan_payment_a_few_days_late_still_counts_as_coming():
    loan = bill("Synchrony", 176.99, 13, category="LOAN_PAYMENTS", balance=3832.62, balance_as_of="2026-11-08")
    # Nov 13 has passed unpaid, but within the late window it is still the next payment, not a month added on
    p = money.loan_payoff(loan, [], date(2026, 11, 16))
    assert (p["payments_left"], p["next_due"], p["payoff"]) == (22, "2026-11-13", "2028-08-13")


def test_a_paid_off_loan_has_nothing_left():
    loan = bill("Phone", 100, 5, balance=150, balance_as_of="2026-09-01")
    p = money.loan_payoff(loan, [t(10000, "2026-09-05", merchant="Phone"), t(10000, "2026-10-05", merchant="Phone")],
                          date(2026, 10, 20))
    assert (p["remaining_cents"], p["payments_left"], p["payoff"]) == (0, 0, "2026-10-05")


def test_gains_add_up_what_each_fund_with_a_known_cost_is_up_and_skip_the_rest():
    doc = {"version": 1, "accounts": [{"name": "Dodge & Cox", "holdings": [
        {"symbol": "DODGX", "shares": 200, "cost_basis": 43428.09}, {"symbol": "DODFX", "shares": 100}]}]}
    prices = {"DODGX": [("2026-11-06", 311.20)], "DODFX": [("2026-11-06", 60.00)]}
    _, holdings = money.manual_holdings(doc, prices, None, date(2026, 11, 8))
    assert [h["cost_basis_cents"] for h in holdings] == [4342809, None]

    plaid = {**h("VTI", "etf", 600000), "cost_basis_cents": 500000}
    g = money.gains(holdings + [plaid])
    # DODGX 200 × $311.20 = $62,240 on $43,428.09; VTI $6,000 on $5,000
    assert (g["value_cents"], g["cost_basis_cents"], g["gain_cents"]) == (6824000, 4842809, 1981191)
    assert g["pct"] == 40.9  # $19,811.91 / $48,428.09


def test_this_months_spending_is_set_against_an_average_months_take_home_pay():
    pay = income(amount=2154.94)
    txns = [t(150000, "2026-11-02", "RENT_AND_UTILITIES", "Encore"), t(4000, "2026-11-07"),
            t(-215494, "2026-11-06", "INCOME", "DFAS"), t(9999, "2026-10-30")]  # last month's charge doesn't count
    s = money.spent_vs_pay(txns, pay, date(2026, 11, 8))
    # $2,154.94 × 26 paychecks ÷ 12 months = $4,669.04
    assert (s["take_home_cents"], s["spent_cents"], s["left_cents"], s["pct"]) == (466904, 154000, 312904, 33.0)
    assert money.spent_vs_pay(txns, None, date(2026, 11, 8)) is None


def test_months_with_an_extra_paycheck_are_the_ones_with_three_biweekly_paydays():
    pay = income(amount=2154.94)  # paydays Nov 6, Nov 20, Dec 4, ...
    months = money.extra_paycheck_months(pay, date(2026, 11, 8))
    assert [(m["month"], m["paydays"]) for m in months] == [
        ("2027-01", ["2027-01-01", "2027-01-15", "2027-01-29"]),
        ("2027-07", ["2027-07-02", "2027-07-16", "2027-07-30"])]
    assert months[0]["extra_cents"] == 215494
    assert money.extra_paycheck_months(income(schedule="semimonthly", days=[15, 31]), date(2026, 11, 8)) == []


def test_an_account_with_holdings_splits_into_cash_and_invested_and_any_balance_beyond_them_counts_as_cash():
    assert money.account_parts(CMA, HOLDINGS) == {"cash_cents": 300000, "invested_cents": 700000}
    assert money.account_parts({**CMA, "current_cents": 1050000}, HOLDINGS) == {"cash_cents": 350000,
                                                                                 "invested_cents": 700000}
    assert money.account_parts(CHECKING, HOLDINGS) is None

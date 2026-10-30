from datetime import date

from finance import money


def t(amount_cents, day, category="FOOD_AND_DRINK", merchant="Cafe", detailed=None, pending=False,
      account_key="bofa:1111", name=None, tid=None):
    return {"transaction_id": tid or f"{merchant}-{day}-{amount_cents}", "amount_cents": amount_cents, "date": day,
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


def test_typical_monthly_spend_averages_complete_months_in_history():
    txns = [t(50000, "2026-07-31"), t(100000, "2026-08-10"), t(200000, "2026-09-10"), t(999999, "2026-10-05")]
    # history starts Jul 31: August and September are the complete months; October is partial
    assert money.typical_monthly_spend(txns, date(2026, 10, 29)) == 150000
    assert money.typical_monthly_spend([], date(2026, 10, 29)) is None


# --- plan-driven: payday, bills, safe to spend

from finance.plan import EMPTY_PLAN  # noqa: E402


def income(schedule="biweekly", next_payday="2026-11-06", **kw):
    return {"name": "Pay", "amount": 2400, "schedule": schedule, "next_payday": next_payday, **kw}


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


def test_detect_recurring_accepts_a_single_charge_the_bank_marks_recurring():
    txns = [t(499, "2026-10-05", "ENTERTAINMENT", "Apple.com/bill", name="APPLE.COM/BILL RECURRING 866-712-7753")]
    [r] = money.detect_recurring(txns, date(2026, 10, 29))
    assert (r["cadence"], r["next_due"], r["count"]) == ("monthly", "2026-11-05", 1)


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
    li = money.left_to_invest([CHECKING, CARD], holdings, plan, upcoming, typical_monthly_cents=200000)

    # checking 1400 - card 350 - Car 420 - buffer 100 = 530; + idle 5000 = 5530
    assert li["checking_left_cents"] == 53000
    assert li["idle_cash_cents"] == 500000
    assert li["total_cents"] == 553000
    assert [(o["months"], o["amount_cents"]) for o in li["options"]] == [(0, 553000), (1, 353000), (3, 0)]
    assert li["missing"] == []


def test_left_to_invest_never_counts_a_checking_shortfall_and_flags_an_empty_plan():
    li = money.left_to_invest([CHECKING, a("9", "credit", "credit card", current=500000)], [], EMPTY_PLAN,
                              {"bills": []}, typical_monthly_cents=None)
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
    assert "Bank of America" in found[0]["title"] and "./run.sh link" in found[0]["detail"]
    assert "Merrill" in found[3]["title"]


def test_alerts_warn_when_consent_has_thirty_days_or_fewer():
    soon = item_row(consent_expires_at="2026-11-20T00:00:00Z")
    [x] = money.alerts([soon], {"bills": []}, [], NOW)
    assert (x["level"], x["kind"]) == ("warn", "consent_expiring") and "22 days" in x["title"]
    assert money.alerts([item_row()], {"bills": []}, [], NOW) == []

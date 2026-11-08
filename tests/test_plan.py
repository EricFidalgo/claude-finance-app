import json
from datetime import date

import pytest

from finance import plan

FULL = {
    "version": 1,
    "income": {"name": "Pay", "amount": 2400, "schedule": "biweekly", "next_payday": "2026-11-06", "account": "1111"},
    "bills": [{"name": "Rent", "amount": 1200, "due_day": 1, "account": "1111", "category": "RENT_AND_UTILITIES",
               "autopay": True, "fixed": True}],
    "monthly_spend": {"FOOD_AND_DRINK": 400},
    "buffer": 200,
    "goals": [{"name": "Emergency fund", "target": 6000, "start": "2026-10-01", "by": "2027-06-30"}],
    "reminders": [{"date": "2026-12-08", "title": "Check fund payouts", "detail": "Look up year-end estimates"}],
}


def test_missing_file_loads_as_the_empty_plan_and_ensure_writes_it(data_dir):
    assert plan.load() == plan.EMPTY_PLAN
    plan.ensure_file()
    assert json.loads((data_dir / "plan.json").read_text()) == plan.EMPTY_PLAN
    assert plan.validate(plan.EMPTY_PLAN) == []


def test_full_plan_round_trips(data_dir):
    plan.save(FULL, today=date(2026, 11, 8))
    assert plan.load() == FULL


def test_invalid_plan_is_rejected_with_every_problem_and_file_unchanged(data_dir):
    plan.save(FULL, today=date(2026, 11, 8))
    bad = json.loads(json.dumps(FULL))
    bad["income"]["schedule"] = "fortnightly"
    bad["income"]["next_payday"] = "11/06/2026"
    bad["bills"][0]["due_day"] = 32
    bad["bills"].append({"name": "", "amount": -5})
    bad["buffer"] = "lots"
    with pytest.raises(plan.PlanError) as err:
        plan.save(bad, today=date(2026, 11, 8))

    text = " | ".join(err.value.errors)
    for fragment in ["income.schedule", "income.next_payday", "bills[0].due_day", "bills[1].name",
                     "bills[1].amount", "bills[1].due_day", "buffer"]:
        assert fragment in text
    assert plan.load() == FULL


def test_optional_bill_fields_get_defaults():
    p = plan.normalize({**plan.EMPTY_PLAN, "bills": [{"name": "Gym", "amount": 30, "due_day": 5}]})
    assert p["bills"][0] == {"name": "Gym", "amount": 30, "due_day": 5, "account": None, "category": None,
                             "autopay": False, "fixed": True}


def test_a_new_goal_starts_saving_on_the_day_it_is_saved_and_keeps_that_start(data_dir):
    p = {**FULL, "goals": [{"name": "Vacation", "target": 1000, "by": "2027-01-15"}, FULL["goals"][0]]}
    saved = plan.save(p, today=date(2026, 11, 8))
    assert [g["start"] for g in saved["goals"]] == ["2026-11-08", "2026-10-01"]
    assert plan.save(saved, today=date(2026, 12, 1))["goals"][0]["start"] == "2026-11-08"

    # A goal with no date isn't saving yet, so its clock starts when it gets one.
    someday = plan.save({**FULL, "goals": [{"name": "Car", "target": 5000}]}, today=date(2026, 11, 8))
    assert "start" not in someday["goals"][0]
    dated = {**someday["goals"][0], "by": "2027-12-01"}
    assert plan.save({**FULL, "goals": [dated]}, today=date(2027, 2, 1))["goals"][0]["start"] == "2027-02-01"

    with pytest.raises(plan.PlanError) as err:
        plan.save({**FULL, "goals": [{"name": "Trip", "target": 1, "start": "soon"}]}, today=date(2026, 11, 8))
    assert err.value.errors == ["goals[0].start must be a date like 2026-11-08"]


def test_changing_a_goals_target_or_date_keeps_what_is_set_aside_and_restarts_from_today(data_dir):
    pay = {"amount": 2400, "schedule": "biweekly", "next_payday": "2026-11-06"}
    trip = {"name": "Vacation", "target": 1000, "by": "2027-01-15"}
    plan.save({**FULL, "income": pay, "goals": [trip]}, today=date(2026, 11, 8))  # $250 on 4 paydays from Nov 20
    # Dec 5: Nov 20 and Dec 4 have set aside $500. Raise it to $1,500.
    raised = plan.save({**FULL, "income": pay, "goals": [{**trip, "start": "2026-11-08", "target": 1500}]},
                       today=date(2026, 12, 5))["goals"][0]
    assert (raised["start"], raised["saved"]) == ("2026-12-05", 500)
    # Saving it again unchanged keeps it as is.
    assert plan.save({**FULL, "income": pay, "goals": [raised]}, today=date(2026, 12, 9))["goals"][0] == raised

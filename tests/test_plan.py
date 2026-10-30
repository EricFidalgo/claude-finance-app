import json

import pytest

from finance import plan

FULL = {
    "version": 1,
    "income": {"name": "Pay", "amount": 2400, "schedule": "biweekly", "next_payday": "2026-11-06", "account": "1111"},
    "bills": [{"name": "Rent", "amount": 1200, "due_day": 1, "account": "1111", "category": "RENT_AND_UTILITIES",
               "autopay": True, "fixed": True}],
    "monthly_spend": {"FOOD_AND_DRINK": 400},
    "buffer": 200,
    "goals": [{"name": "Emergency fund", "target": 6000, "by": "2027-06-30"}],
}


def test_missing_file_loads_as_the_empty_plan_and_ensure_writes_it(data_dir):
    assert plan.load() == plan.EMPTY_PLAN
    plan.ensure_file()
    assert json.loads((data_dir / "plan.json").read_text()) == plan.EMPTY_PLAN
    assert plan.validate(plan.EMPTY_PLAN) == []


def test_full_plan_round_trips(data_dir):
    plan.save(FULL)
    assert plan.load() == FULL


def test_invalid_plan_is_rejected_with_every_problem_and_file_unchanged(data_dir):
    plan.save(FULL)
    bad = json.loads(json.dumps(FULL))
    bad["income"]["schedule"] = "fortnightly"
    bad["income"]["next_payday"] = "11/06/2026"
    bad["bills"][0]["due_day"] = 32
    bad["bills"].append({"name": "", "amount": -5})
    bad["buffer"] = "lots"
    with pytest.raises(plan.PlanError) as err:
        plan.save(bad)

    text = " | ".join(err.value.errors)
    for fragment in ["income.schedule", "income.next_payday", "bills[0].due_day", "bills[1].name",
                     "bills[1].amount", "bills[1].due_day", "buffer"]:
        assert fragment in text
    assert plan.load() == FULL


def test_optional_bill_fields_get_defaults():
    p = plan.normalize({**plan.EMPTY_PLAN, "bills": [{"name": "Gym", "amount": 30, "due_day": 5}]})
    assert p["bills"][0] == {"name": "Gym", "amount": 30, "due_day": 5, "account": None, "category": None,
                             "autopay": False, "fixed": True}

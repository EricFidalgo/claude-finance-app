"""The owner's plan: plan.json, plain and hand-editable. Schema: docs/plan-schema.md.

Amounts in the file are dollars (what a person types). `money.to_cents()` brings them into the app's integer cents.
"""
import json
import math
import os
from datetime import date

from . import config, money

EMPTY_PLAN = {"version": 1, "income": None, "bills": [], "monthly_spend": {}, "buffer": None, "goals": []}
SCHEDULES = ("weekly", "biweekly", "semimonthly", "monthly")
BILL_DEFAULTS = {"account": None, "category": None, "autopay": False, "fixed": True}


class PlanError(ValueError):
    def __init__(self, errors):
        super().__init__("; ".join(errors))
        self.errors = errors


def _empty():
    return json.loads(json.dumps(EMPTY_PLAN))


def load() -> dict:
    path = config.plan_path()
    if not path.exists():
        return _empty()
    return normalize(json.loads(path.read_text()))


def ensure_file():
    if not config.plan_path().exists():
        _write(_empty())


def normalize(p: dict) -> dict:
    """Fill in optional fields so readers can rely on every key being present."""
    out = {**_empty(), **p}
    out["bills"] = [{**{k: b.get(k) for k in ("name", "amount", "due_day")}, **BILL_DEFAULTS,
                     **{k: v for k, v in b.items() if k in BILL_DEFAULTS and v is not None}} for b in out["bills"] or []]
    out["monthly_spend"] = out["monthly_spend"] or {}
    out["goals"] = out["goals"] or []
    return out


def _is_amount(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) and x >= 0  # 1e400 parses as inf


def _is_date(x):
    try:
        date.fromisoformat(x)
        return True
    except (TypeError, ValueError):
        return False


def validate(p) -> list:
    errors = []
    if not isinstance(p, dict):
        return ["plan must be a JSON object"]
    inc = p.get("income")
    if inc is not None:
        if not isinstance(inc, dict):
            errors.append("income must be an object or null")
        else:
            if not _is_amount(inc.get("amount")) or not inc.get("amount"):
                errors.append("income.amount must be a positive number of dollars")
            if inc.get("schedule") not in SCHEDULES:
                errors.append(f"income.schedule must be one of {', '.join(SCHEDULES)}")
            if not _is_date(inc.get("next_payday")):
                errors.append("income.next_payday must be a date like 2026-11-06")
            days = inc.get("days")
            if days is not None and not (isinstance(days, list) and len(days) == 2
                                         and all(isinstance(d, int) and 1 <= d <= 31 for d in days)):
                errors.append("income.days must be two days of the month, e.g. [15, 31]")
    if not isinstance(p.get("bills", []), list):
        errors.append("bills must be a list")
    else:
        for i, b in enumerate(p.get("bills") or []):
            if not isinstance(b, dict):
                errors.append(f"bills[{i}] must be an object")
                continue
            if not isinstance(b.get("name"), str) or not b["name"].strip():
                errors.append(f"bills[{i}].name is required")
            if not _is_amount(b.get("amount")):
                errors.append(f"bills[{i}].amount must be a number of dollars, 0 or more")
            if not (isinstance(b.get("due_day"), int) and not isinstance(b["due_day"], bool) and 1 <= b["due_day"] <= 31):
                errors.append(f"bills[{i}].due_day must be a day of the month, 1-31")
            for flag in ("autopay", "fixed"):
                if b.get(flag) is not None and not isinstance(b[flag], bool):
                    errors.append(f"bills[{i}].{flag} must be true or false")
    spend = p.get("monthly_spend") or {}
    if not isinstance(spend, dict) or not all(_is_amount(v) for v in spend.values()):
        errors.append("monthly_spend must map category names to dollar amounts")
    if p.get("buffer") is not None and not _is_amount(p["buffer"]):
        errors.append("buffer must be a number of dollars, 0 or more, or null")
    for i, g in enumerate(p.get("goals") or []):
        if not isinstance(g, dict) or not g.get("name") or not _is_amount(g.get("target")):
            errors.append(f"goals[{i}] needs a name and a target amount")
        elif g.get("by") is not None and not _is_date(g["by"]):
            errors.append(f"goals[{i}].by must be a date like 2027-06-30")
        elif g.get("start") is not None and not _is_date(g["start"]):
            errors.append(f"goals[{i}].start must be a date like 2026-11-08")
        elif g.get("saved") is not None and not _is_amount(g["saved"]):
            errors.append(f"goals[{i}].saved must be a number of dollars, 0 or more")
    return errors


def save(p: dict, today: date) -> dict:
    """Validate and write the plan, starting or restarting each goal's saving clock (see _started)."""
    errors = validate(p)
    if errors:
        raise PlanError(errors)
    p = normalize(p)
    before = load()
    p["goals"] = [_started(g, before, today) for g in p["goals"]]
    _write(p)
    return p


def _started(goal, before, today):
    """A dated goal without a start starts saving today; an undated one waits. A goal whose target or date changed
    keeps what it had set aside by today as `saved` and starts again from today, so only the rest is re-spread."""
    if not goal.get("by"):
        return {k: v for k, v in goal.items() if k not in ("start", "saved")}
    if not goal.get("start"):
        return {**goal, "start": today.isoformat()}
    old = next((g for g in before["goals"] if g.get("name") == goal["name"] and g.get("start") == goal["start"]), None)
    if old is None or (old.get("target"), old.get("by")) == (goal["target"], goal["by"]):
        return goal
    kept = money.goal_progress(old, before.get("income"), today)["set_aside_cents"]
    return {**goal, "start": today.isoformat(), "saved": kept / 100}


def _write(p):
    path = config.plan_path()
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(p, indent=2) + "\n")
    os.replace(tmp, path)

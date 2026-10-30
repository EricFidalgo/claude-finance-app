"""The owner's plan: data/plan.json, plain and hand-editable. Schema: docs/plan-schema.md.

Amounts in the file are dollars (what a person types); callers get the parsed dict as stored.
"""
import json
import os

from . import config

EMPTY_PLAN = {"version": 1, "income": None, "bills": [], "monthly_spend": {}, "buffer": None, "goals": []}


class PlanError(ValueError):
    def __init__(self, errors):
        super().__init__("; ".join(errors))
        self.errors = errors


def load() -> dict:
    path = config.plan_path()
    if not path.exists():
        return json.loads(json.dumps(EMPTY_PLAN))
    return json.loads(path.read_text())


def save(plan: dict) -> dict:
    path = config.plan_path()
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(plan, indent=2) + "\n")
    os.replace(tmp, path)
    return plan

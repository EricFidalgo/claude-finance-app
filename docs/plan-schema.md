# `plan.json`

The owner's plan: pay, bills, spending targets, buffer, goals, reminders. Plain JSON you can edit by hand, from the Plan screen, or with `PUT /api/plan`. The app writes an empty plan on first start and is useful with it; each field you fill in makes a number sharper.

Amounts are **dollars** (`1650` or `15.49`), not cents. Dates are `YYYY-MM-DD`.

## Example

```json
{
  "version": 1,
  "income": {
    "amount": 2450,
    "schedule": "semimonthly",
    "next_payday": "2026-10-31",
    "days": [15, 31]
  },
  "bills": [
    {"name": "Rent", "amount": 1650, "due_day": 1, "autopay": true, "fixed": true, "account": "1111", "category": "RENT_AND_UTILITIES"},
    {"name": "City Power", "amount": 70, "due_day": 18, "autopay": false, "fixed": false, "account": null, "category": null},
    {"name": "Furniture loan", "amount": 120, "due_day": 13, "balance": 2280, "balance_as_of": "2026-11-08"}
  ],
  "monthly_spend": {"FOOD_AND_DRINK": 700, "GENERAL_MERCHANDISE": 250},
  "buffer": 200,
  "goals": [{"name": "Vacation", "target": 1000, "start": "2026-11-08", "by": "2027-01-15"}],
  "reminders": [{"date": "2026-12-08", "title": "Check fund payout estimates", "detail": "Before sizing the sale."}]
}
```

## Fields

| Field | Type | Meaning |
|---|---|---|
| `version` | `1` | Schema version. |
| `income` | object or `null` | Take-home pay. `null` until you add it; safe-to-spend waits for it. |
| `income.amount` | dollars > 0 | One paycheck, after tax. |
| `income.schedule` | `weekly`, `biweekly`, `semimonthly`, `monthly` | How often you're paid. |
| `income.next_payday` | date | Any upcoming payday. The app rolls it forward by the schedule. |
| `income.days` | `[d1, d2]`, optional | Semimonthly only: the two days of the month. `31` means the last day. Default `[15, 31]`. |
| `bills` | list | Bills you pay each month. |
| `bills[].name` | text, required | Matched against transaction names to tell whether it's paid, so use the name the bank shows. |
| `bills[].amount` | dollars ≥ 0, required | The usual amount. |
| `bills[].due_day` | 1–31, required | Day of the month. Short months use their last day. |
| `bills[].autopay` | `true`/`false`, default `false` | Shown as a tag; informational. |
| `bills[].fixed` | `true`/`false`, default `true` | `false` for bills that vary (utilities): amount is shown as an estimate, a paid match ignores the amount, and it never gets a price-change flag. |
| `bills[].account` | text or `null` | Optional: account mask it's paid from. Informational. |
| `bills[].category` | text or `null` | Optional: Plaid category, e.g. `RENT_AND_UTILITIES`. |
| `bills[].balance` | dollars ≥ 0, optional | For a loan: what's left to pay on `balance_as_of`. Bills shows it counting down to a payoff month. Left out of the file when not set. |
| `bills[].balance_as_of` | date, with `balance` | The day the balance was read. Payments matched to the bill after it come off the balance; the rest fall on the coming due days at `amount`. The Plan screen sets it to today when you change the balance. |
| `monthly_spend` | `{category: dollars}` | Monthly spending targets per Plaid primary category (`FOOD_AND_DRINK`, `GENERAL_MERCHANDISE`, `TRANSPORTATION`, `ENTERTAINMENT`, …). Shown as target marks on Spending. |
| `buffer` | dollars ≥ 0 or `null` | Kept untouched in checking; subtracted from safe-to-spend and from "ready to invest". The Plan screen and the snapshot also show it as months of planned costs (all bills plus all spending targets). |
| `goals` | list | Optional. Money built up inside checking. Each needs `name` and `target` (dollars). |
| `goals[].by` | date, optional | When you need the money. Without it the goal sets nothing aside. On this date the money is freed back into safe-to-spend. |
| `goals[].start` | date, optional | When saving began. Filled in by the app; leave it alone. |
| `goals[].saved` | dollars ≥ 0, optional | Already set aside on `start`. Filled in by the app when you change a goal. |
| `reminders` | list | Optional. Each needs `date` and `title`; `detail` is optional text. From its date on, a reminder shows under Needs attention on Today (and in the snapshot) until dismissed; before it, the next three show under Coming up on Today and the snapshot lists it with the plan. |

Each goal sets aside an equal slice of what's left (`target` − `saved`) on every payday after `start`, up to (not on) `by`. What it has set aside so far is subtracted from safe-to-spend and from "ready to invest", like the buffer. Saving a goal whose `target` or `by` changed keeps what it had set aside by today as `saved` and moves `start` to today, so only the rest is spread over the paydays left. A goal is matched to its earlier version by `name` and `start`.

A save that fails validation is rejected whole, with one message per problem (for example `bills[0].due_day must be a day of the month, 1-31`), and the file is left as it was.

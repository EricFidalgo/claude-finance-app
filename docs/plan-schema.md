# `plan.json`

The owner's plan: pay, bills, spending targets, buffer, goals. Plain JSON you can edit by hand, from the Plan screen, or with `PUT /api/plan`. The app writes an empty plan on first start and is useful with it; each field you fill in makes a number sharper.

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
    {"name": "City Power", "amount": 70, "due_day": 18, "autopay": false, "fixed": false, "account": null, "category": null}
  ],
  "monthly_spend": {"FOOD_AND_DRINK": 700, "GENERAL_MERCHANDISE": 250},
  "buffer": 200,
  "goals": [{"name": "Emergency fund", "target": 15000, "by": "2027-06-30"}]
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
| `monthly_spend` | `{category: dollars}` | Monthly spending targets per Plaid primary category (`FOOD_AND_DRINK`, `GENERAL_MERCHANDISE`, `TRANSPORTATION`, `ENTERTAINMENT`, …). Shown as target marks on Spending. |
| `buffer` | dollars ≥ 0 or `null` | Kept untouched in checking; subtracted from safe-to-spend and from "ready to invest". |
| `goals` | list | Optional. Each needs `name` and `target` (dollars); `by` is an optional date. |

A save that fails validation is rejected whole, with one message per problem (for example `bills[0].due_day must be a day of the month, 1-31`), and the file is left as it was.

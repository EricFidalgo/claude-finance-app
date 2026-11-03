# `data/manual.json`

Investments Plaid can't link (a fund company, the TSP), typed in by hand. Edit the file, or `PUT /api/manual` with the whole document; there is no screen for it. Missing file means no manual investments.

You enter **shares**, or a **balance in dollars** for an account that reports one (the TSP). The app fetches daily prices and values them like linked holdings: they count in net worth, allocation, the Investing screen and the snapshot.

## Example

```json
{
  "version": 1,
  "accounts": [
    {"name": "Fidelity", "holdings": [
      {"symbol": "FSPSX", "name": "Fidelity International Index", "shares": 120.5},
      {"symbol": "FXAIX", "name": "Fidelity 500 Index", "shares": 85.25}
    ]},
    {"name": "TSP", "holdings": [
      {"symbol": "TSP:C", "name": "TSP C Fund", "balance": 5000, "as_of": "2026-10-30", "per_paycheck": 250}
    ]}
  ]
}
```

## Fields

| Field | Type | Meaning |
|---|---|---|
| `version` | `1` | Schema version. |
| `accounts` | list | One entry per account. Each shows on the Money screen as institution "Manual". |
| `accounts[].name` | text, required | Shown as the account name. Must be unique (ignoring case and punctuation). |
| `accounts[].holdings` | list, at least one | What the account holds. |
| `holdings[].symbol` | text, required | A Yahoo Finance ticker (`FSPSX`, `BRK-B`), or a TSP fund: `TSP:G`, `TSP:F`, `TSP:C`, `TSP:S`, `TSP:I`, `TSP:L Income`, `TSP:L 2050` and the other L years. |
| `holdings[].name` | text, optional | Shown under the ticker. Defaults to the symbol. |
| `holdings[].shares` | number > 0 | Shares held. Use this **or** `balance`. |
| `holdings[].balance` | dollars ≥ 0 | The account's value on `as_of`; converted to shares at that day's price. |
| `holdings[].as_of` | date | Required with `balance`. |
| `holdings[].per_paycheck` | dollars ≥ 0, optional | With `balance` only: the contribution each payday (your part plus any match). |

## How values are worked out

- **Prices**: the latest daily close, from Yahoo Finance for tickers and from tsp.gov's price history for TSP funds. They are fetched when the file is saved through the API and after every sync (each 6 hours). A mutual fund posts its price after the market closes, so during the day the value is the previous close; the Investing screen says which day.
- **Shares**: a `shares` holding is worth shares × the latest price.
- **Balance**: `balance ÷ price on as_of` shares, plus `per_paycheck ÷ that day's price` for each payday in the plan (`data/plan.json` income) after `as_of`, up to today. Paydays come from the plan's `income.next_payday` and schedule, counted backwards too when `as_of` is earlier; with no income in the plan, nothing accrues.
- **Distributions**: a fund that pays a dividend or capital gain buys more shares if you reinvest, and the app can't see that. Update `shares` from your statement (each December for most funds).
- A holding with no price yet shows "—" and counts as $0 until one arrives. A source that's down leaves the last prices in place.

Fetching prices sends Yahoo each fund's symbol and a date range; tsp.gov's download is the same for everyone. Shares and balances stay on this computer.

A save that fails validation is rejected whole, with one message per problem (for example `accounts[1].holdings[0] needs shares, or a balance and as_of`), and the file is left as it was. A field the table doesn't list is a problem too, so a misspelt `per_paycheck` can't be dropped unnoticed. A successful `PUT` answers `{"manual": <the saved document>, "prices": {symbol: {"date", "price"} or {"error"}}}`.

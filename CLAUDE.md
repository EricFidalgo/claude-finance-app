# Finance tracker

The owner's private finance app: Plaid (read-only) → SQLite → a phone PWA at http://127.0.0.1:8787, reached from the phone through `tailscale serve`. Owner docs: `README.md`. Why things are built this way: `docs/decisions.md`.

## Guardrails

- **Read-only.** Request only the `transactions` and `investments` Plaid products, so no code path can move money.
- **Secrets stay in env vars.** Tokens and keys appear in no log, error, commit, image layer or API response; the browser receives data, never tokens. `data/items.json` holds the access tokens and stays unopened.
- **Real data stays local.** Tests and screenshots use fixtures, Plaid Sandbox or the demo server. Against the real app, run counts and automated checks (scrollWidth, JS errors), and read real transactions only when the owner asks a question about them.
- **Exposure is 127.0.0.1 plus `tailscale serve`.** Funnel and public ports stay off.
- **The owner runs `./run.sh`.** It reads the Plaid keys; you edit it and leave executing it to them.

## Answering a finance question

1. `curl -s http://127.0.0.1:8787/api/snapshot` for the overview (pass `?screen=spending` etc. to focus it).
2. Drill in with the endpoint that owns the answer: `/api/spending?period=today|week|last_week|month|last_month`, `/api/transactions?start=YYYY-MM-DD&q=text`, `/api/bills`, `/api/accounts`, `/api/holdings`, `/api/alerts`, `/api/plan`.
3. Answer with the arithmetic shown: which transactions or balances, and how they add up. Amounts in the API are integer cents.

To change the plan, `PUT /api/plan` with the whole plan (schema: `docs/plan-schema.md`, amounts in dollars). A 400 lists every problem; fix and resend.

## Money conventions

- **Plaid's sign:** positive is money out, negative is money in. Amounts are integer cents everywhere except `plan.json`.
- **Spending** excludes income, transfers between own accounts and credit-card payments; refunds net in; pending counts.
- **Accounts dedupe** on `family:mask` (`bofa:2222`): Merrill and BoA both report CMA-Edge, and it counts once.
- **Safe-to-spend** = (checking available − unpaid plan bills due before next payday − card owed incl. pending − buffer) ÷ days to payday. It stays `needs_plan` until income and bills exist.

## Working on the code

- Tests: `.venv/bin/pytest -q -m "not sandbox"`. Money logic lives in `finance/money.py` as pure functions over rows; test it there.
- Demo server on made-up data, for UI work and screenshots: `.venv/bin/python -m tests.demo [port]` (default 8790).
- **Host allowlist:** requests with a Host outside localhost, 127.0.0.1, `*.ts.net` get a 400. A Starlette `TestClient` sends `testserver`, so set `FINANCE_EXTRA_HOSTS=testserver` (conftest does this for the suite).
- UI is plain JS in `finance/static/`, no build step. Build DOM with `textContent`; bank and merchant strings are untrusted.

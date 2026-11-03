# Finance tracker

The owner's private finance app: Plaid (read-only) → SQLite → a phone PWA at http://127.0.0.1:8787, reached from the phone through `tailscale serve`. Owner docs: `README.md`. Why things are built this way: `docs/decisions.md`.

## Guardrails

- **Read-only.** Request only the `transactions` and `investments` Plaid products, so no code path can move money.
- **Secrets stay in env vars.** Tokens and keys appear in no log, error, commit, image layer or API response; the browser receives data, never tokens. `items.json` in the data folder holds the access tokens and stays unopened. Inspect compose with `docker compose config --no-interpolate`: plain `config` prints the Plaid keys the SessionStart hook loaded.
- **Real data stays in the data folder** (`~/.local/share/finance-tracker`, outside the repo); the repo holds code, docs and fixtures. Tests and screenshots use fixtures, Plaid Sandbox or the demo server. Against the real app, run counts and automated checks (scrollWidth, JS errors), and read real transactions only when the owner asks a question about them.
- **Exposure is 127.0.0.1 plus `tailscale serve`.** Funnel and public ports stay off.
- **The owner runs `./run.sh`.** It reads the Plaid keys; you edit it and leave executing it to them.

## Answering a finance question

1. `curl -s http://127.0.0.1:8787/api/snapshot` for the overview (pass `?screen=spending` etc. to focus it).
2. Drill in with the endpoint that owns the answer: `/api/spending?period=today|week|pay_period|last_week|month|last_month` (or `?start=YYYY-MM-DD&end=YYYY-MM-DD`), `/api/transactions?period=…` (or `start`/`end`, plus `q=text`, `category=`, `merchant=` (exact name); returns `spent_cents` and marks each row `spending: true|false`), `/api/bills`, `/api/accounts`, `/api/holdings`, `/api/alerts`, `/api/plan`.
3. Answer with the arithmetic shown: which transactions or balances, and how they add up. Amounts in the API are integer cents.

To change the plan, `PUT /api/plan` with the whole plan (schema: `docs/plan-schema.md`, amounts in dollars). To change hand-entered investments (share counts, the TSP balance), `GET /api/manual`, edit, and `PUT` the whole document back (schema: `docs/manual-schema.md`); its response carries each symbol's fresh price or fetch error. Either PUT answers a bad document with a 400 listing every problem; fix and resend.

## Money conventions

- **Plaid's sign:** positive is money out, negative is money in. Amounts are integer cents everywhere except per-share prices and the dollars in `plan.json` and `manual.json`.
- **Spending** excludes income, transfers between own accounts and credit-card payments; refunds net in; pending counts.
- **Accounts dedupe** on `family:mask` (`bofa:2222`): Merrill and BoA both report CMA-Edge, and it counts once.
- **Safe-to-spend** = (checking available − unpaid plan bills due before next payday − card owed incl. pending − buffer) ÷ days to payday. It stays `needs_plan` until income and bills exist.
- **Left to spend today** = allowance − spent today, where allowance = (safe-to-spend pool + spent today) ÷ days to payday, so it holds still through the day. Spent today leaves out plan-bill payments. Rent, plan bills, card owed and buffer are already out; the allowance covers everything else.

## Working on the code

- Tests: `.venv/bin/pytest -q -m "not sandbox"`. Money logic lives in `finance/money.py` as pure functions over rows; test it there.
- Demo server on made-up data, for UI work and screenshots: `.venv/bin/python -m tests.demo [port]` (default 8790).
- **Host allowlist:** requests with a Host outside localhost, 127.0.0.1, `*.ts.net` get a 400. A Starlette `TestClient` sends `testserver`, so set `FINANCE_EXTRA_HOSTS=testserver` (conftest does this for the suite).
- UI is plain JS in `finance/static/`, no build step. Build DOM with `textContent`; bank and merchant strings are untrusted.

# Decisions

One line each: what was chosen and why.

## Stack

- **Python 3.12, SQLite, Starlette, uvicorn.** Small, no ORM, one process; the Plaid prototypes were already Python.
- **Scheduler inside the app** (a background task every 6 hours, 5 minutes while Plaid says PRODUCT_NOT_READY). One container, nothing else to run.
- **Frontend: plain JS, CSS and HTML in `finance/static/`, no build step.** Six screens don't need a framework, and nothing has to be installed to change one.
- **No service worker.** Stale balances served from a cache are worse than "can't reach the server"; the manifest alone makes it installable.
- **Polling, no webhooks.** A webhook needs a public URL, and the app has none on purpose.

## Money

- **Integer cents everywhere inside the app; dollars only in `data/plan.json`** (what a person types). Plaid's floats go through `Decimal` once, at sync.
- **Plaid's sign is kept: positive is money out, negative is money in.** Flipping it in one place and not another is the classic bug; keeping the source's sign avoids that.
- **Spending excludes income, transfers between own accounts and card payments; refunds net in; pending counts.** Otherwise paying the card bill would count every purchase twice.
- **Accounts are keyed by institution family + mask** (`bofa:2222`). Merrill and Bank of America share a login and both report CMA-Edge, so it counts once in net worth.
- **Safe-to-spend = (checking available − plan bills due before next payday and not yet paid − card balance owed − buffer) ÷ days to payday.** It shows a "fill in your plan" prompt, not a guess, until income and bills exist.
- **Card owed includes pending charges** (limit − available when the bank reports both), because that is what the next statement will ask for.

## Recurring charges and flags

- **A fixed series is ≥3 charges of the same merchant within 5% on a weekly, biweekly or monthly cadence; 2 are enough only when they are all the merchant's charges.** Among a restaurant's dozens of charges, two similar amounts a month apart are chance.
- **A variable series (utilities) needs 3 charges on cadence at any amount.**
- **A series follows a price change**: the next charge within ±5 days of the expected date continues it, so a price rise shows as a flag, not a new subscription.
- **A plan bill marked `fixed: false` never gets a price-change flag**, only "higher than usual" (>1.5× its median).

## Security and exposure

- **Read-only products only** (`transactions`, `investments`). No code path can move money.
- **Bound to 127.0.0.1; phone access via `tailscale serve`** (HTTPS inside the tailnet). Never Funnel, never a public port.
- **Host allowlist** (localhost, 127.0.0.1, `*.ts.net`). A web page that points its own domain at 127.0.0.1 (DNS rebinding) gets a 400 instead of balances. Extra names via `FINANCE_EXTRA_HOSTS`.
- **CSP `default-src 'self'`, `frame-ancestors 'none'`, `Cache-Control: no-store` on `/api/`.** No outside scripts, no framing, no balances in a browser cache.
- **Bank names and merchant strings are untrusted text**: the UI builds the DOM with `textContent` only, never `innerHTML`.
- **Keys reach the container by name only** (`run.sh` exports them, compose passes them through). Tokens live in `data/items.json` (mode 600, gitignored, not in the image).
- **Linking runs at `http://localhost:8765`** because Plaid Link in Production refuses other plain-http hosts.
- **No in-app chat with the Anthropic API.** It would send financial data to a third party. "Ask Claude" copies a text snapshot to the clipboard instead; the owner decides where to paste it.

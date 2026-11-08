# Finance tracker

A private finance app that runs on your own computer. It reads your bank and brokerage accounts through Plaid (Bank of America and Merrill are handled, including the CMA-Edge account both report), and you open it on your phone over Tailscale.

It is read-only: it asks Plaid for `transactions` and `investments` only, so no code path can move money.

Investments Plaid can't see (a fund company account, the TSP) are typed into `manual.json` in your data folder and priced daily from Yahoo Finance and tsp.gov; see [Investments you enter by hand](#investments-you-enter-by-hand).

Screens: **Today** (what's left to spend today; what you've spent today, this week and this pay period; how your savings goals are building up; anything that needs you; your latest transactions and a few tips), **Spending** (by category, against last period), **Transactions** (any period or dates, with search; tap a total, a category or a merchant to get there), **Bills** (a calendar of paydays and bills, and the schedule), **Money** (accounts, net worth, connections), **Investing** (value over time, where it could grow, holdings), **Plan**, and **Ask Claude** (copies a text summary to paste into a chat).

**Left to spend today** is your daily share of the money that's free until payday. Rent and the other plan bills due before payday, your card balance, your buffer and what your savings goals have put aside so far are taken out first, and what's left is split across the days to payday. That daily amount covers everything else: groceries, eating out, gas, fun. The bar fills as you spend today; spend less and tomorrow's share grows, spend more and it shrinks.

## Setup (once)

1. **Start it.** In this folder, run `./run.sh`. It builds the container and waits until http://127.0.0.1:8787 answers. Your Plaid keys are read from `~/.claude-secrets/env/` at that moment and never written anywhere.
2. **Put it on your tailnet.** Run `tailscale serve --bg 8787`, then `tailscale serve status` to see the address (`https://ubuntu.your-tailnet.ts.net/`). This only uses HTTPS port 443, so it leaves any other serve ports alone. If it says HTTPS certificates are off, turn them on under DNS in the Tailscale admin console. If it needs root, run `sudo tailscale set --operator=$USER` once. Never use `tailscale funnel` for this app.
3. **Set the phone password.** Run `./run.sh password` and type it twice: 6 or more numbers, so the phone shows its number pad. Until you do, the app on the phone shows a lock screen asking for it. This computer never needs it.
4. **Install it on your iPhone.** With Tailscale connected, open that address in Safari, then Share → Add to Home Screen. Unlock with the password; it then offers Face ID for next time.
5. **Link your banks.** On the Money screen, under Connections, tap **Add bank or card** (checking, savings, credit cards) or **Add brokerage** (investment and retirement accounts) and sign in through Plaid's window. Linking a bank's login once brings in every account it shares.
6. **Fill in your plan.** On the Plan screen enter your take-home pay, next payday, bills and buffer; or ask Claude in this folder to fill it in from what you tell it. Until pay and bills are in, Today asks for them instead of guessing. The format is in [docs/plan-schema.md](docs/plan-schema.md).

## Day to day

The app syncs every 6 hours on its own. Pull the latest from the Today screen, or run `./run.sh sync`.

| Command | What it does |
|---|---|
| `./run.sh` | Build and start (also after pulling code changes) |
| `./run.sh sync` | Sync with Plaid now |
| `./run.sh logs` | Follow the logs |
| `./run.sh down` | Stop it; your data stays |
| `./run.sh password` | Set a new phone password. Signs out the phone and turns Face ID off; use it if you're locked out |

The phone asks for Face ID (or the password) when you open the app, and again after 5 minutes away.

## Investments you enter by hand

`manual.json` lists accounts by share count, or for the TSP by balance plus what goes in each paycheck. Prices refresh after every sync (a year of them, for the Investing chart; your brokerage tickers like VTI go to Yahoo for this too), and the TSP's contributions are added on each payday in your plan. When your share counts change, or you see a new TSP balance, ask Claude in this folder to update it, or edit the file by hand. The format is in [docs/manual-schema.md](docs/manual-schema.md).

## Banks: add, fix, remove

Everything happens on the Money screen under **Connections**, on the phone or at http://localhost:8787 on this computer.

- **Add**: *Add bank or card*, or *Add brokerage*. The first sync runs straight away; a bank Plaid is still preparing shows up within a few hours.
- **Fix login**: when a login breaks, or Bank of America's yearly consent (about 12 months) gets close, Today warns you and the bank's button turns into **Fix login**. Sign in again in Plaid's window; your history is kept and the countdown restarts. The same button (*Change accounts* while all is well) lets you pick which accounts the bank shares.
- **Remove**: tells Plaid to stop sharing that bank and deletes its token, accounts, transactions and holdings from the app. An account another linked bank also reports (CMA-Edge, which both Bank of America and Merrill report) stays.

If a bank's sign-in sends you off to its own site and never comes back to the app on the phone, do that one from the browser on this computer instead.

## Your data

Everything lives in `~/.local/share/finance-tracker` on this computer, outside this folder: `finance.db` (transactions, balances, holdings), `items.json` (Plaid access tokens), `plan.json`, `manual.json` and `lock.json` (the phone password and passkeys). The folder is readable only by you, and none of it is in this repo or in the Docker image, so committing or pushing the code can't carry any of it. Set `FINANCE_DATA` before `./run.sh` to keep it elsewhere. Delete the folder to forget everything.

Earlier versions kept it in `data/` inside this folder; the first `./run.sh` after updating moves it out.

## For developers

- Tests: `.venv/bin/pytest -q -m "not sandbox"` (fixtures only). `-m sandbox` also calls Plaid Sandbox and needs `PLAID_SANDBOX_SECRET`.
- To try linking against Plaid's test banks instead of real ones, run with `FINANCE_PLAID_ENV=sandbox` (needs `PLAID_SANDBOX_SECRET`; log in as `user_good` / `pass_good`).
- Demo on made-up data: `.venv/bin/python -m tests.demo [port]`, default http://127.0.0.1:8790.
- Why things are the way they are: [docs/decisions.md](docs/decisions.md).

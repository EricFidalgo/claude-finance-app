# Finance tracker

A private finance app that runs on this computer. It reads Bank of America (checking …1111, credit card …3333) and Merrill (CMA-Edge …2222) through Plaid, and you open it on your phone over Tailscale.

It is read-only: it asks Plaid for `transactions` and `investments` only, so no code path can move money.

Investments Plaid can't see (Fidelity, Vanguard, the TSP) are typed into `data/manual.json` and priced daily from Yahoo Finance and tsp.gov; see [Investments you enter by hand](#investments-you-enter-by-hand).

Screens: **Today** (what's left to spend today; what you've spent today, this week and this pay period; anything that needs you; your latest transactions and a few tips), **Spending** (by category, against last period), **Transactions** (any period or dates, with search; tap a total, a category or a merchant to get there), **Bills** (a calendar of paydays and bills, and the schedule), **Money** (accounts, net worth, connections), **Investing** (value over time, where it could grow, holdings), **Plan**, and **Ask Claude** (copies a text summary to paste into a chat).

**Left to spend today** is your daily share of the money that's free until payday. Rent and the other plan bills due before payday, your card balance and your buffer are taken out first, and what's left is split across the days to payday. That daily amount covers everything else: groceries, eating out, gas, fun. The bar fills as you spend today; spend less and tomorrow's share grows, spend more and it shrinks.

## Setup (once)

1. **Start it.** In this folder, run `./run.sh`. It builds the container and waits until http://127.0.0.1:8787 answers. Your Plaid keys are read from `~/.claude-secrets/env/` at that moment and never written anywhere.
2. **Put it on your tailnet.** Run `tailscale serve --bg 8787`, then `tailscale serve status` to see the address (`https://ubuntu.your-tailnet.ts.net/`). This only uses HTTPS port 443, so it leaves your existing serve on `:4533` alone. If it says HTTPS certificates are off, turn them on under DNS in the Tailscale admin console. If it needs root, run `sudo tailscale set --operator=$USER` once. Never use `tailscale funnel` for this app.
3. **Install it on your iPhone.** With Tailscale connected, open that address in Safari, then Share → Add to Home Screen.
4. **Fill in your plan.** On the Plan screen enter your take-home pay, next payday, bills and buffer; or ask Claude in this folder to fill it in from what you tell it. Until pay and bills are in, Today asks for them instead of guessing. The format is in [docs/plan-schema.md](docs/plan-schema.md).

## Day to day

The app syncs every 6 hours on its own. Pull the latest from the Today screen, or run `./run.sh sync`.

| Command | What it does |
|---|---|
| `./run.sh` | Build and start (also after pulling code changes) |
| `./run.sh sync` | Sync with Plaid now |
| `./run.sh logs` | Follow the logs |
| `./run.sh down` | Stop it; your data in `data/` stays |
| `./run.sh link` | Open the Plaid Link page at http://localhost:8765 on this computer |

## Investments you enter by hand

`data/manual.json` lists accounts by share count, or for the TSP by balance plus what goes in each paycheck. Prices refresh after every sync (a year of them, for the Investing chart; your brokerage tickers like VTI go to Yahoo for this too), and the TSP's contributions are added on each payday in your plan. When your share counts change, or you see a new TSP balance, ask Claude in this folder to update it, or edit the file by hand. The format is in [docs/manual-schema.md](docs/manual-schema.md).

## Re-linking

Bank of America's consent for Plaid lasts about 12 months. The Money screen shows the date under Connections, and Today warns you as it gets close or if a login breaks. To renew, run `./run.sh link` and open http://localhost:8765 in a browser **on this computer** (Plaid only allows plain-http links on localhost). Choose the bank's "Fix login or change accounts" button; your history is kept and the countdown restarts.

## Your data

Everything lives in `data/` on this computer: `finance.db` (transactions, balances, holdings), `items.json` (Plaid access tokens, readable only by you), `plan.json` and `manual.json`. None of it is in git or in the Docker image. Back it up the way you back up anything private, and delete `data/` to forget everything.

## For developers

- Tests: `.venv/bin/pytest -q -m "not sandbox"` (fixtures only). `-m sandbox` also calls Plaid Sandbox and needs `PLAID_SANDBOX_SECRET`.
- Demo on made-up data: `.venv/bin/python -m tests.demo [port]`, default http://127.0.0.1:8790.
- Why things are the way they are: [docs/decisions.md](docs/decisions.md).

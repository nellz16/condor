# MemeScout AI — beginner-safe Solana memecoin research

MemeScout AI is a **paper-only** Condor routine for researching high-risk/high-reward Solana memecoins from Telegram. It is designed for a beginner with no laptop and no trading knowledge.

## Safety first

- **No real trading in Phase 1.** Approve buttons open simulated paper buys only.
- **No private key required.** Do not paste wallet seed phrases or private keys into Condor.
- **No exchange API key required.** MemeScout uses public DEX Screener data first.
- **Default paper balance:** 100 USDC.
- **Emergency stop:** `/memescout_emergency_stop` blocks new signals and paper trades.
- **No profit guarantee.** Memecoins are extremely risky. This system is for learning and research only.

## Telegram commands

- `/memescout` — show the MemeScout control menu.
- `/memescout_status` — show scanner state, paper balance, and open paper trades.
- `/memescout_signals` — show recent stored signals.
- `/memescout_pnl` — show paper PnL, win rate, drawdown, best trade, and worst trade.
- `/memescout_daily` — show a compact daily summary.
- `/memescout_pause` — pause scans.
- `/memescout_resume` — resume scans in paper-only mode.
- `/memescout_emergency_stop` — immediately block new signals and paper trades.

You can also run the continuous `memescout_ai` routine from `/routines`.

## How signals work

MemeScout pulls Solana pair data from the DEX Screener API, stores features in SQLite, and runs a deterministic score. The LLM, if configured, can only explain the score in beginner-friendly language. It does **not** decide trades by itself.

Signal fields include token symbol, token mint, pair address, token age, liquidity, market cap, 5m/1h volume, buy/sell count, price change, slippage estimate, rug risk score, graduation probability score, expected upside range, max loss plan, take profit plan, and a beginner explanation.

## Paper wallet rules

- Starting balance: 100 USDC.
- Default simulated buy size: 10 USDC.
- Slippage is included in simulated entry.
- Default stoploss: -35%.
- Take profit plan: sell 50% at 2x, sell 25% at 4x, then trail a stop for the rest.
- PnL, win rate, drawdown, best trade, and worst trade are tracked locally.

## Optional Gemini explainer

Add these to `.env` if you want Gemini explanations:

```env
LLM_PROVIDER=gemini
GEMINI_API_KEY=your_key_here
GEMINI_MODEL=gemini-2.5-flash-lite
```

If no LLM key is configured, MemeScout still runs in rule-based mode.

## Mobile-only workflow

### 1. Edit from a phone with GitHub Codespaces

1. Open your fork on GitHub from your phone browser.
2. Start a Codespace.
3. Edit `.env` from `.env.example`.
4. Commit and push changes from the Codespaces terminal.

### 2. Run 24/7 on an Azure Ubuntu VM

1. Create an Ubuntu VM in Azure.
2. SSH into the VM from Azure Cloud Shell or a mobile SSH app.
3. Install Docker and Git.
4. Clone your Condor fork.
5. Copy `.env.example` to `.env` and fill in `TELEGRAM_TOKEN` and `ADMIN_USER_ID`.
6. Start Condor and control MemeScout from Telegram.

### 3. Control from Telegram

Use `/memescout` for the menu. When a signal arrives, tap **Approve paper buy** or **Reject**. Approve never sends a real order.

## Docker quick start

```bash
cp .env.example .env
# edit .env with TELEGRAM_TOKEN and ADMIN_USER_ID
docker build -t condor-memescout .
docker run --env-file .env -v $(pwd)/data:/app/data condor-memescout
```

If this repository uses Docker Compose in your deployment, keep the same `.env` values and mount `./data` so SQLite persists across restarts.

## Learning loop

MemeScout stores every signal, feature snapshot, rejection, and paper trade. Every 50 completed paper trades it can generate a conservative learning report with before/after scoring weights. It avoids overfitting to one jackpot trade and requires at least 200 paper trades before live mode should even be discussed.

## Final warning

MemeScout AI cannot guarantee profit. It can lose the entire paper balance in simulation, and real memecoin trading can lose real money quickly. Phase 1 is intentionally paper-only.


## Phase 1.5 paper position monitor

MemeScout includes a separate paper-only position monitor routine named `memescout_position_monitor`. It watches only MemeScout paper positions, fetches latest read-only prices from DEX Screener, and records simulated exits in SQLite. It never calls `/trade`, `/swap`, Gateway, wallet signing, or Hummingbot order endpoints.

Paper exit rules:

- Stoploss: sell the remaining simulated position at -35% by default.
- TP1: sell 50% at 2x.
- TP2: sell 25% at 4x.
- Trailing stop: remaining 25% trails after 2x; default trailing stop is 30%.
- Emergency stop blocks new paper buys. Risk-reducing paper closes remain allowed by default with `MEMESCOUT_ALLOW_RISK_REDUCING_CLOSES_DURING_EMERGENCY=true`.

Additional commands:

- `/memescout_positions` — list paper positions.
- `/memescout_position <id>` — show one paper position and exit flags.
- `/memescout_force_close_paper <id>` — paper-only close at the latest DEX Screener price.

Monitor configuration:

```env
MEMESCOUT_MONITOR_ENABLED=true
MEMESCOUT_MONITOR_INTERVAL_SECONDS=60
MEMESCOUT_TRAILING_STOP_PCT=30
MEMESCOUT_ALLOW_RISK_REDUCING_CLOSES_DURING_EMERGENCY=true
```

## Runtime smoke test checklist

From a fresh clone, run the automated fixture smoke test first:

```bash
PYTHONPATH=. python scripts/memescout_smoke_check.py
```

Optional read-only live DEX Screener check:

```bash
PYTHONPATH=. python scripts/memescout_smoke_check.py --live-dex
```

Manual Telegram checklist:

1. Start the bot and confirm it logs in without errors.
2. Send `/memescout` and confirm the control menu appears.
3. Send `/memescout_status` and confirm it says `PAPER ONLY`.
4. Send `/memescout_pause`, then `/memescout_status`; status/PnL should still work while scanning is paused.
5. Send `/memescout_resume`.
6. Use `/routines` to start `memescout_ai`, or tap `Scan now` from `/memescout`.
7. When a signal appears, tap **Approve paper buy** and confirm it says `Paper buy opened only`.
8. On another signal, tap **Reject** and confirm no trade is opened.
9. Send `/memescout_positions` and `/memescout_position <id>` to inspect paper position state.
10. Send `/memescout_pnl` and verify realized/unrealized paper PnL appears.
11. Send `/memescout_daily` and verify performance metrics appear.
12. Send `/memescout_force_close_paper <id>` to verify paper-only force close.
13. Send `/memescout_emergency_stop`; Approve should now be blocked.
14. Restart the bot and send `/memescout_status`; SQLite signal/trade state should still be present.

## Safety audit notes

MemeScout code is intentionally isolated from Condor real execution paths. The MemeScout package and handlers do not import `/trade`, `/swap`, Gateway execution modules, Hummingbot Backend API order clients, wallet private-key loaders, or transaction-signing helpers. Approval calls `approve_paper_buy`, which writes a row to SQLite only. Rejection updates signal status only.

## Strategy IDs

MemeScout classifies paper-only candidates by deterministic strategy before any optional LLM explanation:

- `fresh_launch`
- `momentum_continuation`
- `pullback_reentry`
- `liquidity_expansion`
- `boost_anomaly`
- `rug_defense_only`

Duplicates are suppressed per strategy. A token previously seen as `momentum_continuation` can later be considered as `pullback_reentry` if the deterministic rules match that setup. `rug_defense_only` never sends approval signals; it stores risk/rejection information for learning.

Admin strategy controls:

```text
/memescout_strategies
/memescout_strategy_enable <strategy_id>
/memescout_strategy_disable <strategy_id>
/memescout_strategy_status <strategy_id>
```

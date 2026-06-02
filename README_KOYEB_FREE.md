# MemeScout AI on Koyeb Free

Koyeb Free is resource constrained: **512 MB RAM**, **0.1 vCPU**, and **2 GB SSD**. It runs this project as a **Web Service**, not a Worker Service. MemeScout AI must stay paper-only on this target.

## What Koyeb Free mode does

Set `KOYEB_FREE_MODE=true` to run Condor in a lightweight MemeScout-only mode:

- Starts a tiny HTTP health server on `PORT`.
- Keeps Telegram MemeScout paper commands available.
- Skips the full Condor web dashboard, ServerDataService, Hummingbot API background fetches, Gateway integration startup, agent health monitor, update watcher, and other heavy background services.
- Does **not** enable any real trading path.

## Exact environment variables

```env
TELEGRAM_TOKEN=<your Telegram bot token>
ADMIN_USER_ID=<your Telegram user id>

KOYEB_FREE_MODE=true
PORT=8000
MEMESCOUT_LOW_MEMORY_MODE=true
MEMESCOUT_DISABLE_FULL_HUMMINGBOT_API=true
MEMESCOUT_AUTOSTART_SCANNER=true
MEMESCOUT_AUTOSTART_MONITOR=true

MEMESCOUT_PAPER_ONLY=true
MEMESCOUT_DB_PATH=data/memescout_ai.sqlite
MEMESCOUT_PAPER_BALANCE_USDC=100
MEMESCOUT_MAX_SIGNALS_PER_HOUR=6
MEMESCOUT_MAX_CANDIDATES_STORED_PER_HOUR=100
MEMESCOUT_MAX_DEX_REQUESTS_PER_MINUTE=50
MEMESCOUT_ENABLE_DEX_SEARCH=false
MEMESCOUT_DUPLICATE_SIGNAL_WINDOW_SECONDS=3600
MEMESCOUT_DEX_REQUEST_MIN_INTERVAL_SECONDS=1.05
MEMESCOUT_LLM_MAX_CALLS_PER_HOUR=20
MEMESCOUT_SCAN_INTERVAL_SECONDS=300
MEMESCOUT_MONITOR_ENABLED=true
MEMESCOUT_MONITOR_INTERVAL_SECONDS=120
MEMESCOUT_TRAILING_STOP_PCT=30
MEMESCOUT_ALLOW_RISK_REDUCING_CLOSES_DURING_EMERGENCY=true

LLM_PROVIDER=
GEMINI_API_KEY=
OPENAI_API_KEY=
OPENROUTER_API_KEY=
HELIUS_API_KEY=
```

LLM keys are optional and blank by default. MemeScout runs in rule-based mode without them.

## Exact Koyeb start command

```bash
python main.py
```

If your Koyeb build does not install the package automatically, use:

```bash
pip install -e . && python main.py
```

## Health endpoints

The lightweight server listens on `PORT` and exposes:

- `GET /`
- `GET /healthz`
- `GET /status`

`/healthz` returns `200 OK` quickly. `/status` returns non-secret JSON with app name, paper-only mode, scanner/monitor state, emergency stop, uptime, open position count, and last scan timestamp.

## UptimeRobot setup

Create an UptimeRobot HTTPS monitor that pings:

```text
https://<koyeb-app>.koyeb.app/healthz
```

UptimeRobot can help avoid Koyeb scale-to-zero by sending traffic, but it **cannot** prevent crashes, out-of-memory kills, redeploys, platform maintenance, or Koyeb restarts.

## SQLite warning

Koyeb Free cannot attach persistent volumes. `MEMESCOUT_DB_PATH=data/memescout_ai.sqlite` is local ephemeral storage and may be lost. Use `/memescout_backup` often to download a copy of paper-only SQLite data.

Do not treat Koyeb Free SQLite as durable long-term storage. Postgres support is intentionally not added in this step.

## Koyeb MemeScout loop controls

`/routines` is intentionally disabled in `KOYEB_FREE_MODE=true` because Koyeb Free mode registers only MemeScout paper-only handlers. Use these commands instead:

- `/memescout_start` — start the paper scanner loop.
- `/memescout_stop` — stop the paper scanner loop.
- `/memescout_monitor_start` — start the paper position monitor loop.
- `/memescout_monitor_stop` — stop the paper position monitor loop.
- `/memescout_loop_status` — show loop state, last scan/monitor timestamps, errors, and ETA.
- `/memescout_debug_last_scan` — show last scan summary and top filtered candidates.
- `/memescout_reset_hourly_limits` — reset only MemeScout hourly counters; does not delete signals or trades.
- `/memescout_strategies` — list paper-only strategy modules and enabled/disabled state.
- `/memescout_strategy_enable <strategy_id>` / `/memescout_strategy_disable <strategy_id>` — control one scanner strategy.
- `/memescout_strategy_status <strategy_id>` — inspect one scanner strategy.
- `/memescout_mode` — show entry/exit mode.
- `/memescout_set_entry_mode <manual_approval|auto_paper|observe_only>` — choose manual, fully automatic paper, or watchlist mode.
- `/memescout_set_exit_mode <auto|manual_only>` — choose deterministic monitor exits or manual-only force close.
- `/memescout_auto_status` / `/memescout_auto_report` — inspect auto-paper experiment results.

For 24/7 operation on Koyeb, set `MEMESCOUT_AUTOSTART_SCANNER=true` and `MEMESCOUT_AUTOSTART_MONITOR=true`. Autostart is duplicate-safe inside the running process.

`Scan now` may store candidates but send `0` Telegram signals when all candidates are rejected by filters, duplicate suppression, or the Telegram-signal quota. Rejected/stored-only candidates do not consume `MEMESCOUT_MAX_SIGNALS_PER_HOUR`; separate candidate-storage, strategy, and DEX-request limits are shown in `/memescout_status` and `/memescout_debug_last_scan`. Use `/memescout_debug_last_scan` to see why.

## Telegram smoke test

1. Deploy to Koyeb.
2. Open `https://<koyeb-app>.koyeb.app/healthz` and confirm `OK`.
3. Send `/memescout_status` in Telegram.
4. Send `/memescout_loop_status`.
5. If autostart is off, send `/memescout_start` and `/memescout_monitor_start`.
6. Send `/memescout` and tap **Scan now**.
7. If it sends 0 signals, send `/memescout_debug_last_scan`.
8. Use `/memescout_positions`, `/memescout_pnl`, and `/memescout_backup`.

## Safety reminder

Koyeb Free mode remains paper-only. It must not be used for real trading, private keys, Gateway swaps, Jupiter execution, or Hummingbot order endpoints.

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

MEMESCOUT_PAPER_ONLY=true
MEMESCOUT_DB_PATH=data/memescout_ai.sqlite
MEMESCOUT_PAPER_BALANCE_USDC=100
MEMESCOUT_MAX_SIGNALS_PER_HOUR=6
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

## Telegram smoke test

1. Deploy to Koyeb.
2. Open `https://<koyeb-app>.koyeb.app/healthz` and confirm `OK`.
3. Send `/memescout_status` in Telegram.
4. Send `/memescout` and tap **Scan now**.
5. Use `/memescout_positions`, `/memescout_pnl`, and `/memescout_backup`.

## Safety reminder

Koyeb Free mode remains paper-only. It must not be used for real trading, private keys, Gateway swaps, Jupiter execution, or Hummingbot order endpoints.

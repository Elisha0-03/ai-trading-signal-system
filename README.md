# AI Multi-Timeframe Trading Signal System

A signal-only Python application that captures TradingView chart views, sends four labeled timeframes to Google Gemini for three independent strategy analyses, validates the returned prices, and delivers a Telegram digest per instrument. It does **not** place orders, connect to a broker, or install trading indicators.

## Security note about the uploaded source

The uploaded ZIP contained Python entrypoints and modules whose visible contents were encrypted payloads passed to `exec`, with anti-debug checks. That code was not executed or trusted. This folder is a clean, readable replacement built from the accompanying PDF requirements; the original ZIP is left untouched. Review this source before configuring credentials.

## What a cycle does

- Reads 20 enabled instruments and four shared chart intervals (`15m`, `1h`, `4h`, `1D`): 80 chart captures.
- Runs each enabled strategy independently for each instrument: 60 expected Gemini analyses (20 × 3).
- Uses three editable prompt files: V47.0 Intraday, V51.0 Scalping, and V57.0 Swing.
- Rejects malformed model output and BUY/SELL levels whose entry, stop, and target are ordered inconsistently. `NO_ENTRY` is permitted and is sent by default.
- Sends one Telegram digest per instrument, containing each enabled strategy's result.

The scheduler starts each cycle five seconds after a UTC 15-minute boundary by default. Screenshot capture uses a bounded pool of four independent browser workers by default (`screenshot.max_concurrent_captures`). A large batch still spans a period, so it is not a synchronized snapshot and screenshots are not guaranteed to occur at the exact close of each instrument/timeframe candle. Confirm whether this timing is sufficient for your use. TradingView may show a consent banner, login gate, CAPTCHA, rate limit, or unavailable symbol; such captures are logged and an incomplete MTF set is skipped rather than analyzed.

Generated screenshot cycle folders older than the configured retention period (three days by default) are removed at the start of a cycle.

## Requirements

- Python 3.10 or newer
- Google Chrome or Chromium installed on the machine running the service (Selenium Manager obtains a compatible driver when possible)
- A Gemini API key with access to the configured model
- A Telegram bot token and target chat ID
- Network access to TradingView, Google AI, and Telegram

API usage, model availability, and quotas depend on your Google account and can change. The default Gemini model and request pacing are configurable in `config/config.yaml`. A live cycle can use up to 60 Gemini requests; check your account's model access, quota, and any charges before running it.

## Install and configure

From this directory on Linux:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
cp .env.example .env
```

Edit `.env` with the three values:

```dotenv
GEMINI_API_KEY=...
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
```

Keep `.env` private and do not commit it. The application sends credentials only to the configured Google API and Telegram Bot API endpoints; they are not written to logs.

## Test without network or credentials

```bash
python main.py --dry-run
python -m unittest discover -s tests -v
```

The dry run validates configuration and prints the expected scale without opening a browser or making API calls. The unit tests cover counts, JSON parsing, signal-level validation, message formatting, and scheduling. These tests do not establish TradingView availability, Gemini model access, Telegram delivery, signal accuracy, or VPS readiness.

## Run live

A single live cycle immediately captures charts, calls Gemini, and sends Telegram messages:

```bash
python main.py --once
```

This can consume API quota or incur charges and will send messages to the configured chat. `--once --no-telegram` skips Telegram delivery but still makes Gemini API calls. For continuous operation, run:

```bash
python main.py
```

The scheduler follows UTC boundaries. Set `scheduler.run_immediately: true` only if you want a cycle at startup as well. The main loop logs failures and remains alive; systemd can restart the process after an exit.

## Configuration

- `config/config.yaml`: intervals, strategies, prompts, model, rate pacing, screenshot options, Telegram behavior, scheduler timing.
- `config/instruments.yaml`: instrument symbols, names, categories, and enabled flags. The current 20 symbols are copied from the uploaded configuration; verify each symbol/provider on TradingView before a live cycle.
- `prompts/*.txt`: editable analysis instructions. Keep the JSON response keys and price-order rules intact unless you also update `src/signal_processor.py`.

Disable a strategy by setting its `enabled` field to `false`. To limit a strategy to selected instruments, add a `strategies: [v47_intraday, ...]` list to those instrument entries; if omitted, all enabled strategies apply.

## Free hobby deployment: GitHub Actions

The workflow in `.github/workflows/trading-signals.yml` uses a standard `ubuntu-latest` GitHub-hosted runner in a **public repository**. [Standard runners are free for public repositories](https://docs.github.com/billing/managing-billing-for-github-actions/about-billing-for-github-actions); Gemini access and quota are separate and are not made free by this deployment. No paid runner, persistent VPS, cache, or artifact uploads are configured.

It schedules **one cycle every 15 minutes** using `*/15 * * * *` (UTC), and also offers an explicit manual run. It runs `python main.py --once`, not the perpetual scheduler. A concurrency group prevents overlapping cycles without cancelling a running one; only one pending cycle is kept by GitHub, so a backlog does not replay every missed interval. A job has a 45-minute maximum runtime and a timeout can leave a cycle only partially delivered.

### Finish setup

1. Open the repository's **Settings → Secrets and variables → Actions → New repository secret**. Add these three secrets (not variables or source files):
   - `GEMINI_API_KEY`: your Google AI API key with access to the configured model (`gemini-2.5-pro` by default).
   - `TELEGRAM_BOT_TOKEN`: your Telegram bot token from **@BotFather**.
   - `TELEGRAM_CHAT_ID`: your numeric Telegram destination chat ID. Start the bot in Telegram first; for a group, add the bot and permit it to send messages. For a channel, grant the necessary posting permission.
2. Review model quota/cost and the enabled instruments. A full cycle can make up to **60 Gemini analyses and 20 Telegram digests**; this repeats at each scheduled cycle.
3. Check the repository's **Actions** tab and enable workflows if GitHub asks. The workflow must be on the default branch (`main`). Once all three secrets are saved, the **next scheduled run is live**—there is no additional activation switch.
4. If you want to test immediately after configuring all secrets, choose **Actions → Trading signals → Run workflow → main → Run workflow**. This makes live Gemini calls and sends Telegram messages. Do not manually rerun a partially delivered cycle unless duplicate messages are acceptable.
5. Inspect the run's **Run exactly one live cycle** log for capture failures, validated analysis counts, and Telegram delivery failures. A completed job alone does not prove all instruments reached Telegram, since individual capture, analysis, and delivery errors are logged and skipped by the app.

Before all three secrets are present and nonblank, the first workflow step **fails closed** with missing secret names only. It does not check out code, install dependencies, open a chart, call Gemini, or send Telegram messages. Workflow secrets are passed only to the guard and live-cycle steps; do not print them. `.env`, screenshots, and runtime logs are excluded from source. Public repository code and Actions logs are visible to others; review code changes before running them with secrets.

### Timing and operating limits

This is best-effort scheduled execution, **not an always-on VPS** and not an interactive Telegram command bot. Telegram is the output destination; no polling or webhook server is started. The internal five-second candle-boundary delay does not apply to `--once`: GitHub queue/setup time determines when capture actually starts. [GitHub documents](https://docs.github.com/en/actions/using-workflows/events-that-trigger-workflows#schedule) that scheduled jobs can be delayed or dropped under load and public-repository schedules are disabled after **60 days without repository activity**. Re-enable from the Actions tab after inactivity; no artificial keep-alive commits are included. Fresh runners discard local screenshots and logs at the end of a job; use the Actions console for diagnostics.

To pause delivery, go to **Actions → Trading signals → ⋯ → Disable workflow**. Removing any required secret also stops future runs before live work. This deployment has not yet been verified with live credentials, Gemini responses, or Telegram delivery.

## VPS deployment

A systemd unit template is in `deployment/trading_signals.service`. Copy this project to `/opt/ai-trading-signal-system`, install its dependencies and Chrome, create and populate `/opt/ai-trading-signal-system/.env`, then install the unit:

```bash
sudo cp deployment/trading_signals.service /etc/systemd/system/trading_signals.service
sudo systemctl daemon-reload
sudo systemctl enable --now trading_signals
sudo systemctl status trading_signals
journalctl -u trading_signals -f
```

The unit runs as the `ubuntu` account by default; adjust `User`, `Group`, and paths for your VPS. Do not enable the service until Chrome, configuration, network access, and credentials are ready. This project has not been deployed to or tested on a VPS.

## Limitations and safety

Chart images may omit details or render differently by symbol, viewport, browser, region, and TradingView access state. AI-generated levels can be wrong; confidence labels are not calibrated probabilities and do not imply a win rate. Review every result independently and use your own risk controls. This is not investment advice. The application is signal-only and contains no broker integration or trade execution code.

# AI Multi-Timeframe Trading Signal System

A signal-only Python application that captures TradingView charts, asks Gemini to analyze them, validates the returned levels, and delivers a Telegram digest. It does **not** place orders or connect to a broker. AI signals can be wrong; review them independently. This is not investment advice.

## Paused, adapted for Gemini Free tier

The GitHub Actions workflow was manually disabled during the free-quota adaptation and is intentionally left disabled. Configuration in source does not re-enable a disabled workflow. The existing Gemini API key, Telegram token, and chat ID have not been replaced or removed.

The first live run encountered a **20-request-per-day Free-tier limit** and produced no Telegram signals. The old configuration planned 60 independent analyses every 15 minutes, before retries. The new workload is deliberately much smaller:

| Setting | Free-tier configuration |
| --- | --- |
| Enabled instruments | EUR/USD, GBP/USD, USD/JPY, Gold (XAU/USD), Bitcoin (BTC/USDT) |
| Strategy | V57.0 Swing only |
| Chart timeframes | `15m`, `1h`, `4h`, `1D`, bundled into one Gemini request per instrument |
| Per cycle | 20 chart captures, at most 5 Gemini requests, up to 5 instrument digests |
| Schedule | `17 1,9,17 * * *` UTC: 01:17, 09:17, 17:17 |
| Nigeria time (WAT, UTC+1) | 02:17, 10:17, 18:17 daily |
| Scheduled daily maximum | **15 Gemini requests**, leaving 5 of the observed 20-request quota unused |
| Request pacing | At most 4 requests per minute; no automatic retries |

Swing analysis suits this slower cadence better than scalping. The other 15 instruments and both other strategies are retained but disabled, not deleted. All three prompt files remain editable. `NO_ENTRY` digests are still enabled. Missing/incomplete chart sets are skipped and can reduce actual requests and messages.

### Quota and cost safeguards

- Configuration validation rejects workload above 5 analyses per cycle or the configured 15-request scheduled daily budget. It rejects retries, startup runs, and paid-mode policy settings.
- The Gemini client counts HTTP attempts **before** posting, including failed/ambiguous calls, and refuses a sixth request in a cycle.
- HTTP 429 stops the entire cycle immediately without retry, model switching, or paid fallback; the one-cycle process exits unsuccessfully rather than claiming a successful cycle. Earlier digests, if any, cannot be unsent.
- Other failed requests are not retried under the committed configuration.
- There is deliberately **no manual `workflow_dispatch` button**. Scheduled runs use `--once`, not the perpetual scheduler. Concurrency prevents overlap.
- No paid Google plan, billing account, paid model fallback, or paid runner is enabled by this project/change. `usage_tier: free` and `allow_paid: false` are local workload policy, **not** a Google billing API control. Keep the existing Google project on Free tier; enabling billing there can change charging behavior.

The 15/day limit is the normal configured schedule, **not a durable cross-run quota ledger**. GitHub run replays, direct local `--once` invocations, a second deployment, and other applications sharing the Google project's quota can exceed it. Do not run copies concurrently or re-run jobs to test. Account/model quotas, token/image limits, and availability can change; the five-request headroom is not reserved and does not guarantee every request succeeds. The configured `gemini-3.8-flash` model and API key are unchanged; no model-access probe was made during adaptation.

## Safely resume later

1. Leave **Actions → Trading signals** disabled until the Gemini daily quota has reset after the earlier failed run. Since a fresh quota reset was not checked, a conservative option is to wait at least 24 hours after the last attempted live request, then confirm available quota in Google AI Studio. Keep the project on **Free tier**, with no paid billing upgrade.
2. Confirm the public `main` branch contains this free-tier change. Keep the three existing repository **secrets** (`GEMINI_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`) unchanged. Do not paste them into source, logs, or repository variables.
3. Ensure this workflow is the **only** deployment using this workload/key and no other app is exhausting its project quota. Check that it still enables only the five instruments and swing strategy.
4. Optionally run the **offline** checks below. Do not run `--once`, use **Re-run jobs**, or start the systemd service as a test.
5. On the [Trading signals workflow page](https://github.com/Elisha0-03/ai-trading-signal-system/actions/workflows/trading-signals.yml), click **Enable workflow** once. Wait for the next scheduled slot; this enables future live analysis and Telegram delivery, not an immediate validation run.
6. Inspect that next scheduled run's logs for validated analysis counts and Telegram delivery errors. A green job alone does not prove every signal was delivered: chart, parsing, and Telegram errors may skip individual results. A 429 is now a failed cycle; disable the workflow and inspect quota instead of replaying it.

To pause again: **Actions → Trading signals → Workflow options → Disable workflow**. Removing a secret is unnecessary. A source commit does not automatically resume a manually disabled workflow.

## Install and test offline

Requirements: Python 3.10+, Chrome/Chromium for live screenshot capture, and dependencies in `requirements.txt`.

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python -m unittest discover -s tests -v
python main.py --dry-run
```

The dry run validates configuration and prints the 20 captures/5 analyses per cycle and 15 scheduled requests/day without browser, Gemini, Telegram, or trading activity. Unit tests use fake keys and mocked external operations. These checks do **not** establish model access, TradingView availability, successful Telegram delivery, or signal accuracy. No live cycle or Telegram test was run for this adaptation.

For a separately reviewed deployment, copy `.env.example` to `.env` and populate its required values privately. The GitHub workflow uses repository secrets instead. `.env`, screenshots, and logs are ignored by Git. Before any live work, a missing/blank required secret fails closed; the workflow's credential guard runs before checkout/install/capture/API calls.

## Files and execution behavior

- `config/config.yaml`: strategies, model, request caps/pacing, chart options, Telegram behavior, internal schedule.
- `config/instruments.yaml`: all 20 original symbols; only the five above are enabled. Verify each TradingView symbol/provider before live use.
- `prompts/*.txt`: V47.0 Intraday, V51.0 Scalping, V57.0 Swing prompts. Retain JSON keys and valid BUY/SELL price ordering.
- `.github/workflows/trading-signals.yml`: three daily UTC slots; secrets are passed only to the credential guard and live-cycle step. It runs offline tests before any live cycle. Public code and Actions logs remain publicly visible; never print credentials.

Four independent browser workers capture chart sets. Screenshot cycle folders older than three days are removed. Charts are not synchronized candle-close snapshots; TradingView may display consent/login/CAPTCHA/rate limits or unavailable symbols. An incomplete multi-timeframe set is skipped rather than analyzed.

The internal Python scheduler is aligned every 480 minutes with a 4,620-second offset from UTC eight-hour boundaries, matching the three nominal GitHub times; `run_immediately` is false. It is an **alternative** to GitHub, not an additional deployment. `python main.py --once` starts live work immediately and can send Telegram messages; `--no-telegram` still consumes Gemini quota. Do not use either as an offline test.

GitHub-hosted execution is best effort, not an always-on server or interactive Telegram bot. Queue/setup time shifts actual capture time; scheduled jobs can be delayed/dropped. The 45-minute timeout can leave partial output. Public-repository schedules can be disabled after 60 days of inactivity. Fresh runners discard local screenshots/logs after a job; consult Actions logs. Standard public-repository runners are used, with no paid runner, persistent VPS, caches, or artifact uploads configured. See [GitHub scheduling behavior](https://docs.github.com/en/actions/using-workflows/events-that-trigger-workflows#schedule).

`deployment/trading_signals.service` remains an unactivated optional systemd template. Do not enable it while GitHub is active; this project has not been deployed to or tested on a VPS.

## Source and safety notes

The original uploaded ZIP contained encrypted Python payloads passed to `exec` with anti-debug checks. Those payloads were not executed or trusted. This repository is a clean, readable replacement, built from the accompanying requirements; the original upload was left untouched.

Model confidence is not a calibrated probability or win rate. Chart rendering and model output can be incomplete or incorrect. Price-order validation rejects malformed BUY/SELL levels but cannot establish that a trade is safe or profitable. There is no broker integration or order execution code.

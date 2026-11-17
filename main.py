"""Entrypoint for a signal-only AI chart analysis service."""
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any

from src.config import (
    enabled_instruments,
    enabled_strategies,
    load_settings,
    prompt_for,
    required_credentials,
)
from src.gemini_client import GeminiClient, GeminiQuotaError, GeminiRequestBudgetError
from src.scheduler import run_forever
from src.screenshot_capture import capture_cycle
from src.signal_processor import format_instrument_message, parse_signal
from src.telegram_client import TelegramClient


def configure_logging(settings: dict[str, Any]) -> None:
    log_dir = settings["root"] / settings["system"].get("log_dir", "logs")
    log_dir.mkdir(parents=True, exist_ok=True)
    level = getattr(logging, str(settings["system"].get("log_level", "INFO")).upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.StreamHandler(), logging.FileHandler(log_dir / "service.log", encoding="utf-8")],
    )


def expected_work(settings: dict[str, Any]) -> tuple[int, int]:
    instruments = enabled_instruments(settings)
    strategies = enabled_strategies(settings)
    return len(instruments) * len(settings["timeframes"]), sum(
        1 for instrument in instruments for strategy_key in strategies
        if instrument.get("strategies") is None or strategy_key in instrument.get("strategies", [])
    )


def run_cycle(settings: dict[str, Any], no_telegram: bool = False) -> None:
    log = logging.getLogger("cycle")
    missing = required_credentials(include_telegram=not no_telegram)
    if missing:
        raise RuntimeError("Missing required environment variables: " + ", ".join(sorted(missing)))

    api_key = os.environ["GEMINI_API_KEY"].strip()
    telegram = None if no_telegram else TelegramClient(
        os.environ["TELEGRAM_BOT_TOKEN"].strip(),
        os.environ["TELEGRAM_CHAT_ID"].strip(),
        settings.get("telegram", {}),
    )
    gemini = GeminiClient(api_key, settings.get("gemini", {}))

    now = datetime.now(timezone.utc)
    cycle_id = now.strftime("%Y%m%dT%H%M%SZ")
    instruments = enabled_instruments(settings)
    strategies = enabled_strategies(settings)
    expected_screenshots, expected_analyses = expected_work(settings)
    log.info("Cycle %s started: %d instruments, %d expected screenshots, %d strategy analyses",
             cycle_id, len(instruments), expected_screenshots, expected_analyses)

    captures, capture_errors = capture_cycle(settings, cycle_id)
    if capture_errors:
        for symbol, messages in capture_errors.items():
            log.error("Skipping incomplete MTF set for %s: %s", symbol, "; ".join(messages))

    completed = 0
    sent = 0
    for instrument in instruments:
        symbol = str(instrument["symbol"])
        images = captures.get(symbol, {})
        expected_labels = [str(item["name"]) for item in settings["timeframes"]]
        if len(images) != len(expected_labels) or any(label not in images for label in expected_labels):
            log.warning("Skipping all strategy analyses for %s because one or more charts are missing", symbol)
            continue
        strategy_results = []
        for strategy_key, strategy in strategies.items():
            allowed = instrument.get("strategies")
            if allowed is not None and strategy_key not in allowed:
                continue
            try:
                raw = gemini.analyze(
                    prompt=prompt_for(settings, strategy_key),
                    symbol=symbol,
                    instrument_name=str(instrument["name"]),
                    images=images,
                )
                signal = parse_signal(raw)
                strategy_results.append((strategy_key, strategy, signal))
                completed += 1
                log.info("Validated %s %s: %s (%s)", symbol, strategy.get("name", strategy_key),
                         signal.direction, signal.confidence)
            except (GeminiQuotaError, GeminiRequestBudgetError):
                log.error("Free-tier limit reached: aborting cycle without further analysis or delivery")
                raise
            except Exception:
                log.exception("Analysis failed for %s strategy %s", symbol, strategy_key)

        if telegram and strategy_results:
            message = format_instrument_message(
                instrument, strategy_results,
                send_no_entry=bool(settings.get("telegram", {}).get("send_no_entry", True)),
            )
            if message:
                try:
                    limit = min(4096, int(settings.get("telegram", {}).get("max_message_chars", 3900)))
                    if len(message) <= limit:
                        telegram.send(message)
                    else:
                        # Preserve complete results by falling back to one strategy per message.
                        for item in strategy_results:
                            one = format_instrument_message(instrument, [item],
                                send_no_entry=bool(settings.get("telegram", {}).get("send_no_entry", True)))
                            if one:
                                telegram.send(one)
                    sent += 1
                except Exception:
                    log.exception("Telegram delivery failed for %s", symbol)

    log.info("Cycle %s complete: %d/%d analyses validated; %d instrument digests attempted",
             cycle_id, completed, expected_analyses, sent)


def dry_run(settings: dict[str, Any]) -> int:
    screenshots, analyses = expected_work(settings)
    missing = required_credentials(include_telegram=True)
    print("Configuration is valid.")
    print(f"Enabled instruments: {len(enabled_instruments(settings))}")
    print(f"Timeframes per instrument: {len(settings['timeframes'])}")
    print(f"Expected screenshots per cycle: {screenshots}")
    print(f"Enabled strategies: {', '.join(x.get('name', key) for key, x in enabled_strategies(settings).items())}")
    print(f"Expected independent analyses per cycle: {analyses}")
    cycles = 1440 // int(settings["scheduler"]["cycle_interval_minutes"])
    print(f"Scheduled cycles per day: {cycles}; maximum scheduled Gemini requests: {analyses * cycles}/20")
    print("Free-tier policy: no automatic retries, no paid fallback; abort cycle on HTTP 429.")
    print("Live credentials present: " + ("yes" if not missing else "no; missing " + ", ".join(sorted(missing))))
    print("Dry run performs no browser, AI API, Telegram, or trading activity.")
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="AI chart analysis and Telegram signal delivery; no order execution.")
    parser.add_argument("--dry-run", action="store_true", help="validate configuration and show planned cycle counts, without network calls")
    parser.add_argument("--once", action="store_true", help="run one live cycle immediately")
    parser.add_argument("--no-telegram", action="store_true", help="analyze but do not send Telegram messages (Gemini API calls may incur cost)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        settings = load_settings()
        configure_logging(settings)
        if args.dry_run:
            return dry_run(settings)
        if args.once:
            run_cycle(settings, no_telegram=args.no_telegram)
            return 0
        scheduler = settings.get("scheduler", {})
        interval = int(scheduler.get("cycle_interval_minutes", 15))
        delay = int(scheduler.get("delay_after_close_seconds", 5))
        logging.getLogger(__name__).info("Starting scheduled service (UTC, every %d minutes)", interval)

        def guarded_cycle() -> None:
            try:
                run_cycle(settings, no_telegram=args.no_telegram)
            except Exception:
                logging.getLogger("cycle").exception("Cycle failed; service will remain available")

        run_forever(guarded_cycle, interval, delay, bool(scheduler.get("run_immediately", False)))
    except KeyboardInterrupt:
        print("Stopped.")
        return 130
    except Exception as exc:
        print(f"Startup/runtime error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

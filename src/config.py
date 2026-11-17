"""Configuration loading and validation for the signal-only application."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv


class ConfigurationError(ValueError):
    """Raised when project configuration is missing or inconsistent."""


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def load_settings(root: Path | None = None) -> dict[str, Any]:
    root = (root or project_root()).resolve()
    load_dotenv(root / ".env", override=False)
    config_path = root / "config" / "config.yaml"
    instruments_path = root / "config" / "instruments.yaml"
    try:
        with config_path.open(encoding="utf-8") as stream:
            settings = yaml.safe_load(stream) or {}
        with instruments_path.open(encoding="utf-8") as stream:
            instrument_config = yaml.safe_load(stream) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigurationError(f"Could not read configuration: {exc}") from exc

    settings["root"] = root
    settings["instruments"] = instrument_config.get("instruments", [])
    _validate(settings)
    return settings


def _validate(settings: dict[str, Any]) -> None:
    timeframes = settings.get("timeframes")
    if not isinstance(timeframes, list) or not timeframes:
        raise ConfigurationError("Set at least one timeframe in config/config.yaml")
    names = [str(item.get("name", "")) for item in timeframes if isinstance(item, dict)]
    if len(names) != len(timeframes) or any(not name for name in names):
        raise ConfigurationError("Each timeframe must have a name and TradingView interval")
    if len(set(names)) != len(names):
        raise ConfigurationError("Timeframe names must be unique")
    if any(not item.get("tradingview_interval") for item in timeframes):
        raise ConfigurationError("Each timeframe must have a TradingView interval")

    strategies = settings.get("strategies")
    if not isinstance(strategies, dict) or not any(item.get("enabled") for item in strategies.values()):
        raise ConfigurationError("Enable at least one strategy in config/config.yaml")
    root = settings["root"]
    for key, strategy in strategies.items():
        if not strategy.get("enabled"):
            continue
        prompt_path = (root / str(strategy.get("prompt_file", ""))).resolve()
        if not prompt_path.is_relative_to(root) or not prompt_path.is_file():
            raise ConfigurationError(f"Prompt for strategy {key!r} is missing or outside the project")

    instruments = settings.get("instruments")
    if not isinstance(instruments, list) or not instruments:
        raise ConfigurationError("Set at least one instrument in config/instruments.yaml")
    symbols: set[str] = set()
    for instrument in instruments:
        if not instrument.get("symbol") or not instrument.get("name"):
            raise ConfigurationError("Every instrument needs symbol and name fields")
        symbol = str(instrument["symbol"])
        if symbol in symbols:
            raise ConfigurationError(f"Duplicate instrument symbol: {symbol}")
        symbols.add(symbol)

    gemini = settings.get("gemini", {})
    if int(gemini.get("rate_limit_per_minute", 0)) <= 0:
        raise ConfigurationError("gemini.rate_limit_per_minute must be a positive integer")
    if int(settings.get("scheduler", {}).get("cycle_interval_minutes", 0)) <= 0:
        raise ConfigurationError("scheduler.cycle_interval_minutes must be positive")
    if gemini.get("usage_tier") != "free" or gemini.get("allow_paid") is not False:
        raise ConfigurationError("Only the free usage tier is permitted; allow_paid must be false")
    if int(gemini.get("max_retries", -1)) != 0:
        raise ConfigurationError("Free-tier max_retries must be zero")
    cap = int(gemini.get("max_requests_per_cycle", 0))
    budget = int(gemini.get("daily_request_budget", 0))
    if not 0 < cap <= budget < 20:
        raise ConfigurationError("Request limits must satisfy 0 < per-cycle cap <= daily budget < 20")
    analyses = sum(
        1 for instrument in enabled_instruments(settings) for strategy in enabled_strategies(settings)
        if instrument.get("strategies") is None or strategy in instrument["strategies"]
    )
    interval = int(settings["scheduler"]["cycle_interval_minutes"])
    if interval > 1440 or 1440 % interval or analyses == 0 or analyses > cap or analyses * (1440 // interval) > budget:
        raise ConfigurationError("Configured workload exceeds the free daily budget or per-cycle cap")
    delay = int(settings["scheduler"].get("delay_after_close_seconds", 0))
    if not 0 <= delay < interval * 60 or settings["scheduler"].get("run_immediately", False):
        raise ConfigurationError("Free-tier scheduler requires a bounded delay and run_immediately false")


def enabled_instruments(settings: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for item in settings["instruments"] if item.get("enabled", True)]


def enabled_strategies(settings: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {key: value for key, value in settings["strategies"].items() if value.get("enabled", False)}


def prompt_for(settings: dict[str, Any], strategy_key: str) -> str:
    strategy = settings["strategies"][strategy_key]
    path = (settings["root"] / strategy["prompt_file"]).resolve()
    if not path.is_relative_to(settings["root"]):
        raise ConfigurationError(f"Prompt for {strategy_key!r} resolves outside the project")
    return path.read_text(encoding="utf-8").strip()


def required_credentials(include_telegram: bool = True) -> dict[str, str]:
    required = {"GEMINI_API_KEY": os.getenv("GEMINI_API_KEY", "").strip()}
    if include_telegram:
        required["TELEGRAM_BOT_TOKEN"] = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        required["TELEGRAM_CHAT_ID"] = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    return {name: value for name, value in required.items() if not value}

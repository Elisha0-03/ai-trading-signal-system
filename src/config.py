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

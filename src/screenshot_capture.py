"""TradingView chart capture. This module only reads pages and saves screenshots."""
from __future__ import annotations

import logging
import re
import shutil
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import quote
from typing import Any

from selenium import webdriver
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

log = logging.getLogger(__name__)


def safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("_.") or "item"


def cleanup_old_cycles(screenshot_root: Path, retention_days: int) -> None:
    """Delete only dated cycle directories created by this application."""
    if retention_days <= 0 or not screenshot_root.exists():
        return
    cutoff = time.time() - retention_days * 86400
    for candidate in screenshot_root.iterdir():
        if candidate.is_dir() and re.fullmatch(r"\d{8}T\d{6}Z", candidate.name):
            try:
                if candidate.stat().st_mtime < cutoff:
                    shutil.rmtree(candidate)
                    log.info("Removed expired screenshot cycle %s", candidate.name)
            except OSError:
                log.warning("Could not remove expired screenshot cycle %s", candidate, exc_info=True)


class TradingViewCapture:
    def __init__(self, settings: dict[str, Any]):
        self.settings = settings["screenshot"]
        self.driver: webdriver.Chrome | None = None
        self.seen: set[tuple[str, str]] = set()

    def __enter__(self) -> "TradingViewCapture":
        options = Options()
        if self.settings.get("headless", True):
            options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--disable-notifications")
        options.add_argument("--disable-infobars")
        options.add_argument(f"--window-size={int(self.settings.get('window_width', 1920))},{int(self.settings.get('window_height', 1080))}")
        options.add_argument("--lang=en-US")
        options.set_capability("pageLoadStrategy", "eager")
        self.driver = webdriver.Chrome(options=options)
        self.driver.set_page_load_timeout(int(self.settings.get("chart_wait_seconds", 20)) + 15)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.driver is not None:
            try:
                self.driver.quit()
            except WebDriverException:
                log.debug("Browser cleanup raised an error", exc_info=True)
            self.driver = None

    def capture(self, symbol: str, timeframe: dict[str, str], destination: Path) -> Path:
        if self.driver is None:
            raise RuntimeError("Use TradingViewCapture as a context manager")
        label = str(timeframe["name"])
        key = (symbol, label)
        if key in self.seen:
            raise ValueError(f"Refusing duplicate screenshot in one cycle: {symbol} {label}")
        self.seen.add(key)

        url = "https://www.tradingview.com/chart/?symbol={}&interval={}".format(
            quote(symbol, safe=""), quote(str(timeframe["tradingview_interval"]), safe="")
        )
        try:
            self.driver.get(url)
        except TimeoutException:
            log.warning("Page-load timeout for %s %s; checking whether chart rendered", symbol, label)
        try:
            WebDriverWait(self.driver, int(self.settings.get("chart_wait_seconds", 20))).until(
                lambda driver: len(driver.find_elements(By.CSS_SELECTOR, "canvas")) > 0
            )
        except TimeoutException as exc:
            raise RuntimeError(f"TradingView chart did not render for {symbol} ({label}); access may be blocked") from exc

        time.sleep(float(self.settings.get("post_navigation_wait_seconds", 5)))
        destination.parent.mkdir(parents=True, exist_ok=True)
        if self.settings.get("capture_chart_area_only", True):
            candidates = self.driver.find_elements(By.CSS_SELECTOR, "[data-name='chart-container'], .chart-container")
            if candidates:
                candidates[0].screenshot(str(destination))
            else:
                self.driver.save_screenshot(str(destination))
        else:
            self.driver.save_screenshot(str(destination))
        if not destination.is_file() or destination.stat().st_size == 0:
            raise RuntimeError(f"Screenshot output is empty for {symbol} ({label})")
        log.info("Captured %s %s -> %s", symbol, label, destination.name)
        return destination


def _capture_batch(settings: dict[str, Any], tasks: list[tuple[dict[str, Any], dict[str, str], Path]]) -> list[tuple[str, str, Path | None, str | None]]:
    """Run a batch in its own browser; always report a result for every task."""
    outcomes: list[tuple[str, str, Path | None, str | None]] = []
    try:
        with TradingViewCapture(settings) as capture:
            for instrument, timeframe, destination in tasks:
                symbol = str(instrument["symbol"])
                label = str(timeframe["name"])
                try:
                    path = capture.capture(symbol, timeframe, destination)
                    outcomes.append((symbol, label, path, None))
                except Exception as exc:
                    message = f"{label}: {type(exc).__name__}: {exc}"
                    log.exception("Capture failed for %s %s", symbol, label)
                    outcomes.append((symbol, label, None, message))
    except Exception as exc:
        message = f"Browser worker failed: {type(exc).__name__}: {exc}"
        log.exception("Could not start a TradingView browser worker")
        for instrument, timeframe, _destination in tasks:
            outcomes.append((str(instrument["symbol"]), str(timeframe["name"]), None, message))
    return outcomes


def capture_cycle(settings: dict[str, Any], cycle_id: str) -> tuple[dict[str, dict[str, Path]], dict[str, list[str]]]:
    """Capture one image per enabled symbol/timeframe using a bounded browser pool."""
    root = settings["root"]
    screenshot_root = root / settings["system"].get("screenshot_dir", "screenshots")
    cleanup_old_cycles(screenshot_root, int(settings["system"].get("screenshot_retention_days", 3)))
    output = screenshot_root / cycle_id
    instruments = [item for item in settings["instruments"] if item.get("enabled", True)]
    results: dict[str, dict[str, Path]] = {str(item["symbol"]): {} for item in instruments}
    errors: dict[str, list[str]] = {str(item["symbol"]): [] for item in instruments}
    tasks: list[tuple[dict[str, Any], dict[str, str], Path]] = []
    for instrument in instruments:
        symbol = str(instrument["symbol"])
        for timeframe in settings["timeframes"]:
            label = str(timeframe["name"])
            destination = output / safe_name(symbol) / f"{safe_name(label)}.png"
            tasks.append((instrument, timeframe, destination))

    if not tasks:
        return results, errors
    worker_limit = max(1, int(settings["screenshot"].get("max_concurrent_captures", 4)))
    worker_count = min(worker_limit, len(tasks))
    batches = [tasks[index::worker_count] for index in range(worker_count)]
    with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="tv-capture") as executor:
        futures = [executor.submit(_capture_batch, settings, batch) for batch in batches]
        for future in as_completed(futures):
            for symbol, label, path, error in future.result():
                if path is not None:
                    results[symbol][label] = path
                if error is not None:
                    errors[symbol].append(error)
    return results, {symbol: messages for symbol, messages in errors.items() if messages}

"""Parse model output into a validated, signal-only result."""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any


@dataclass(frozen=True)
class Signal:
    direction: str
    confidence: str
    entry_price: Decimal | None
    stop_loss: Decimal | None
    take_profit: Decimal | None
    technical_justification: str
    narrative: str


def _number(value: Any, key: str) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"{key} must be a number or null")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{key} must be a number or null") from exc
    if not number.is_finite():
        raise ValueError(f"{key} must be finite")
    return number


def _clean_text(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field} is required")
    return text[:1200]


def parse_signal(raw: str) -> Signal:
    text = raw.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("Response did not contain a JSON object")
    try:
        data = json.loads(text[start:end + 1])
    except json.JSONDecodeError as exc:
        raise ValueError(f"Response JSON is invalid: {exc.msg}") from exc
    if not isinstance(data, dict):
        raise ValueError("Signal response must be a JSON object")
    direction = str(data.get("direction", "")).strip().upper()
    if direction not in {"BUY", "SELL", "NO_ENTRY"}:
        raise ValueError("direction must be BUY, SELL, or NO_ENTRY")
    confidence = str(data.get("confidence", "LOW")).strip().upper()
    if confidence not in {"LOW", "MEDIUM", "HIGH"}:
        raise ValueError("confidence must be LOW, MEDIUM, or HIGH")
    justification = _clean_text(data.get("technical_justification"), "technical_justification")
    narrative = _clean_text(data.get("narrative"), "narrative")
    if direction == "NO_ENTRY":
        return Signal(direction, confidence, None, None, None, justification, narrative)

    entry = _number(data.get("entry_price"), "entry_price")
    stop = _number(data.get("stop_loss"), "stop_loss")
    target = _number(data.get("take_profit"), "take_profit")
    if entry is None or stop is None or target is None:
        raise ValueError("BUY/SELL requires numeric entry_price, stop_loss, and take_profit")
    if direction == "BUY" and not stop < entry < target:
        raise ValueError("BUY levels must satisfy stop_loss < entry_price < take_profit")
    if direction == "SELL" and not target < entry < stop:
        raise ValueError("SELL levels must satisfy take_profit < entry_price < stop_loss")
    return Signal(direction, confidence, entry, stop, target, justification, narrative)


def _decimal_text(value: Decimal | None) -> str:
    if value is None:
        return "—"
    return format(value, "f").rstrip("0").rstrip(".") if "." in format(value, "f") else format(value, "f")


def _rr(signal: Signal) -> str:
    if signal.direction == "NO_ENTRY" or None in (signal.entry_price, signal.stop_loss, signal.take_profit):
        return "—"
    risk = abs(signal.entry_price - signal.stop_loss)
    reward = abs(signal.take_profit - signal.entry_price)
    if not risk:
        return "—"
    ratio = float(reward / risk)
    return f"{ratio:.2f}:1" if math.isfinite(ratio) else "—"


def format_instrument_message(instrument: dict[str, Any], results: list[tuple[str, dict[str, Any], Signal]], send_no_entry: bool = True) -> str | None:
    included = [(key, strategy, signal) for key, strategy, signal in results
                if send_no_entry or signal.direction != "NO_ENTRY"]
    if not included:
        return None
    sections = [f"Trading signal analysis — {instrument['name']} ({instrument['symbol']})"]
    for _key, strategy, signal in included:
        sections.extend([
            "",
            f"{strategy.get('name', _key)}: {signal.direction} | Confidence: {signal.confidence}",
            f"Entry: {_decimal_text(signal.entry_price)} | Stop loss: {_decimal_text(signal.stop_loss)} | Take profit: {_decimal_text(signal.take_profit)} | R:R: {_rr(signal)}",
            f"Technical basis: {signal.technical_justification}",
            f"Summary: {signal.narrative}",
        ])
    sections.extend(["", "AI-generated chart analysis only; not financial advice. Verify before acting."])
    return "\n".join(sections)

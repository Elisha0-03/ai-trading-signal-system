"""Google Gemini Vision REST client with conservative retries and request pacing."""
from __future__ import annotations

import base64
import logging
import mimetypes
import threading
import time
from pathlib import Path
from typing import Any

import requests

log = logging.getLogger(__name__)


class GeminiQuotaError(RuntimeError):
    """Quota/rate limit: stop this cycle without retries or model fallback."""


class GeminiRequestBudgetError(RuntimeError):
    """The cycle has already attempted its maximum allowed requests."""


class RateGate:
    def __init__(self, requests_per_minute: int):
        if requests_per_minute <= 0:
            raise ValueError("requests_per_minute must be positive")
        self.interval = 60.0 / requests_per_minute
        self.lock = threading.Lock()
        self.next_allowed = 0.0

    def wait(self) -> None:
        with self.lock:
            now = time.monotonic()
            wait_for = max(0.0, self.next_allowed - now)
            self.next_allowed = max(now, self.next_allowed) + self.interval
        if wait_for:
            time.sleep(wait_for)


class GeminiClient:
    def __init__(self, api_key: str, config: dict[str, Any]):
        if not api_key:
            raise ValueError("GEMINI_API_KEY is required")
        self.api_key = api_key
        self.config = config
        self.model = str(config.get("model", "gemini-3.8-flash"))
        self.base = str(config.get("api_base", "https://generativelanguage.googleapis.com/v1beta")).rstrip("/")
        self.timeout = int(config.get("timeout_seconds", 90))
        self.max_retries = int(config.get("max_retries", 0))
        self.max_requests_per_cycle = int(config.get("max_requests_per_cycle", 5))
        self.requests_attempted = 0
        self.retry_base = float(config.get("retry_base_seconds", 2))
        self.gate = RateGate(int(config.get("rate_limit_per_minute", 4)))
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json", "x-goog-api-key": api_key})

    def analyze(self, prompt: str, symbol: str, instrument_name: str, images: dict[str, Path]) -> str:
        parts: list[dict[str, Any]] = [{"text": (
            f"Analyze instrument {instrument_name} ({symbol}) using the labeled multi-timeframe images below. "
            "Image order follows the timeframe labels. Return only the JSON object requested by the strategy prompt."
        )}]
        for timeframe, path in images.items():
            mime_type = mimetypes.guess_type(path.name)[0] or "image/png"
            encoded = base64.b64encode(path.read_bytes()).decode("ascii")
            parts.append({"text": f"Timeframe: {timeframe}"})
            parts.append({"inlineData": {"mimeType": mime_type, "data": encoded}})
        body = {
            "contents": [{"role": "user", "parts": [{"text": prompt}, *parts]}],
            "generationConfig": {
                "temperature": float(self.config.get("temperature", 0.1)),
                "responseMimeType": "application/json",
            },
        }
        url = f"{self.base}/models/{self.model}:generateContent"
        for attempt in range(self.max_retries + 1):
            if self.requests_attempted >= self.max_requests_per_cycle:
                raise GeminiRequestBudgetError("Per-cycle Gemini request cap reached; no further requests permitted")
            self.gate.wait()
            try:
                # Count BEFORE posting: failed/ambiguous attempts also consume the budget.
                self.requests_attempted += 1
                response = self.session.post(url, json=body, timeout=self.timeout)
                if response.status_code == 429:
                    raise GeminiQuotaError("Gemini HTTP 429: stopping cycle; no retry or paid fallback")
                if response.status_code >= 500:
                    if attempt < self.max_retries:
                        retry_after = response.headers.get("Retry-After")
                        delay = float(retry_after) if retry_after and retry_after.isdigit() else self.retry_base * (2 ** attempt)
                        log.warning("Gemini returned HTTP %s; retrying after %.1fs", response.status_code, delay)
                        time.sleep(min(delay, 60.0))
                        continue
                response.raise_for_status()
                data = response.json()
                candidates = data.get("candidates") or []
                if not candidates:
                    raise RuntimeError("Gemini response contained no candidates")
                response_parts = candidates[0].get("content", {}).get("parts", [])
                text = "\n".join(part.get("text", "") for part in response_parts if part.get("text"))
                if not text.strip():
                    raise RuntimeError("Gemini response contained no text")
                return text.strip()
            except (requests.Timeout, requests.ConnectionError) as exc:
                if attempt >= self.max_retries:
                    raise RuntimeError(f"Gemini request failed after retries: {type(exc).__name__}") from exc
                delay = min(self.retry_base * (2 ** attempt), 60.0)
                log.warning("Gemini network error (%s); retrying after %.1fs", type(exc).__name__, delay)
                time.sleep(delay)
            except requests.HTTPError as exc:
                status = exc.response.status_code if exc.response is not None else "unknown"
                # Do not log request headers, URLs containing credentials, or the API key.
                detail = exc.response.text[:500] if exc.response is not None else ""
                raise RuntimeError(f"Gemini returned HTTP {status}: {detail}") from exc
        raise RuntimeError("Gemini request exhausted retries")

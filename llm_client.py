"""Gemini 呼叫封裝：結構化輸出、重試、token 與成本追蹤。"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import asdict, dataclass
from typing import Any


# 2026-07-20 Gemini Developer API Standard 價格，單位 USD / 1M tokens。
# 未列出的模型仍會追蹤 token，只是不顯示成本。
MODEL_PRICING = {
    "gemini-3.1-flash-lite": {"input": 0.25, "output": 1.50, "cached": 0.025},
    "gemini-3.5-flash": {"input": 1.50, "output": 9.00, "cached": 0.15},
    "gemini-3-flash-preview": {"input": 0.50, "output": 3.00, "cached": 0.05},
    "gemini-3.1-pro-preview": {"input": 2.00, "output": 12.00, "cached": 0.20},
    "gemini-2.5-flash": {"input": 0.30, "output": 2.50, "cached": 0.03},
    "gemini-2.5-pro": {"input": 1.25, "output": 10.00, "cached": 0.125},
}


@dataclass
class UsageRecord:
    stage: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    thinking_tokens: int = 0
    cached_tokens: int = 0
    total_tokens: int = 0
    estimated_usd: float = 0.0
    local_cache_hit: bool = False


def _read_value(value: Any, *names: str) -> int:
    for name in names:
        if isinstance(value, dict) and value.get(name) is not None:
            return int(value[name] or 0)
        found = getattr(value, name, None)
        if found is not None:
            return int(found or 0)
    return 0


class UsageLedger:
    def __init__(self) -> None:
        self._records: list[UsageRecord] = []
        self._lock = threading.Lock()

    def record(self, stage: str, model: str, usage: Any) -> None:
        input_tokens = _read_value(
            usage, "prompt_token_count", "input_token_count", "total_input_tokens"
        )
        output_tokens = _read_value(
            usage, "candidates_token_count", "output_token_count", "total_output_tokens"
        )
        thinking_tokens = _read_value(
            usage, "thoughts_token_count", "thinking_token_count", "total_thought_tokens"
        )
        cached_tokens = _read_value(
            usage, "cached_content_token_count", "cached_token_count", "total_cached_tokens"
        )
        total_tokens = _read_value(usage, "total_token_count", "total_tokens")
        if not total_tokens:
            total_tokens = input_tokens + output_tokens + thinking_tokens
        pricing = MODEL_PRICING.get(model, {})
        regular_input = max(input_tokens - cached_tokens, 0)
        estimated_usd = (
            regular_input * float(pricing.get("input", 0))
            + cached_tokens * float(pricing.get("cached", pricing.get("input", 0)))
            + (output_tokens + thinking_tokens) * float(pricing.get("output", 0))
        ) / 1_000_000
        record = UsageRecord(
            stage=stage,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            thinking_tokens=thinking_tokens,
            cached_tokens=cached_tokens,
            total_tokens=total_tokens,
            estimated_usd=estimated_usd,
        )
        with self._lock:
            self._records.append(record)

    def local_cache(self, stage: str, model: str) -> None:
        with self._lock:
            self._records.append(
                UsageRecord(stage=stage, model=model, local_cache_hit=True)
            )

    def records(self) -> list[dict[str, Any]]:
        with self._lock:
            return [asdict(record) for record in self._records]

    def summary(self, usd_to_twd: float = 32.21) -> dict[str, Any]:
        records = self.records()
        estimated_usd = sum(float(record["estimated_usd"]) for record in records)
        return {
            "input_tokens": sum(int(record["input_tokens"]) for record in records),
            "output_tokens": sum(int(record["output_tokens"]) for record in records),
            "thinking_tokens": sum(int(record["thinking_tokens"]) for record in records),
            "cached_tokens": sum(int(record["cached_tokens"]) for record in records),
            "total_tokens": sum(int(record["total_tokens"]) for record in records),
            "local_cache_hits": sum(bool(record["local_cache_hit"]) for record in records),
            "estimated_usd": round(estimated_usd, 6),
            "estimated_twd": round(estimated_usd * usd_to_twd, 2),
            "records": records,
            "pricing_date": "2026-07-20",
        }


class GeminiClient:
    def __init__(self, api_key: str, ledger: UsageLedger) -> None:
        try:
            from google import genai
            from google.genai import types
        except ImportError as exc:  # pragma: no cover - 只在依賴缺漏時發生
            raise RuntimeError("缺少 google-genai，請先執行 pip install -r requirements.txt") from exc
        self._types = types
        self._client = genai.Client(api_key=api_key)
        self._ledger = ledger

    def _config(
        self,
        *,
        max_output_tokens: int,
        temperature: float,
        thinking_level: str | None,
        schema: dict[str, Any] | None,
        media_resolution_low: bool,
    ) -> Any:
        kwargs: dict[str, Any] = {
            "max_output_tokens": max_output_tokens,
            "temperature": temperature,
        }
        if schema:
            kwargs.update(response_mime_type="application/json", response_schema=schema)
        if thinking_level:
            kwargs["thinking_config"] = self._types.ThinkingConfig(
                thinking_level=thinking_level
            )
        if media_resolution_low:
            kwargs["media_resolution"] = "MEDIA_RESOLUTION_LOW"
        return self._types.GenerateContentConfig(**kwargs)

    def _generate(
        self,
        *,
        stage: str,
        model: str,
        contents: Any,
        schema: dict[str, Any] | None,
        max_output_tokens: int,
        temperature: float,
        thinking_level: str | None,
        media_resolution_low: bool = False,
    ) -> Any:
        last_error: Exception | None = None
        active_thinking = thinking_level
        active_resolution = media_resolution_low
        for attempt in range(3):
            try:
                response = self._client.models.generate_content(
                    model=model,
                    contents=contents,
                    config=self._config(
                        max_output_tokens=max_output_tokens,
                        temperature=temperature,
                        thinking_level=active_thinking,
                        schema=schema,
                        media_resolution_low=active_resolution,
                    ),
                )
                self._ledger.record(stage, model, getattr(response, "usage_metadata", {}))
                return response
            except Exception as exc:  # API SDK 會丟不同型別的例外
                last_error = exc
                message = str(exc).lower()
                if "thinking" in message:
                    active_thinking = None
                if "media_resolution" in message or "media resolution" in message:
                    active_resolution = False
                if attempt < 2:
                    time.sleep(1.5 * (attempt + 1))
        raise RuntimeError(f"Gemini 呼叫失敗（{stage}）：{last_error}") from last_error

    def generate_json(
        self,
        *,
        stage: str,
        model: str,
        prompt: str | None = None,
        contents: Any | None = None,
        schema: dict[str, Any],
        max_output_tokens: int,
        thinking_level: str = "low",
        temperature: float = 0.2,
        media_resolution_low: bool = False,
    ) -> dict[str, Any]:
        response = self._generate(
            stage=stage,
            model=model,
            contents=contents if contents is not None else (prompt or ""),
            schema=schema,
            max_output_tokens=max_output_tokens,
            temperature=temperature,
            thinking_level=thinking_level,
            media_resolution_low=media_resolution_low,
        )
        parsed = getattr(response, "parsed", None)
        if parsed is not None:
            if isinstance(parsed, dict):
                return parsed
            if hasattr(parsed, "model_dump"):
                return parsed.model_dump()
        text = (getattr(response, "text", "") or "").strip()
        try:
            value = json.loads(text)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Gemini 未回傳合法 JSON（{stage}）") from exc
        if not isinstance(value, dict):
            raise RuntimeError(f"Gemini JSON 頂層不是物件（{stage}）")
        return value

    def count_tokens(self, model: str, contents: Any) -> int:
        response = self._client.models.count_tokens(model=model, contents=contents)
        return _read_value(response, "total_tokens", "total_token_count")

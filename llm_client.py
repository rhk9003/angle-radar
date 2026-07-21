"""Gemini 呼叫封裝：結構化輸出、重試、token 與成本追蹤。"""

from __future__ import annotations

import json
import re
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


def _parse_json_object(text: str) -> dict[str, Any]:
    """接受純 JSON、程式碼圍欄或 JSON 後方的少量說明；拒絕截斷物件。"""
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, count=1, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned, count=1)
    start = cleaned.find("{")
    if start < 0:
        raise json.JSONDecodeError("missing JSON object", cleaned, 0)
    value, _end = json.JSONDecoder().raw_decode(cleaned[start:])
    if not isinstance(value, dict):
        raise TypeError("JSON top level is not an object")
    return value


def _finish_reason(response: Any) -> str:
    candidates = getattr(response, "candidates", None) or []
    if not candidates:
        return "unknown"
    reason = getattr(candidates[0], "finish_reason", None)
    return str(getattr(reason, "name", None) or reason or "unknown")


def _append_json_retry_instruction(contents: Any) -> Any:
    instruction = (
        "上一次回覆無法完整解析。請縮短每個文字欄位，嚴格依 schema 輸出完整 JSON；"
        "只能輸出 JSON，並優先確保所有括號閉合。"
    )
    if isinstance(contents, str):
        return f"{contents}\n\n{instruction}"
    if isinstance(contents, list):
        return [*contents, {"text": instruction}]
    return [contents, instruction]


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
            usage,
            "thoughts_token_count",
            "thinking_token_count",
            "total_thought_tokens",
        )
        cached_tokens = _read_value(
            usage,
            "cached_content_token_count",
            "cached_token_count",
            "total_cached_tokens",
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
            "thinking_tokens": sum(
                int(record["thinking_tokens"]) for record in records
            ),
            "cached_tokens": sum(int(record["cached_tokens"]) for record in records),
            "total_tokens": sum(int(record["total_tokens"]) for record in records),
            "local_cache_hits": sum(
                bool(record["local_cache_hit"]) for record in records
            ),
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
            raise RuntimeError(
                "缺少 google-genai，請先執行 pip install -r requirements.txt"
            ) from exc
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
                self._ledger.record(
                    stage, model, getattr(response, "usage_metadata", {})
                )
                return response
            except Exception as exc:  # API SDK 會丟不同型別的例外
                last_error = exc
                message = str(exc).lower()
                changed_config = False
                if "thinking" in message:
                    active_thinking = None
                    changed_config = True
                if "media_resolution" in message or "media resolution" in message:
                    active_resolution = False
                    changed_config = True
                if (
                    "invalid_argument" in message or "invalid argument" in message
                ) and active_thinking:
                    # 新模型偶爾會將不相容的 thinking 組合只回報通用 400。
                    # 先以相同內容、不附 thinking config 重試一次。
                    active_thinking = None
                    changed_config = True
                if (
                    "invalid_argument" in message or "invalid argument" in message
                ) and not changed_config:
                    break
                if attempt < 2:
                    if not changed_config:
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
        thinking_level: str | None = "low",
        temperature: float = 0.2,
        media_resolution_low: bool = False,
    ) -> dict[str, Any]:
        active_contents = contents if contents is not None else (prompt or "")
        active_max_tokens = max_output_tokens
        active_thinking = thinking_level
        last_error: Exception | None = None
        last_finish_reason = "unknown"
        for json_attempt in range(2):
            response = self._generate(
                stage=stage if json_attempt == 0 else f"{stage}（JSON 重試）",
                model=model,
                contents=active_contents,
                schema=schema,
                max_output_tokens=active_max_tokens,
                temperature=temperature,
                thinking_level=active_thinking,
                media_resolution_low=media_resolution_low,
            )
            parsed = getattr(response, "parsed", None)
            try:
                if isinstance(parsed, dict):
                    return parsed
                if parsed is not None and hasattr(parsed, "model_dump"):
                    parsed_value = parsed.model_dump()
                    if isinstance(parsed_value, dict):
                        return parsed_value
                return _parse_json_object(getattr(response, "text", "") or "")
            except (json.JSONDecodeError, TypeError) as exc:
                last_error = exc
                last_finish_reason = _finish_reason(response)
                if json_attempt == 0:
                    active_contents = _append_json_retry_instruction(active_contents)
                    active_max_tokens = min(
                        max(active_max_tokens + 2_048, int(active_max_tokens * 1.5)),
                        12_000,
                    )
                    active_thinking = "low"

        raise RuntimeError(
            f"Gemini 未回傳合法 JSON（{stage}；結束原因：{last_finish_reason}）"
        ) from last_error

    def count_tokens(self, model: str, contents: Any) -> int:
        response = self._client.models.count_tokens(model=model, contents=contents)
        return _read_value(response, "total_tokens", "total_token_count")

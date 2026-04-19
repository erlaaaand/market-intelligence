from __future__ import annotations

import json
import logging
from typing import Any, Final

import httpx

from src.core.entities import MarketAnalysisReport, RawTrendData
from src.core.exceptions import LLMAnalysisError
from src.core.ports import LLMPort
from src.infrastructure.llm.prompts import SYSTEM_PROMPT, build_user_message
from src.infrastructure.llm.parser import parse_and_validate

logger = logging.getLogger(__name__)


class OllamaLLMAdapter(LLMPort):
    _CHAT_PATH: Final[str] = "/api/chat"

    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        model: str = "qwen3:30b",
        timeout: float = 120.0,
        retries: int = 2,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout = timeout
        self._retries = retries

    # ── Public interface ──────────────────────────────────────────────

    def analyze_trends(
        self,
        raw_data: list[RawTrendData],
        region: str,
        analysis_date: str,
    ) -> MarketAnalysisReport:
        user_msg = build_user_message(raw_data, region, analysis_date)
        url = f"{self._base_url}{self._CHAT_PATH}"
        last_exc: Exception | None = None

        for attempt in range(1, self._retries + 1):
            logger.info(
                "Ollama call  model='%s'  region='%s'  attempt=%d/%d",
                self._model, region, attempt, self._retries,
            )
            try:
                raw_json = self._call_ollama(url, user_msg)
                return parse_and_validate(raw_json, self._model, region, analysis_date)
            except LLMAnalysisError:
                raise
            except Exception as exc:
                last_exc = exc
                logger.warning(
                    "Ollama attempt %d/%d failed (%s: %s). %s",
                    attempt, self._retries,
                    type(exc).__name__, exc,
                    "Retrying..." if attempt < self._retries else "Giving up.",
                )

        raise LLMAnalysisError(
            model=self._model,
            reason=f"All {self._retries} attempts failed. The model did not respond in time.",
        )

    # ── HTTP streaming ────────────────────────────────────────────────

    def _call_ollama(self, url: str, user_message: str) -> str:
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
            ],
            "stream": True,
            "format": "json",
            "options": {
                "temperature": 0.1,
                "num_predict": 4096,
            },
        }

        connect_timeout = 15.0
        read_timeout = self._timeout

        try:
            chunks: list[str] = []
            with httpx.Client(
                timeout=httpx.Timeout(
                    connect=connect_timeout,
                    read=read_timeout,
                    write=15.0,
                    pool=5.0,
                )
            ) as client:
                with client.stream("POST", url, json=payload) as resp:
                    if resp.status_code >= 400:
                        body_text = resp.read().decode("utf-8", errors="replace")
                        raise LLMAnalysisError(
                            model=self._model,
                            reason=f"Ollama returned HTTP {resp.status_code}: {body_text[:300]}",
                        )
                    for line in resp.iter_lines():
                        if not line.strip():
                            continue
                        try:
                            chunk: dict[str, Any] = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        token = (
                            chunk.get("message", {}).get("content", "")
                            or chunk.get("response", "")
                        )
                        if token:
                            chunks.append(token)
                        if chunk.get("done"):
                            break

        except LLMAnalysisError:
            raise
        except httpx.ConnectError:
            raise LLMAnalysisError(
                model=self._model,
                reason=(
                    f"Cannot connect to Ollama at '{self._base_url}'. "
                    "Make sure Ollama is running."
                ),
            ) from None
        except httpx.TimeoutException:
            raise LLMAnalysisError(
                model=self._model,
                reason=(
                    f"Ollama connection timed out after {read_timeout}s. "
                    "Try increasing OLLAMA_TIMEOUT in .env."
                ),
            ) from None
        except httpx.HTTPError:
            raise LLMAnalysisError(
                model=self._model,
                reason="Ollama HTTP error. Check that the Ollama server is reachable.",
            ) from None

        content = "".join(chunks)
        if not content:
            raise LLMAnalysisError(
                model=self._model,
                reason=(
                    "Ollama returned an empty response. "
                    "The model may have failed to generate output."
                ),
            )
        return content
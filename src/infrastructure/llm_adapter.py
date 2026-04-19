from __future__ import annotations

# src/infrastructure/llm_adapter.py

"""
LLM adapter implementations.

Provided adapters
─────────────────
OllamaLLMAdapter   Production adapter — calls a local Ollama instance.
                   Default model: qwen3:30b (configurable).
                   Uses the Ollama /api/chat endpoint with format="json"
                   to guarantee structured JSON output.

MockLLMAdapter     Deterministic stub for tests and offline development.
                   Generates realistic-looking analytics from raw data
                   without any network calls.

Both implement :class:`src.core.ports.LLMPort` and raise only
:class:`src.core.exceptions.LLMAnalysisError` on unrecoverable failures.

Ollama API contract
───────────────────
POST http://{host}/api/chat
{
  "model": "<model>",
  "messages": [{"role":"system","content":"..."}, {"role":"user","content":"..."}],
  "stream": false,
  "format": "json",
  "options": {"temperature": 0.1, "num_predict": 4096}
}
Response → {"message": {"content": "{...json...}"}, "done": true}
"""

from __future__ import annotations

import json
import logging
import random
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Final

import httpx
from pydantic import ValidationError

from src.core.entities import (
    Anomaly,
    LifecycleStage,
    MarketAnalysisReport,
    RawTrendData,
    ReportMetadata,
    TrendAnalysisDetail,
    TrendMetrics,
    TrendTopic,
)
from src.core.exceptions import LLMAnalysisError
from src.core.ports import LLMPort

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Embedded "Ultimate Prompt"
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT: Final[str] = """
[SYSTEM ROLE]
Anda adalah Advanced Market Intelligence Agent. Tugas utama Anda adalah mengekstraksi, menganalisis secara mendalam, dan merestrukturisasi data tren pasar mentah menjadi format JSON yang sangat terstruktur, komprehensif, dan siap diproses oleh model analitik Large Language Model tingkat lanjut.

[STRICT OUTPUT RULES]
• Balas HANYA dengan satu objek JSON yang valid. Tidak ada teks penjelas, tidak ada markdown, tidak ada komentar.
• Semua nilai string harus dalam Bahasa Inggris, ringkas, dan bermakna.
• Setiap elemen "market_trends" WAJIB memiliki semua field yang ditentukan dalam skema.
• "lifecycle_stage" HARUS salah satu dari: "Emerging", "Trending", "Peak", "Stagnant", "Declining".
• "momentum_score" dan "volatility_index" adalah angka float antara 0.0 dan 100.0.
• "key_drivers" harus berupa array dengan minimal satu elemen string.
• "anomalies_detected" boleh berupa array kosong jika tidak ada anomali.
• "trend_id" HARUS unik di dalam satu laporan.

[EXPECTED JSON SCHEMA]
{
  "metadata": {
    "region": "<REGION_CODE>",
    "date": "<YYYY-MM-DD>",
    "processed_at": "<ISO-8601-TIMESTAMP>"
  },
  "market_trends": [
    {
      "trend_id": "<UNIQUE_IDENTIFIER>",
      "topic": "<NAMA_TREN>",
      "metrics": {
        "momentum_score": <FLOAT_0_TO_100>,
        "volatility_index": <FLOAT_0_TO_100>
      },
      "analysis": {
        "lifecycle_stage": "<Emerging | Trending | Peak | Stagnant | Declining>",
        "key_drivers": ["<FAKTOR_1>", "<FAKTOR_2>"],
        "potential_impact": "<DESKRIPSI_MENDALAM>"
      },
      "anomalies_detected": [
        {
          "type": "<JENIS_ANOMALI>",
          "description": "<PENJELASAN_ANOMALI>"
        }
      ]
    }
  ]
}
""".strip()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _strip_thinking_tags(text: str) -> str:
    """
    Strip ``<think>…</think>`` blocks that qwen3 thinking models may emit
    before the actual JSON payload, even when ``format=json`` is set.
    """
    cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    return cleaned.strip()


def _extract_json_object(text: str) -> str:
    """
    Extract the first ``{ … }`` block from *text*.

    Used as a last-resort fallback when the model emits extra prose around
    the JSON object despite ``format=json``.
    """
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return text
    return text[start : end + 1]


def _raw_records_to_user_message(
    raw_data: list[RawTrendData],
    region: str,
    analysis_date: str,
) -> str:
    """
    Format raw trending records into a concise user message for the LLM.

    Keeps the payload small by only including the fields the model needs.
    """
    compact = [
        {
            "rank": i + 1,
            "keyword": r.keyword,
            "relative_interest": r.raw_value,   # 0–100
            "source": r.source,
        }
        for i, r in enumerate(raw_data)
    ]
    return (
        f"Analyse the following {len(raw_data)} trending search keyword(s) "
        f"for region '{region}' on {analysis_date}.\n\n"
        f"Raw data (ranked by relative interest):\n"
        f"{json.dumps(compact, indent=2)}\n\n"
        "Return ONLY the JSON object following the schema in your system instructions. "
        "No preamble, no explanation, no markdown fences."
    )


# ---------------------------------------------------------------------------
# OllamaLLMAdapter
# ---------------------------------------------------------------------------

class OllamaLLMAdapter(LLMPort):
    """
    Production LLM adapter that calls a locally running Ollama instance.

    Args:
        base_url:  Ollama server base URL (default: http://localhost:11434).
        model:     Ollama model tag (default: qwen3:30b).
        timeout:   HTTP timeout in seconds for the /api/chat call.
        retries:   Number of parse/validation retry attempts.
    """

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

    # ------------------------------------------------------------------
    # LLMPort
    # ------------------------------------------------------------------

    def analyze_trends(
        self,
        raw_data: list[RawTrendData],
        region: str,
        analysis_date: str,
    ) -> MarketAnalysisReport:
        user_msg = _raw_records_to_user_message(raw_data, region, analysis_date)
        url = f"{self._base_url}{self._CHAT_PATH}"

        last_exc: Exception | None = None

        for attempt in range(1, self._retries + 1):
            logger.info(
                "Ollama call  model='%s'  region='%s'  attempt=%d/%d",
                self._model,
                region,
                attempt,
                self._retries,
            )
            try:
                raw_json = self._call_ollama(url, user_msg)
                return self._parse_and_validate(raw_json, region, analysis_date)
            except LLMAnalysisError:
                raise   # hard failures are not retried
            except Exception as exc:
                last_exc = exc
                logger.warning(
                    "Ollama attempt %d/%d failed (%s: %s). %s",
                    attempt,
                    self._retries,
                    type(exc).__name__,
                    exc,
                    "Retrying…" if attempt < self._retries else "Giving up.",
                )

        raise LLMAnalysisError(
            model=self._model,
            reason=f"All {self._retries} attempts failed. Last error: {last_exc}",
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _call_ollama(self, url: str, user_message: str) -> str:
        """POST to Ollama /api/chat and return the assistant's content string."""
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
            ],
            "stream": False,
            "format": "json",          # forces structured JSON output
            "options": {
                "temperature": 0.1,    # low temperature = more deterministic
                "num_predict": 4096,
            },
        }

        try:
            with httpx.Client(timeout=self._timeout) as client:
                resp = client.post(url, json=payload)
        except httpx.ConnectError as exc:
            raise LLMAnalysisError(
                model=self._model,
                reason=f"Cannot connect to Ollama at '{self._base_url}': {exc}",
            ) from exc
        except httpx.TimeoutException as exc:
            raise LLMAnalysisError(
                model=self._model,
                reason=f"Ollama request timed out after {self._timeout}s: {exc}",
            ) from exc
        except httpx.HTTPError as exc:
            raise LLMAnalysisError(
                model=self._model,
                reason=f"Ollama HTTP error: {exc}",
            ) from exc

        if resp.status_code >= 400:
            raise LLMAnalysisError(
                model=self._model,
                reason=f"Ollama returned HTTP {resp.status_code}: {resp.text[:300]}",
            )

        try:
            body: dict[str, Any] = resp.json()
        except json.JSONDecodeError as exc:
            raise LLMAnalysisError(
                model=self._model,
                reason=f"Ollama response is not valid JSON: {exc}",
            ) from exc

        content: str = (
            body.get("message", {}).get("content", "")
            or body.get("response", "")   # fallback for /api/generate shape
        )
        if not content:
            raise LLMAnalysisError(
                model=self._model,
                reason="Ollama response contains no content field.",
            )

        return content

    def _parse_and_validate(
        self, content: str, region: str, analysis_date: str
    ) -> MarketAnalysisReport:
        """
        Parse *content* (raw LLM string) into a validated ``MarketAnalysisReport``.

        Recovery strategy (graceful degradation):
        1. Strip <think>…</think> tags.
        2. Extract the first { … } block.
        3. JSON-parse the block.
        4. Full Pydantic validation → if OK, return.
        5. If validation fails, attempt per-item recovery:
           - validate each trend individually, drop invalid ones.
           - inject the correct metadata from our call context.
           - if ≥1 trend survives, return the partial report.
        6. Zero trends surviving → raise LLMAnalysisError.
        """
        cleaned = _strip_thinking_tags(content)
        json_str = _extract_json_object(cleaned)

        try:
            raw_obj: dict[str, Any] = json.loads(json_str)
        except json.JSONDecodeError as exc:
            raise LLMAnalysisError(
                model=self._model,
                reason=f"LLM output is not parseable JSON: {exc}. "
                       f"First 300 chars: {json_str[:300]}",
            ) from exc

        # Ensure metadata is consistent with our call context (LLM may
        # hallucinate wrong region or date).
        raw_obj.setdefault("metadata", {})
        raw_obj["metadata"]["region"] = region
        raw_obj["metadata"]["date"] = analysis_date
        raw_obj["metadata"].setdefault(
            "processed_at",
            datetime.now(tz=timezone.utc).isoformat(),
        )

        # Attempt full validation first
        try:
            return MarketAnalysisReport.model_validate(raw_obj)
        except ValidationError as full_err:
            logger.warning(
                "Full MarketAnalysisReport validation failed (%d error(s)). "
                "Attempting per-item recovery.",
                full_err.error_count(),
            )

        # Per-item recovery
        valid_trends: list[TrendTopic] = []
        for item in raw_obj.get("market_trends", []):
            if not isinstance(item, dict):
                continue
            try:
                valid_trends.append(TrendTopic.model_validate(item))
            except ValidationError as item_err:
                logger.warning(
                    "Dropping invalid trend item (topic=%r): %d error(s).",
                    item.get("topic", "<unknown>"),
                    item_err.error_count(),
                )

        if not valid_trends:
            raise LLMAnalysisError(
                model=self._model,
                reason=(
                    "LLM output failed validation and no individual trends "
                    "could be recovered."
                ),
            )

        logger.info(
            "Partial recovery: %d valid trend(s) salvaged.", len(valid_trends)
        )
        return MarketAnalysisReport(
            metadata=ReportMetadata(
                region=region,
                date=analysis_date,
            ),
            market_trends=valid_trends,
        )


# ---------------------------------------------------------------------------
# MockLLMAdapter  (deterministic stub — no network calls)
# ---------------------------------------------------------------------------

_MOCK_DRIVERS: Final[list[list[str]]] = [
    ["Social media virality", "Influencer amplification"],
    ["Breaking news cycle", "Public curiosity spike"],
    ["Seasonal demand pattern", "Consumer habit shift"],
    ["Viral challenge / meme spread", "Platform algorithm boost"],
    ["Economic uncertainty", "Cost-of-living search behaviour"],
    ["Technology product launch", "Media coverage surge"],
    ["Political or regulatory event", "Investor sentiment shift"],
    ["Sports or entertainment event", "Celebrity association"],
]

_MOCK_IMPACTS: Final[list[str]] = [
    "High short-term content opportunity; saturation likely within 7–14 days.",
    "Sustained audience interest expected; brand awareness campaigns recommended.",
    "Niche but loyal audience segment; long-form content will outperform shorts.",
    "Broad mainstream appeal; high competition from established publishers.",
    "Regional spike with limited global transfer; localise content strategy.",
    "Evergreen potential if paired with actionable how-to or educational framing.",
    "Controversy-adjacent; engagement high but reputational risk must be managed.",
]

_MOCK_ANOMALY_POOL: Final[list[dict[str, str]]] = [
    {
        "type": "Sudden Volume Spike",
        "description": "Search volume increased >3× within a 24-hour window, "
                       "suggesting a single triggering event.",
    },
    {
        "type": "Geographic Concentration",
        "description": "Interest is disproportionately concentrated in one "
                       "sub-region rather than distributed nationally.",
    },
    {
        "type": "Keyword Co-occurrence Anomaly",
        "description": "Topic frequently co-occurs with semantically unrelated "
                       "keywords, indicating possible misspelling or double meaning.",
    },
    {
        "type": "Cyclical Pattern Deviation",
        "description": "Trend appears outside its typical seasonal window, "
                       "indicating exogenous demand rather than habitual search.",
    },
]


class MockLLMAdapter(LLMPort):
    """
    Deterministic mock LLM adapter for offline development and unit tests.

    Generates realistic-looking analytics derived from the raw input values
    so assertions on relative ordering are stable across runs.

    Args:
        inject_anomaly_probability: Float 0–1 controlling how often an anomaly
                                    is injected per trend. Default 0.3 (30%).
    """

    def __init__(self, inject_anomaly_probability: float = 0.3) -> None:
        if not 0.0 <= inject_anomaly_probability <= 1.0:
            raise ValueError("inject_anomaly_probability must be in [0, 1]")
        self._anomaly_prob = inject_anomaly_probability

    def analyze_trends(
        self,
        raw_data: list[RawTrendData],
        region: str,
        analysis_date: str,
    ) -> MarketAnalysisReport:
        logger.info(
            "MockLLMAdapter.analyze_trends  region='%s'  records=%d",
            region,
            len(raw_data),
        )

        trends: list[TrendTopic] = [
            self._build_mock_trend(record, i, region, analysis_date)
            for i, record in enumerate(raw_data)
        ]

        return MarketAnalysisReport(
            metadata=ReportMetadata(
                region=region,
                date=analysis_date,
            ),
            market_trends=trends,
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _build_mock_trend(
        self,
        record: RawTrendData,
        index: int,
        region: str,
        analysis_date: str,
    ) -> TrendTopic:
        v = record.raw_value  # 0–100

        # Derive scores deterministically from raw_value
        momentum = min(100.0, round(v * 0.9 + (index % 5) * 2.0, 2))
        volatility = min(100.0, round(100.0 - v * 0.6 + (index % 3) * 5.0, 2))

        lifecycle = self._lifecycle_from_value(v)

        # Deterministic driver selection (stable for same index)
        drivers = _MOCK_DRIVERS[index % len(_MOCK_DRIVERS)]
        impact = _MOCK_IMPACTS[index % len(_MOCK_IMPACTS)]

        anomalies: list[Anomaly] = []
        # Use a seeded RNG so tests are reproducible
        rng = random.Random(f"{region}:{record.keyword}:{analysis_date}")
        if rng.random() < self._anomaly_prob:
            anomaly_dict = _MOCK_ANOMALY_POOL[index % len(_MOCK_ANOMALY_POOL)]
            anomalies.append(Anomaly(**anomaly_dict))

        trend_id = TrendTopic.make_trend_id(region, record.keyword, analysis_date)

        return TrendTopic(
            trend_id=trend_id,
            topic=record.keyword,
            metrics=TrendMetrics(
                momentum_score=momentum,
                volatility_index=volatility,
            ),
            analysis=TrendAnalysisDetail(
                lifecycle_stage=lifecycle,
                key_drivers=list(drivers),
                potential_impact=impact,
            ),
            anomalies_detected=anomalies,
        )

    @staticmethod
    def _lifecycle_from_value(raw_value: int) -> LifecycleStage:
        """
        Map a raw_value score to a lifecycle stage.

        Mapping (deterministic, no randomness):
            80–100 → Peak
            60–79  → Trending
            40–59  → Emerging
            20–39  → Stagnant
            0–19   → Declining
        """
        if raw_value >= 80:
            return LifecycleStage.PEAK
        if raw_value >= 60:
            return LifecycleStage.TRENDING
        if raw_value >= 40:
            return LifecycleStage.EMERGING
        if raw_value >= 20:
            return LifecycleStage.STAGNANT
        return LifecycleStage.DECLINING
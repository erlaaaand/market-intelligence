from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Final

import httpx

from src.core.entities import (
    CreativeDocument,
    CreativeDocumentBatch,
    MarketAnalysisReport,
    RawTrendData,
)
from src.core.exceptions import LLMAnalysisError
from src.core.ports import LLMPort, WebSearchPort
from src.infrastructure.llm.prompts import SYSTEM_PROMPT, build_user_message
from src.infrastructure.llm.parser import parse_and_validate

logger = logging.getLogger(__name__)

_MAX_SEARCH_WORKERS: Final[int] = 5
_SEARCH_RESULTS_PER_KW: Final[int] = 3

# Proses max N keyword per panggilan Ollama agar JSON tidak truncated.
# qwen2.5:7b: gunakan 2-3. Model lebih besar (30b) bisa 5.
_DEFAULT_CHUNK_SIZE: Final[int] = 3


class OllamaLLMAdapter(LLMPort):
    _CHAT_PATH: Final[str] = "/api/chat"

    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        model: str = "qwen3:30b",
        timeout: float = 120.0,
        retries: int = 2,
        web_searcher: WebSearchPort | None = None,
        chunk_size: int = _DEFAULT_CHUNK_SIZE,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout = timeout
        self._retries = retries
        self._web_searcher = web_searcher
        self._chunk_size = max(1, chunk_size)

    # ── Public interface ──────────────────────────────────────────────

    def analyze_trends(
        self,
        raw_data: list[RawTrendData],
        region: str,
        analysis_date: str,
    ) -> MarketAnalysisReport:
        # ── Step 1: RAG — fetch snippets untuk SEMUA keyword sekaligus ─
        # Key di snippets_map = record.keyword (BUKAN query string dengan suffix region)
        # Ini fix untuk bug key-mismatch sebelumnya.
        snippets_map: dict[str, list[dict[str, str]]] = {}
        if self._web_searcher is not None:
            snippets_map = self._fetch_snippets_parallel(raw_data, region)
            logger.info(
                "RAG summary: %d/%d keyword(s) berhasil dapat konteks web.",
                len(snippets_map),
                len(raw_data),
            )
            # Log keyword mana yang TIDAK dapat snippet (untuk debug)
            missing = [r.keyword for r in raw_data if r.keyword not in snippets_map]
            if missing:
                logger.warning(
                    "Keyword TANPA konteks web (akan mengandalkan pengetahuan model): %s",
                    missing,
                )

        # ── Step 2: Pecah ke chunks ───────────────────────────────────
        chunks = [
            raw_data[i: i + self._chunk_size]
            for i in range(0, len(raw_data), self._chunk_size)
        ]
        logger.info(
            "Chunked processing: %d keyword → %d chunk(s) × max %d  [region='%s']",
            len(raw_data), len(chunks), self._chunk_size, region,
        )

        all_documents: list[CreativeDocument] = []
        url = f"{self._base_url}{self._CHAT_PATH}"

        for chunk_idx, chunk in enumerate(chunks, start=1):
            chunk_keywords = [r.keyword for r in chunk]

            # ── KRITIS: ambil snippets dari map menggunakan keyword ASLI ─
            # Ini yang sebelumnya bug: query ke DDGs pakai "keyword + region"
            # tapi lookup ke map juga harus pakai keyword asli (bukan query string).
            chunk_snippets: dict[str, list[dict[str, str]]] = {
                r.keyword: snippets_map[r.keyword]
                for r in chunk
                if r.keyword in snippets_map
            }

            logger.info(
                "Chunk %d/%d  keywords=%s  snippets_available=%d/%d",
                chunk_idx, len(chunks),
                chunk_keywords,
                len(chunk_snippets),
                len(chunk),
            )

            user_msg = build_user_message(
                chunk,
                region,
                analysis_date,
                snippets_map=chunk_snippets,
            )

            # Log ukuran prompt agar bisa detect jika terlalu besar
            logger.info(
                "Prompt chunk %d ukuran: %d karakter",
                chunk_idx, len(user_msg),
            )

            batch = self._call_with_retry(
                url, user_msg, region, analysis_date, chunk_idx
            )
            all_documents.extend(batch.documents)
            logger.info(
                "Chunk %d/%d selesai  documents_this_chunk=%d  total_so_far=%d",
                chunk_idx, len(chunks),
                len(batch.documents),
                len(all_documents),
            )

        logger.info(
            "Semua chunk selesai  total_documents=%d  region='%s'",
            len(all_documents), region,
        )
        return CreativeDocumentBatch(
            region=region,
            date=analysis_date,
            documents=all_documents,
        )

    # ── Retry wrapper per chunk ───────────────────────────────────────

    def _call_with_retry(
        self,
        url: str,
        user_msg: str,
        region: str,
        analysis_date: str,
        chunk_idx: int,
    ) -> CreativeDocumentBatch:
        last_exc: Exception | None = None
        for attempt in range(1, self._retries + 1):
            logger.info(
                "Ollama call  model='%s'  chunk=%d  attempt=%d/%d",
                self._model, chunk_idx, attempt, self._retries,
            )
            try:
                raw_json = self._call_ollama(url, user_msg)
                return parse_and_validate(raw_json, self._model, region, analysis_date)
            except LLMAnalysisError:
                raise
            except Exception as exc:
                last_exc = exc
                logger.warning(
                    "Chunk %d attempt %d/%d gagal (%s: %s). %s",
                    chunk_idx, attempt, self._retries,
                    type(exc).__name__, exc,
                    "Mencoba lagi..." if attempt < self._retries else "Menyerah.",
                )

        raise LLMAnalysisError(
            model=self._model,
            reason=(
                f"Chunk {chunk_idx}: semua {self._retries} percobaan gagal. "
                f"Error terakhir: {last_exc}"
            ),
        )

    # ── RAG: parallel web search ──────────────────────────────────────

    def _fetch_snippets_parallel(
        self,
        raw_data: list[RawTrendData],
        region: str,
    ) -> dict[str, list[dict[str, str]]]:
        """
        Jalankan web search untuk setiap keyword secara paralel.

        PENTING: key di dict hasil = record.keyword (string ASLI),
        bukan query string yang dikirim ke DDGs.
        Query ke DDGs boleh punya konteks tambahan (+ region),
        tapi key penyimpanan HARUS pakai keyword asli agar lookup di chunk benar.
        """
        snippets_map: dict[str, list[dict[str, str]]] = {}

        def _search_one(record: RawTrendData) -> tuple[str, list[dict[str, str]]]:
            # Query lebih spesifik dengan tambahan region & "terbaru"
            query = f"{record.keyword} terbaru {region} 2026"
            logger.info(
                "Memulai web search  keyword='%s'  query='%s'",
                record.keyword, query,
            )
            results = self._web_searcher.search(  # type: ignore[union-attr]
                query, max_results=_SEARCH_RESULTS_PER_KW
            )
            # Return dengan keyword ASLI sebagai key (bukan query)
            return record.keyword, results

        workers = min(_MAX_SEARCH_WORKERS, len(raw_data))
        with ThreadPoolExecutor(
            max_workers=workers, thread_name_prefix="rag_search"
        ) as pool:
            futures = {
                pool.submit(_search_one, record): record.keyword
                for record in raw_data
            }
            for future in as_completed(futures):
                original_keyword = futures[future]
                try:
                    kw, snippets = future.result()
                    if snippets:
                        # kw di sini sudah record.keyword (bukan query)
                        snippets_map[kw] = snippets
                    else:
                        logger.warning(
                            "Keyword '%s' tidak mendapat snippet apapun dari web search.",
                            original_keyword,
                        )
                except Exception as exc:
                    logger.warning(
                        "Web search thread gagal untuk keyword='%s': %s",
                        original_keyword, exc,
                    )

        return snippets_map

    # ── HTTP streaming ke Ollama ──────────────────────────────────────

    def _call_ollama(self, url: str, user_message: str) -> str:
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user",   "content": user_message},
            ],
            "stream": True,
            "format": "json",
            "options": {
                "temperature": 0.1,
                # 3072 cukup untuk 3 dokumen lengkap (~600 token/dok × 3 + buffer)
                "num_predict": 3072,
            },
        }

        try:
            chunks: list[str] = []
            with httpx.Client(
                timeout=httpx.Timeout(
                    connect=15.0,
                    read=self._timeout,
                    write=15.0,
                    pool=5.0,
                )
            ) as client:
                with client.stream("POST", url, json=payload) as resp:
                    if resp.status_code >= 400:
                        body_text = resp.read().decode("utf-8", errors="replace")
                        raise LLMAnalysisError(
                            model=self._model,
                            reason=f"Ollama HTTP {resp.status_code}: {body_text[:300]}",
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
                    f"Tidak bisa konek ke Ollama di '{self._base_url}'. "
                    "Pastikan Ollama sedang berjalan."
                ),
            ) from None
        except httpx.TimeoutException:
            raise LLMAnalysisError(
                model=self._model,
                reason=(
                    f"Ollama timeout setelah {self._timeout}s. "
                    "Coba naikkan OLLAMA_TIMEOUT di .env."
                ),
            ) from None
        except httpx.HTTPError:
            raise LLMAnalysisError(
                model=self._model,
                reason="HTTP error ke Ollama. Periksa server Ollama.",
            ) from None

        content = "".join(chunks)
        if not content:
            raise LLMAnalysisError(
                model=self._model,
                reason="Ollama mengembalikan response kosong.",
            )
        return content
from __future__ import annotations

import logging
from typing import Final

from src.core.ports import WebSearchPort

logger = logging.getLogger(__name__)

_DEFAULT_MAX_RESULTS: Final[int] = 3


def _import_ddgs():
    """Import DDGS dengan fallback ke nama lama. Raise ImportError jika keduanya tidak ada."""
    try:
        from ddgs import DDGS
        return DDGS
    except ImportError:
        pass
    try:
        from duckduckgo_search import DDGS  # type: ignore[no-redef]
        return DDGS
    except ImportError:
        raise ImportError(
            "Library web search tidak ditemukan. "
            "Jalankan: pip install ddgs"
        )


def _call_news(ddgs_instance, query: str, region: str, safesearch: str, max_results: int) -> list:
    """
    Wrapper aman untuk ddgs.news() yang menangani perbedaan API antar versi.

    - ddgs >= 7.x  : news(query, region=..., safesearch=..., max_results=...)
    - duckduckgo_search lama : news(keywords=query, ...)
    """
    # Coba API baru (positional query) terlebih dahulu
    try:
        return list(ddgs_instance.news(query, region=region, safesearch=safesearch, max_results=max_results))
    except TypeError:
        pass

    # Fallback ke API lama (keywords=)
    try:
        return list(ddgs_instance.news(keywords=query, region=region, safesearch=safesearch, max_results=max_results))
    except TypeError:
        pass

    # Fallback minimal tanpa parameter opsional
    return list(ddgs_instance.news(query))


def _call_text(ddgs_instance, query: str, region: str, safesearch: str, max_results: int) -> list:
    """
    Wrapper aman untuk ddgs.text() yang menangani perbedaan API antar versi.
    """
    try:
        return list(ddgs_instance.text(query, region=region, safesearch=safesearch, max_results=max_results))
    except TypeError:
        pass

    try:
        return list(ddgs_instance.text(keywords=query, region=region, safesearch=safesearch, max_results=max_results))
    except TypeError:
        pass

    return list(ddgs_instance.text(query))


class DuckDuckGoSearchAdapter(WebSearchPort):
    """
    Fetch real-time news snippets dari DuckDuckGo.
    Selalu mengembalikan list (tidak pernah raise) agar pipeline tidak crash.
    Kompatibel dengan ddgs >= 7.x dan duckduckgo_search versi lama.

    Install: pip install -U ddgs
    """

    def __init__(self, region: str = "wt-wt", safesearch: str = "moderate") -> None:
        self._region = region
        self._safesearch = safesearch
        self._ddgs_cls = None
        self._api_version: str | None = None  # "new" | "old" | None

    def _get_ddgs_cls(self):
        if self._ddgs_cls is None:
            self._ddgs_cls = _import_ddgs()
        return self._ddgs_cls

    def search(self, query: str, max_results: int = _DEFAULT_MAX_RESULTS) -> list[dict[str, str]]:
        """
        Cari berita real-time. Return [] jika gagal — tidak pernah raise.
        Key yang dikembalikan: 'title', 'body', 'url'.
        """
        try:
            DDGS = self._get_ddgs_cls()
        except ImportError as e:
            logger.warning("Web search dinonaktifkan: %s", e)
            return []

        results: list[dict[str, str]] = []

        # ── Primary: News search ──────────────────────────────────────
        try:
            logger.info("DDGs NEWS search dimulai  query='%s'  max=%d", query, max_results)
            with DDGS() as ddgs:
                hits = _call_news(ddgs, query, self._region, self._safesearch, max_results)

            for hit in hits:
                results.append({
                    "title": str(hit.get("title", "")).strip(),
                    "body":  str(hit.get("body",  "")).strip(),
                    "url":   str(hit.get("url",   "")).strip(),
                })

            if results:
                preview = results[0]
                logger.info(
                    "DDGs NEWS OK  query='%s'  hits=%d  preview='%s...'",
                    query,
                    len(results),
                    (preview["title"] + " — " + preview["body"])[:200],
                )
            else:
                logger.info("DDGs NEWS query='%s' → 0 hasil. Coba text search.", query)

        except Exception as exc:
            logger.warning(
                "DDGs NEWS GAGAL  query='%s'  error=%s: %s — fallback ke text search.",
                query, type(exc).__name__, exc,
            )

        # ── Fallback: Text search (jika news kosong) ──────────────────
        if not results:
            try:
                logger.info("DDGs TEXT search dimulai  query='%s'  max=%d", query, max_results)
                with DDGS() as ddgs:
                    hits = _call_text(ddgs, query, self._region, self._safesearch, max_results)

                for hit in hits:
                    results.append({
                        "title": str(hit.get("title", "")).strip(),
                        "body":  str(hit.get("body",  "")).strip(),
                        "url":   str(hit.get("url",   "")).strip(),
                    })

                if results:
                    preview = results[0]
                    logger.info(
                        "DDGs TEXT OK  query='%s'  hits=%d  preview='%s...'",
                        query,
                        len(results),
                        (preview["title"] + " — " + preview["body"])[:200],
                    )
                else:
                    logger.warning(
                        "DDGs TEXT query='%s' → 0 hasil. Keyword ini tidak punya konteks web.",
                        query,
                    )

            except Exception as exc:
                logger.warning(
                    "DDGs TEXT GAGAL  query='%s'  error=%s: %s",
                    query, type(exc).__name__, exc,
                )

        logger.info(
            "WebSearch selesai  query='%s'  total_snippets=%d",
            query, len(results),
        )
        return results
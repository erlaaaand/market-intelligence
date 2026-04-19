# src/infrastructure/local_storage.py

"""
Local filesystem adapter — concrete implementation of `StoragePort`.

Serialises domain entities to pretty-printed JSON and writes them to
configurable directories. Directories are created on demand.

Write strategy: write to a sibling `.tmp` file first, then atomically
rename to the final path. This prevents partial writes from corrupting
existing files on crash or disk-full errors.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from src.core.entities import TrendTopic
from src.core.exceptions import StorageError
from src.core.ports import StoragePort

logger = logging.getLogger(__name__)


class _ISODateTimeEncoder(json.JSONEncoder):
    """
    Extend the default JSON encoder to serialise `datetime` objects as
    ISO 8601 strings (with timezone offset where available).
    """

    def default(self, obj: Any) -> str | Any:
        if isinstance(obj, datetime):
            return obj.isoformat()
        return super().default(obj)


class LocalStorageAdapter(StoragePort):
    """
    Persists data as pretty-printed JSON files on the local file system.

    Two separate base directories are maintained:
        raw_base_path:       for raw provider payloads.
        processed_base_path: for processed `TrendTopic` entities.

    Both directories (and all intermediate parents) are created
    automatically if they do not already exist.
    """

    def __init__(self, raw_base_path: str, processed_base_path: str) -> None:
        """
        Initialise the adapter.

        Args:
            raw_base_path:       Filesystem path for raw JSON output files.
            processed_base_path: Filesystem path for processed JSON output files.

        Raises:
            StorageError: If the required directories cannot be created.
        """
        self._raw_path = Path(raw_base_path).resolve()
        self._processed_path = Path(processed_base_path).resolve()
        self._ensure_directories()

    # ------------------------------------------------------------------
    # StoragePort implementation
    # ------------------------------------------------------------------

    def save_raw(self, data: dict[str, object], filename: str) -> None:
        """
        Write a raw data dictionary to `<raw_base_path>/<filename>`.

        Args:
            data:     JSON-serialisable dictionary to persist.
            filename: Target filename (e.g. "raw_trends_US_20240601T120000Z.json").

        Raises:
            StorageError: If the write fails for any reason.
        """
        target = self._raw_path / filename
        self._write_json(target, data)
        logger.info("Raw data saved → %s", target)

    def save_processed(self, data: list[TrendTopic], filename: str) -> None:
        """
        Serialise a list of `TrendTopic` entities and write to
        `<processed_base_path>/<filename>`.

        Args:
            data:     List of processed trend entities.
            filename: Target filename.

        Raises:
            StorageError: If the write fails for any reason.
        """
        payload: list[dict[str, object]] = [
            topic.model_dump(mode="json") for topic in data
        ]
        target = self._processed_path / filename
        self._write_json(target, payload)
        logger.info("Processed data saved → %s", target)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _ensure_directories(self) -> None:
        """Create raw and processed directories (including parents) if absent."""
        for directory in (self._raw_path, self._processed_path):
            try:
                directory.mkdir(parents=True, exist_ok=True)
                logger.debug("Directory ensured: %s", directory)
            except OSError as exc:
                raise StorageError(
                    path=str(directory),
                    reason=f"Cannot create directory: {exc}",
                ) from exc

    def _write_json(self, target: Path, payload: object) -> None:
        """
        Serialise *payload* to JSON and write it safely to *target*.

        Uses a `.tmp` sibling file + `Path.replace()` to minimise the risk
        of corrupting an existing file on partial write or crash.

        Args:
            target:  Absolute destination path.
            payload: JSON-serialisable object (dict or list).

        Raises:
            StorageError: On any I/O or serialisation error.
        """
        tmp_target = target.with_suffix(".tmp")
        try:
            serialised = json.dumps(payload, indent=2, cls=_ISODateTimeEncoder)
            tmp_target.write_text(serialised, encoding="utf-8")
            tmp_target.replace(target)
            logger.debug("Written %d bytes → %s", len(serialised), target)
        except (OSError, TypeError, ValueError) as exc:
            # Best-effort cleanup of the temp file.
            if tmp_target.exists():
                try:
                    tmp_target.unlink(missing_ok=True)
                except OSError:
                    pass
            raise StorageError(
                path=str(target),
                reason=str(exc),
            ) from exc